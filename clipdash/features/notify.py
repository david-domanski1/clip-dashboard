"""Notifications: the person never misses a submission deadline or a finished clip.

Pipeline events become one short sentence each and go out on the channels the person turned on:
  desktop  macOS `osascript display notification`, Windows toast (Windows.UI.Notifications, NotifyIcon balloon
           fallback), Linux `notify-send` when installed
  discord  a webhook URL the person pastes (POST JSON {"content": text})
  ntfy     phone push through ntfy.sh: POST https://ntfy.sh/<topic> with the text as body, headers Title and Priority
           (high for deadline events). The topic is the only secret: anyone who knows it can read it, so it is random.

Each event kind has its own on/off setting. Every send runs on a daemon thread with a 10 s timeout and never raises into
the pipeline; results land in an in-memory ring of the last 50 notifications (GET /api/notify/recent). Failures are
throttled to one notification a minute (a render failure fires both clip.failed and job.failed). Settings stay in the
local database like every other setting; nothing is sent anywhere the person did not configure."""
from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from xml.sax.saxutils import escape as xml_escape

import requests
from fastapi import APIRouter

from .. import config, db, errors, events

log = logging.getLogger("clipdash.features.notify")

TITLE = "Clip Dashboard"
TIMEOUT = 10
FAIL_EVERY = 60.0          # seconds between failure notifications
RING_SIZE = 50
CHANNELS = ("desktop", "discord", "ntfy")
NTFY_BASE = "https://ntfy.sh/"
DISCORD_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+/?$")
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PLATFORMS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "twitch": "Twitch", "x": "X"}

# event name → the per-event toggle that controls it
EVENT_TOGGLE = {
    "video.new": "notify_on_new_video",
    "clip.ready": "notify_on_clip_ready",
    "clip.failed": "notify_on_failure",
    "job.failed": "notify_on_failure",
    "post.live": "notify_on_post_live",
    "submission.due": "notify_on_submission_due",
    "submission.missed": "notify_on_submission_due",
}

SETTINGS = {
    "notify_desktop": True, "notify_discord": False, "notify_ntfy": False,
    "notify_discord_webhook": "", "notify_ntfy_topic": "",
    "notify_on_new_video": True, "notify_on_clip_ready": True, "notify_on_failure": True,
    "notify_on_post_live": True, "notify_on_submission_due": True,
}
config.register_settings(SETTINGS)

router = APIRouter()
_recent: deque = deque(maxlen=RING_SIZE)
_ring_lock = threading.Lock()
_fail_lock = threading.Lock()
_last_failure = 0.0
_suggested_topic = "clipdash-" + "".join(secrets.choice(string.ascii_lowercase) for _ in range(10))


def _setting(key: str):
    try:
        return db.get_setting(key, SETTINGS.get(key))
    except Exception:  # noqa: BLE001
        return SETTINGS.get(key)


# ---- senders: each returns a short detail string or raises ----

def applescript_quote(text: str) -> str:
    """A double-quoted AppleScript string literal (backslash and quote escaped, newlines flattened)."""
    t = str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
    return f'"{t}"'


def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def windows_script(text: str, title: str = TITLE) -> str:
    """PowerShell: a toast through Windows.UI.Notifications, a NotifyIcon balloon if the toast API is unavailable."""
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{xml_escape(title)}</text>"
           f"<text>{xml_escape(text)}</text></binding></visual></toast>")
    app_id = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
    return f"""$ErrorActionPreference = 'Stop'
try {{
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
  $doc = New-Object Windows.Data.Xml.Dom.XmlDocument
  $doc.LoadXml({_ps_quote(xml)})
  $toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({_ps_quote(app_id)}).Show($toast)
}} catch {{
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $n = New-Object System.Windows.Forms.NotifyIcon
  $n.Icon = [System.Drawing.SystemIcons]::Information
  $n.BalloonTipTitle = {_ps_quote(title)}
  $n.BalloonTipText = {_ps_quote(text)}
  $n.Visible = $true
  $n.ShowBalloonTip(5000)
  Start-Sleep -Seconds 6
  $n.Dispose()
}}"""


def desktop_command(text: str, title: str = TITLE, platform: str | None = None) -> list[str] | None:
    """The argv that shows a desktop notification on this OS, or None when there is no way to."""
    plat = platform or sys.platform
    if plat == "darwin":
        return ["osascript", "-e", f"display notification {applescript_quote(text)} with title {applescript_quote(title)}"]
    if plat.startswith("win"):
        enc = base64.b64encode(windows_script(text, title).encode("utf-16-le")).decode("ascii")
        return ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", enc]
    exe = shutil.which("notify-send")
    if exe:
        return [exe, "--app-name", title, title, text]
    return None


def send_desktop(text: str, title: str = TITLE, priority: str = "default") -> str:
    cmd = desktop_command(text, title)
    if not cmd:
        raise RuntimeError("No desktop notifier on this computer (on Linux install notify-send).")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or f"exit {r.returncode}").strip()[:200])
    return "Shown on this computer."


def send_discord(text: str, webhook: str | None = None, title: str = TITLE, priority: str = "default") -> str:
    url = (webhook if webhook is not None else _setting("notify_discord_webhook") or "").strip()
    if not url:
        raise RuntimeError("Paste a Discord webhook URL first.")
    if not DISCORD_RE.match(url):
        raise RuntimeError("That is not a Discord webhook URL (it starts with https://discord.com/api/webhooks/).")
    r = requests.post(url, json={"content": text[:2000]}, timeout=TIMEOUT)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord answered {r.status_code}: {(r.text or '')[:160]}")
    return "Sent to Discord."


def send_ntfy(text: str, topic: str | None = None, title: str = TITLE, priority: str = "default") -> str:
    t = (topic if topic is not None else _setting("notify_ntfy_topic") or "").strip()
    if not t:
        raise RuntimeError("Choose an ntfy topic first.")
    if not TOPIC_RE.match(t):
        raise RuntimeError("An ntfy topic is letters, digits, - and _ only (up to 64).")
    r = requests.post(NTFY_BASE + t, data=text.encode("utf-8"),
                      headers={"Title": title, "Priority": "high" if priority == "high" else "default"}, timeout=TIMEOUT)
    if r.status_code >= 300:
        raise RuntimeError(f"ntfy answered {r.status_code}: {(r.text or '')[:160]}")
    return f"Sent to ntfy topic {t}."


def _sender(ch: str):
    """Looked up at call time so tests (and future channels) can swap a sender."""
    return globals()[f"send_{ch}"]


# ---- dispatch ----

def enabled_channels() -> list[str]:
    out = []
    for ch in CHANNELS:
        if not _setting(f"notify_{ch}"):
            continue
        if ch == "discord" and not str(_setting("notify_discord_webhook") or "").strip():
            continue
        if ch == "ntfy" and not str(_setting("notify_ntfy_topic") or "").strip():
            continue
        out.append(ch)
    return out


def _spawn(fn, *args) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):        # the test suite never pops real notifications
        return
    threading.Thread(target=fn, args=args, daemon=True, name="clipdash-notify").start()


def _deliver(entry: dict, text: str, priority: str) -> None:
    for ch in entry["channels"]:
        try:
            _sender(ch)(text, title=TITLE, priority=priority)
            entry["results"][ch] = "ok"
        except Exception as e:  # noqa: BLE001
            entry["results"][ch] = str(e)[:200] or type(e).__name__
            log.warning("notification via %s failed: %s", ch, e)
    errs = [f"{ch}: {r}" for ch, r in entry["results"].items() if r != "ok"]
    entry["ok"] = not errs
    entry["error"] = "; ".join(errs) or None


def notify(event: str, text: str, priority: str = "default") -> dict | None:
    """Send text on every enabled channel (in the background) if this event's toggle is on. Never raises."""
    try:
        toggle = EVENT_TOGGLE.get(event)
        if toggle and not _setting(toggle):
            return None
        channels = enabled_channels()
        entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, "text": text,
                 "priority": priority, "channels": channels, "results": {}, "ok": None if channels else False,
                 "error": None if channels else "No channel is turned on."}
        with _ring_lock:
            _recent.appendleft(entry)
        if channels:
            _spawn(_deliver, entry, text, priority)
        return entry
    except Exception:  # noqa: BLE001
        log.exception("notification skipped")
        return None


def recent() -> list[dict]:
    with _ring_lock:
        return [dict(e, results=dict(e["results"])) for e in _recent]


# ---- event handlers ----

def _clip_hook(clip_id) -> str:
    clip = db.get("clips", clip_id) if clip_id else None
    if not clip:
        return "a clip"
    if (clip.get("hook") or "").strip():
        return clip["hook"].strip()
    video = db.get("videos", clip["video_id"]) if clip.get("video_id") else None
    return ((video or {}).get("title") or clip.get("title") or "untitled clip").strip()


def _post_hook(post: dict) -> str:
    clip = db.get("clips", post.get("clip_id")) if post.get("clip_id") else None
    if clip and (clip.get("hook") or "").strip():
        return clip["hook"].strip()
    return (post.get("title") or "").strip() or _clip_hook(post.get("clip_id"))


def on_video_new(video=None, **_):
    v = video or {}
    text = f"New video found: {(v.get('title') or v.get('url') or 'untitled').strip()}"
    if v.get("live"):
        text += " · LIVE"
    notify("video.new", text)


def on_clip_ready(clip_id=None, **_):
    notify("clip.ready", f"Clip ready to review: {_clip_hook(clip_id)}")


def _on_failure(event: str, error) -> None:
    global _last_failure
    if not _setting("notify_on_failure"):
        return
    with _fail_lock:
        now = time.monotonic()
        if _last_failure and now - _last_failure < FAIL_EVERY:
            return
        _last_failure = now
    notify(event, f"Something failed: {errors.friendly(error)}")


def on_clip_failed(clip_id=None, error=None, **_):
    _on_failure("clip.failed", error)


def on_job_failed(job=None, error=None, **_):
    _on_failure("job.failed", error)


def on_post_live(post=None, **_):
    p = post or {}
    plat = PLATFORMS.get(p.get("platform") or "", (p.get("platform") or "a platform").title())
    camp = db.get("campaigns", p["campaign_id"]) if p.get("campaign_id") else None
    minutes = int((camp or {}).get("submit_minutes") or 0)
    if minutes and not db.is_workspace(camp):
        notify("post.live", f"Posted on {plat}; submit within {minutes} min", priority="high")
    else:
        notify("post.live", f"Posted on {plat}")


def on_submission_due(post=None, minutes_left=0, **_):
    notify("submission.due", f"Submission due in {int(minutes_left)} min: {_post_hook(post or {})}", priority="high")


def on_submission_missed(post=None, **_):
    notify("submission.missed", f"Submission window missed: {_post_hook(post or {})}", priority="high")


HANDLERS = {
    "video.new": on_video_new, "clip.ready": on_clip_ready, "clip.failed": on_clip_failed, "job.failed": on_job_failed,
    "post.live": on_post_live, "submission.due": on_submission_due, "submission.missed": on_submission_missed,
}
_registered = False


def register() -> None:
    global _registered
    if not _registered:
        for name, fn in HANDLERS.items():
            events.on(name, fn)
        _registered = True


# ---- API ----

@router.get("/api/notify/settings")
def get_notify_settings():
    out = {k: _setting(k) for k in SETTINGS}
    out["suggested_topic"] = "" if out.get("notify_ntfy_topic") else _suggested_topic
    out["enabled_channels"] = enabled_channels()
    out["desktop_available"] = desktop_command("x") is not None
    return out


@router.get("/api/notify/recent")
def get_recent():
    return recent()


@router.post("/api/notify/test")
def test_channel(body: dict):
    ch = (body or {}).get("channel")
    if ch not in CHANNELS:
        return {"ok": False, "detail": "Pick desktop, discord or ntfy."}
    text = "Test from Clip Dashboard: notifications work."
    kwargs = {}
    if ch == "discord" and body.get("webhook") is not None:
        kwargs["webhook"] = str(body["webhook"])
    if ch == "ntfy" and body.get("topic") is not None:
        kwargs["topic"] = str(body["topic"])
    entry = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": "test", "text": text,
             "priority": "default", "channels": [ch], "results": {}, "ok": None, "error": None}
    try:
        detail = _sender(ch)(text, title=TITLE, **kwargs)
        entry.update(ok=True, results={ch: "ok"})
        ok = True
    except Exception as e:  # noqa: BLE001
        detail = str(e)[:200] or type(e).__name__
        if isinstance(e, requests.RequestException) or isinstance(e, subprocess.TimeoutExpired):
            detail = errors.friendly(detail)
        entry.update(ok=False, results={ch: detail}, error=f"{ch}: {detail}")
        ok = False
    with _ring_lock:
        _recent.appendleft(entry)
    return {"ok": ok, "detail": detail}
