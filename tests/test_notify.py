"""Notifications: message text per event, the failure throttle, ntfy/Discord payloads, toggles, never raising, endpoints."""
import subprocess

import pytest

from clipdash.features import notify

HOOK = "LACY gets pulled over in a stolen bus"
WEBHOOK = "https://discord.com/api/webhooks/123456/abc-DEF_9"


class Resp:
    def __init__(self, status=204, text=""):
        self.status_code, self.text = status, text


@pytest.fixture()
def sent(fresh_db, monkeypatch):
    """Run deliveries inline and capture what each channel would send."""
    out = []
    monkeypatch.setattr(notify, "_spawn", lambda fn, *a: fn(*a))
    monkeypatch.setattr(notify, "_recent", notify.deque(maxlen=notify.RING_SIZE))
    monkeypatch.setattr(notify, "_last_failure", 0.0)
    for ch in notify.CHANNELS:
        monkeypatch.setattr(notify, f"send_{ch}", lambda text, ch=ch, **kw: out.append((ch, text, kw.get("priority"))) or "ok")
    return out


def _texts(sent):
    return [t for _, t, _ in sent]


def _clip(db, campaign, hook=HOOK, title="Day 3 of GTA VI"):
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "v1", "url": "u",
                               "title": title, "channel": "lacy"})
    return db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "status": "pending", "hook": hook,
                               "source_start": 10, "source_length": 90})


def _post(db, campaign, clip_id, platform="tiktok"):
    aid = db.insert("accounts", {"platform": platform, "label": "tt", "route": "assisted"})
    pid = db.insert("posts", {"clip_id": clip_id, "account_id": aid, "campaign_id": campaign["id"], "platform": platform,
                              "route": "assisted", "status": "live"})
    return db.get("posts", pid)


def test_message_text_for_each_event(sent, campaign, fresh_db):
    db = fresh_db
    notify.on_video_new(video={"title": "GTA VI day 3", "live": 0})
    notify.on_video_new(video={"title": "GTA VI day 4", "live": 1})
    cid = _clip(db, campaign)
    notify.on_clip_ready(clip_id=cid)
    no_hook = db.insert("clips", {"video_id": db.get("clips", cid)["video_id"], "campaign_id": campaign["id"],
                                  "status": "pending", "source_start": 0, "source_length": 90})
    notify.on_clip_ready(clip_id=no_hook)
    post = _post(db, campaign, cid)
    notify.on_post_live(post=post)
    notify.on_submission_due(post=post, minutes_left=10)
    notify.on_submission_missed(post=post)
    notify.on_job_failed(job={"id": 1}, error="requests.exceptions.ConnectionError: Max retries exceeded")
    assert _texts(sent) == [
        "New video found: GTA VI day 3",
        "New video found: GTA VI day 4 · LIVE",
        f"Clip ready to review: {HOOK}",
        "Clip ready to review: Day 3 of GTA VI",
        "Posted on TikTok; submit within 30 min",
        f"Submission due in 10 min: {HOOK}",
        f"Submission window missed: {HOOK}",
        "Something failed: No internet connection, or the platform is unreachable. Retry when online.",
    ]
    prio = {t: p for _, t, p in sent}
    assert prio[f"Submission due in 10 min: {HOOK}"] == "high" and prio["New video found: GTA VI day 3"] == "default"
    assert [e["event"] for e in notify.recent()][:2] == ["job.failed", "submission.missed"]
    assert notify.recent()[0]["ok"] is True and notify.recent()[0]["results"] == {"desktop": "ok"}


def test_post_live_skips_submit_part_for_workspace_and_zero(sent, campaign, fresh_db):
    db = fresh_db
    cid = _clip(db, campaign)
    ws = db.ensure_workspace()
    p = _post(db, ws, cid, platform="youtube")
    notify.on_post_live(post=p)
    db.update("campaigns", campaign["id"], {"submit_minutes": 0})
    notify.on_post_live(post=_post(db, campaign, cid, platform="instagram"))
    assert _texts(sent) == ["Posted on YouTube", "Posted on Instagram"]


def test_failure_rate_limit(sent, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(notify.time, "monotonic", lambda: clock[0])
    notify.on_clip_failed(clip_id=1, error="ffmpeg failed")
    notify.on_job_failed(job={}, error="ffmpeg failed")       # same failure, second event: throttled
    clock[0] += 30
    notify.on_job_failed(job={}, error="other")
    clock[0] += 31
    notify.on_job_failed(job={}, error="HTTP Error 429")
    assert len(sent) == 2 and sent[1][1].startswith("Something failed: The platform is rate-limiting")


def test_toggles_respected(sent, fresh_db):
    fresh_db.set_setting("notify_on_new_video", False)
    fresh_db.set_setting("notify_on_failure", False)
    notify.on_video_new(video={"title": "x"})
    notify.on_job_failed(job={}, error="boom")
    assert sent == [] and notify.recent() == []
    # channels: discord/ntfy only when turned on AND configured; desktop off means nothing goes out
    fresh_db.set_setting("notify_desktop", False)
    fresh_db.set_setting("notify_ntfy", True)                 # on but no topic yet
    assert notify.enabled_channels() == []
    notify.on_clip_ready(clip_id=None)
    assert sent == [] and notify.recent()[0]["error"] == "No channel is turned on."
    fresh_db.set_setting("notify_ntfy_topic", "clipdash-abcdefghij")
    fresh_db.set_setting("notify_discord", True)
    fresh_db.set_setting("notify_discord_webhook", WEBHOOK)
    assert notify.enabled_channels() == ["discord", "ntfy"]
    notify.on_clip_ready(clip_id=None)
    assert [c for c, _, _ in sent] == ["discord", "ntfy"]


def test_ntfy_and_discord_payload_shapes(fresh_db, monkeypatch):
    calls = []
    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: calls.append((url, kw)) or Resp(200))
    notify.send_ntfy("Submission due in 10 min: hi", topic="clipdash-abcdefghij", priority="high")
    notify.send_ntfy("Clip ready", topic="clipdash-abcdefghij")
    notify.send_discord("Clip ready to review: hi", webhook=WEBHOOK)
    (u1, k1), (_, k2), (u3, k3) = calls
    assert u1 == "https://ntfy.sh/clipdash-abcdefghij" and k1["data"] == "Submission due in 10 min: hi".encode()
    assert k1["headers"] == {"Title": "Clip Dashboard", "Priority": "high"} and k1["timeout"] == 10
    assert k2["headers"]["Priority"] == "default"
    assert u3 == WEBHOOK and k3["json"] == {"content": "Clip ready to review: hi"} and k3["timeout"] == 10
    with pytest.raises(RuntimeError):
        notify.send_discord("x", webhook="https://evil.example/api/webhooks/1/a")
    with pytest.raises(RuntimeError):
        notify.send_ntfy("x", topic="../admin")
    assert len(calls) == 3


def test_desktop_command_escaping():
    cmd = notify.desktop_command('He said "go" \\ now\nok', platform="darwin")
    assert cmd[:2] == ["osascript", "-e"]
    assert cmd[2] == 'display notification "He said \\"go\\" \\\\ now ok" with title "Clip Dashboard"'
    win = notify.windows_script("It's <fast> & loud")
    assert "It''s &lt;fast&gt; &amp; loud" in win and "NotifyIcon" in win and "ToastNotificationManager" in win
    assert notify.desktop_command("x", platform="win32")[-2] == "-EncodedCommand"


def test_failing_sender_never_raises(fresh_db, monkeypatch):
    monkeypatch.setattr(notify, "_spawn", lambda fn, *a: fn(*a))
    monkeypatch.setattr(notify, "_recent", notify.deque(maxlen=notify.RING_SIZE))

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("osascript", 10)
    monkeypatch.setattr(notify, "send_desktop", boom)
    notify.on_video_new(video={"title": "x"})
    e = notify.recent()[0]
    assert e["ok"] is False and e["error"].startswith("desktop:")
    # even a broken database read inside a handler is swallowed
    monkeypatch.setattr(notify.db, "get", lambda *a: (_ for _ in ()).throw(RuntimeError("db gone")))
    from clipdash import events
    events.emit("clip.ready", clip_id=5)        # the bus swallows; the handler itself is also guarded by notify()


def test_ring_keeps_last_50(sent):
    for i in range(60):
        notify.notify("clip.ready", f"n{i}")
    r = notify.recent()
    assert len(r) == 50 and r[0]["text"] == "n59" and r[-1]["text"] == "n10"


def test_register_subscribes_once(monkeypatch):
    from clipdash import events
    seen = []
    monkeypatch.setattr(events, "on", lambda name, fn: seen.append(name))
    monkeypatch.setattr(notify, "_registered", False)
    notify.register()
    notify.register()
    assert sorted(seen) == sorted(notify.HANDLERS) and len(seen) == 7


def test_endpoints(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    monkeypatch.setattr(notify, "_recent", notify.deque(maxlen=notify.RING_SIZE))
    calls = []
    monkeypatch.setattr(notify.requests, "post", lambda url, **kw: calls.append(url) or Resp(200))
    with TestClient(app) as c:
        st = c.get("/api/notify/settings").json()
        assert st["notify_desktop"] is True and st["notify_discord"] is False and st["notify_ntfy"] is False
        assert st["notify_on_submission_due"] is True
        assert st["suggested_topic"].startswith("clipdash-") and len(st["suggested_topic"]) == 19
        assert c.put("/api/settings", json={"notify_ntfy_topic": "clipdash-qwertyuiop", "notify_ntfy": True}).status_code == 200
        st = c.get("/api/notify/settings").json()
        assert st["suggested_topic"] == "" and "ntfy" in st["enabled_channels"]
        r = c.post("/api/notify/test", json={"channel": "ntfy"}).json()
        assert r == {"ok": True, "detail": "Sent to ntfy topic clipdash-qwertyuiop."}
        r = c.post("/api/notify/test", json={"channel": "discord", "webhook": ""}).json()
        assert r["ok"] is False and "webhook" in r["detail"]
        assert c.post("/api/notify/test", json={"channel": "pager"}).json()["ok"] is False
        rec = c.get("/api/notify/recent").json()
        assert [x["event"] for x in rec] == ["test", "test"] and rec[1]["ok"] is True
    assert calls == ["https://ntfy.sh/clipdash-qwertyuiop"]
