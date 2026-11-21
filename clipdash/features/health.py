"""Health: installed tool versions, a one-click yt-dlp update, and a plain-words verdict when downloads start failing
the way they do after YouTube or Twitch change something.

  GET  /api/health                  versions, update_available, download failures in the last 24 h, verdict, last events
  POST /api/health/update_yt_dlp    runs `python -m pip install --upgrade yt-dlp` as a background job (uv when the venv
                                    has no pip, which is how uv creates venvs)
  POST /api/health/selftest         quick end-to-end check: video info via yt-dlp, 3 s of ffmpeg audio, Whisper on silence

The latest yt-dlp version comes from PyPI (cached 6 h, 10 s timeout) and is only fetched when the Health screen or the
Today page asks for it, never at startup. Failures are counted from the job.failed event: the raw error text is matched
against yt-dlp's known "the site changed" signatures and stored in health_events so the count survives a restart.
Nothing here posts anything or sends credentials anywhere; the only network calls are PyPI and the self-test video."""
from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter

from .. import config, db, events, jobs, tools

log = logging.getLogger("clipdash.features.health")
router = APIRouter()

db.register_table("CREATE TABLE IF NOT EXISTS health_events (id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT NOT NULL, "
                  "kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')")

PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
PYPI_TTL_S = 6 * 3600
PYPI_RETRY_S = 10 * 60          # after a failed lookup, do not hammer PyPI on every page load
PYPI_TIMEOUT_S = 10
UPDATE_TIMEOUT_S = 600
SELFTEST_URL = "https://www.youtube.com/watch?v=QdBZY2fkU-0"
WINDOW_H = 24
EVENT_LIMIT = 10

# yt-dlp's own wording when an extractor is out of date. Order does not matter; any hit counts.
OUTDATED_SIGNATURES = (
    r"Sign in to confirm you['’]re not a bot",
    r"Unable to extract",
    r"HTTP Error 403",
    r"Requested format is not available",
    r"nsig extraction failed",
    r"Unsupported URL",
    r"Precondition check failed",
)
_FRESH_ONLY_SIGNATURES = (r"This video is not available",)   # only suspicious when the video is brand new
FRESH_VIDEO_DAYS = 2

_pypi_cache: dict = {"at": 0.0, "version": None, "error": None}
_pypi_lock = threading.Lock()
_mem_failures: list[tuple[float, str]] = []        # (unix time, detail): the count if the table is unavailable
_mem_lock = threading.Lock()
_tdcli_cache: dict = {"key": None, "version": None}


# ---------------------------------------------------------------- versions

def _vtuple(v: str | None) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def _dist_version(dist: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(dist)
    except Exception:  # noqa: BLE001
        return None


def yt_dlp_version() -> str | None:
    try:
        import yt_dlp.version
        return yt_dlp.version.__version__
    except Exception:  # noqa: BLE001
        return None


def _reread_yt_dlp_version() -> str | None:
    """After pip replaced the files, read the version again (yt-dlp itself runs as a subprocess, so downloads use the
    new code straight away; the version module is reloaded only so the screen shows the right number)."""
    importlib.invalidate_caches()
    try:
        import yt_dlp.version
        importlib.reload(yt_dlp.version)
        return yt_dlp.version.__version__
    except Exception:  # noqa: BLE001
        return yt_dlp_version()


def tdcli_version() -> str | None:
    exe = tools.tdcli_path()
    if not exe.exists():
        return None
    try:
        key = (str(exe), exe.stat().st_mtime)
    except OSError:
        return None
    if _tdcli_cache["key"] == key:
        return _tdcli_cache["version"]
    ver = None
    try:
        out = tools.run([str(exe), "--version"], timeout=20)
        m = re.search(r"\d+(?:\.\d+)+", out)
        ver = m.group(0) if m else (out.strip().splitlines() or ["installed"])[0][:40]
    except Exception:  # noqa: BLE001
        ver = "installed (version unknown)"
    _tdcli_cache.update({"key": key, "version": ver})
    return ver


def latest_yt_dlp(force: bool = False) -> dict:
    """{"version", "at", "error"} for the newest yt-dlp on PyPI; cached for 6 h, a failure for 10 min."""
    import requests
    with _pypi_lock:
        now = time.time()
        age = now - _pypi_cache["at"]
        fresh = _pypi_cache["at"] and age < (PYPI_RETRY_S if _pypi_cache["error"] else PYPI_TTL_S)
        if fresh and not force:
            return dict(_pypi_cache)
        try:
            r = requests.get(PYPI_URL, timeout=PYPI_TIMEOUT_S, headers={"Accept": "application/json"})
            r.raise_for_status()
            ver = str(r.json()["info"]["version"])
            if not _vtuple(ver):
                raise ValueError(f"odd version string {ver!r}")
            _pypi_cache.update({"at": now, "version": ver, "error": None})
        except Exception as e:  # noqa: BLE001
            _pypi_cache.update({"at": now, "error": f"{type(e).__name__}: {e}"[:200]})   # a known version is kept
        return dict(_pypi_cache)


def tool_versions(check_pypi: bool = True) -> dict:
    """Installed versions of the tools the pipeline leans on, plus the latest yt-dlp from PyPI."""
    installed = yt_dlp_version()
    latest = latest_yt_dlp() if check_pypi else dict(_pypi_cache)
    newest = latest.get("version")
    update_available = bool(installed and newest and _vtuple(newest) > _vtuple(installed))
    try:
        ffmpeg_detail = Path(tools.ffmpeg_path()).name
    except Exception:  # noqa: BLE001
        ffmpeg_detail = "not found"
    tdcli = tdcli_version()
    return {
        "yt_dlp": {"installed": installed, "latest": newest, "update_available": update_available,
                   "checked_at": datetime.fromtimestamp(latest["at"], timezone.utc).isoformat() if latest.get("at") else None,
                   "error": latest.get("error")},
        "tools": [
            {"id": "yt_dlp", "name": "yt-dlp", "role": "downloads YouTube and Twitch video", "installed": installed, "latest": newest,
             "update_available": update_available, "updatable": True},
            {"id": "faster_whisper", "name": "faster-whisper", "role": "captions", "installed": _dist_version("faster-whisper"), "latest": None,
             "update_available": False, "updatable": False},
            {"id": "imageio_ffmpeg", "name": "imageio-ffmpeg", "role": f"video converter ({ffmpeg_detail})", "installed": _dist_version("imageio-ffmpeg"),
             "latest": None, "update_available": False, "updatable": False},
            {"id": "tdcli", "name": "TwitchDownloaderCLI", "role": "Twitch chat replays", "installed": tdcli, "latest": None,
             "update_available": False, "updatable": False, "detail": None if tdcli else "not downloaded (only needed for Twitch chat)"},
        ],
    }


# ---------------------------------------------------------------- failure classification

def looks_like_outdated_downloader(error_text: str | None, video_age_days: float | None = None) -> bool:
    """True when the raw error reads like yt-dlp's extractor is behind the site. 'This video is not available' only
    counts for a video under two days old: older videos that say so are usually really gone."""
    text = str(error_text or "")
    if not text.strip():
        return False
    if any(re.search(p, text, re.I) for p in OUTDATED_SIGNATURES):
        return True
    if video_age_days is not None and video_age_days < FRESH_VIDEO_DAYS:
        return any(re.search(p, text, re.I) for p in _FRESH_ONLY_SIGNATURES)
    return False


def _video_age_days(job: dict) -> float | None:
    p = job.get("payload") or {}
    try:
        vid = p.get("video_id")
        if not vid and p.get("clip_id"):
            clip = db.get("clips", p["clip_id"])
            vid = (clip or {}).get("video_id")
        v = db.get("videos", vid) if vid else None
        when = (v or {}).get("published_at")
        if not when:
            return None
        dt = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    except Exception:  # noqa: BLE001
        return None


def record_event(kind: str, detail: str = "") -> None:
    detail = str(detail or "")[:2000]
    with _mem_lock:
        if kind == "download_failure":
            _mem_failures.append((time.time(), detail))
            del _mem_failures[:-200]
        elif kind == "update":
            _mem_failures.clear()            # failures before an update belong to the old version
    try:   # plain SQL: db.insert() would add a created_at column this table does not have
        db.execute("INSERT INTO health_events (t, kind, detail) VALUES (?, ?, ?)", (db.now(), kind, detail))
    except Exception:  # noqa: BLE001
        log.warning("could not store health event %s", kind, exc_info=True)


def _on_job_failed(job: dict, error: str, **_) -> None:
    if looks_like_outdated_downloader(error, _video_age_days(job)):
        first = next((l.strip() for l in str(error).splitlines() if l.strip() and not re.search(r"failed:?$", l.strip())), str(error).strip())
        record_event("download_failure", f"{job.get('type', 'job')}: {first[:300]}")


def recent_failures(hours: int = WINDOW_H) -> int:
    """Download failures that look like an outdated yt-dlp, in the last `hours`, counting only rows after the last update
    (ordered by row id, since an update and a failure can share the same second)."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        r = db.execute("SELECT MAX(id) m FROM health_events WHERE kind='update'").fetchone()
        after_id = r["m"] or 0
        return db.execute("SELECT COUNT(*) c FROM health_events WHERE kind='download_failure' AND t>=? AND id>?",
                          (since, after_id)).fetchone()["c"]
    except Exception:  # noqa: BLE001
        cutoff = time.time() - hours * 3600
        with _mem_lock:
            return sum(1 for t, _ in _mem_failures if t >= cutoff)


def verdict(versions: dict, failures: int) -> dict:
    """Plain words for the top of the Health screen and, when actionable, a card on Today."""
    y = versions["yt_dlp"]
    installed, latest, upd = y.get("installed"), y.get("latest"), y.get("update_available")
    if (upd and failures >= 1) or failures >= 3:
        text = (f"Downloads are failing in a way that usually means yt-dlp needs an update ({failures} in the last {WINDOW_H} h). "
                + ("Update now." if upd or not latest else
                   "You already have the newest release; the fix usually ships within a day, so try Update again later."))
        return {"text": text, "actionable": True, "level": "warn"}
    if upd:
        return {"text": f"A newer yt-dlp is available ({installed} → {latest}). Downloads are working; update when convenient.",
                "actionable": False, "level": "info"}
    if not installed:
        return {"text": "yt-dlp is not installed, so nothing can be downloaded. Run the installer again.", "actionable": False, "level": "warn"}
    if failures:
        return {"text": f"{failures} download{'s' if failures > 1 else ''} failed in the last {WINDOW_H} h in a way that can mean yt-dlp is behind. "
                        "Not enough to be sure yet; if it keeps happening, update.", "actionable": False, "level": "info"}
    if not latest:
        return {"text": f"Could not check PyPI for the latest yt-dlp ({y.get('error') or 'no answer'}). No download failures in the last {WINDOW_H} h.",
                "actionable": False, "level": "info"}
    return {"text": "Everything looks healthy.", "actionable": False, "level": "ok"}


# ---------------------------------------------------------------- jobs

def _find_uv() -> str | None:
    exe = "uv.exe" if os.name == "nt" else "uv"
    found = shutil.which("uv")
    if found:
        return found
    for d in (Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"):   # apps started from Finder lack the shell PATH
        if (d / exe).exists():
            return str(d / exe)
    return None


def upgrade_commands() -> list[list[str]]:
    """The commands that upgrade yt-dlp in this app's own Python: pip when the venv has it, else uv (uv-made venvs have
    no pip), else bootstrap pip with ensurepip first."""
    pip = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-input", "yt-dlp"]
    if importlib.util.find_spec("pip") is not None:
        return [pip]
    uv = _find_uv()
    if uv:
        return [[uv, "pip", "install", "--python", sys.executable, "--upgrade", "yt-dlp"]]
    return [[sys.executable, "-m", "ensurepip", "--upgrade"], pip]


def update_yt_dlp(payload: dict, progress) -> None:
    before = yt_dlp_version()
    progress(0.05, "Updating yt-dlp…")
    for cmd in upgrade_commands():
        tools.run(cmd, timeout=UPDATE_TIMEOUT_S,
                  on_line=lambda line: progress(None, line.strip()[:200]) if line.strip() else None)
    after = _reread_yt_dlp_version()
    _tdcli_cache["key"] = None
    detail = f"yt-dlp {before or '?'} → {after or '?'}" if after != before else f"yt-dlp {after or '?'} was already the newest"
    record_event("update", detail)
    progress(1.0, detail)


def _fmt_dur(seconds) -> str:
    try:
        s = int(float(seconds or 0))
    except (TypeError, ValueError):
        return "?"
    return f"{s // 60}:{s % 60:02d}"


def selftest(payload: dict, progress) -> None:
    """Three quick steps; each records ok/failed/skipped with the reason, then the job fails if any step failed."""
    from .. import sources
    steps: list[dict] = []

    def step(name: str, label: str, fn):
        progress(None, f"Self-test: {label}…")
        t0 = time.time()
        try:
            detail = fn()
            steps.append({"name": name, "label": label, "status": "ok" if detail is not None else "skipped",
                          "detail": detail or "", "seconds": round(time.time() - t0, 1)})
        except Exception as e:  # noqa: BLE001
            steps.append({"name": name, "label": label, "status": "failed", "detail": f"{type(e).__name__}: {e}"[:400],
                          "seconds": round(time.time() - t0, 1)})

    def video_info():
        info = sources.video_info(SELFTEST_URL)
        return f"{info.get('title') or 'video'} ({_fmt_dur(info.get('duration'))})"

    def ffmpeg_audio():
        config.ensure_dirs()
        out = config.CACHE / "selftest_audio.wav"
        out.unlink(missing_ok=True)
        tools.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-t", "3",
                   "-c:a", "pcm_s16le", "-ac", "1", "-ar", "16000", str(out)], timeout=60)
        size = out.stat().st_size if out.exists() else 0
        out.unlink(missing_ok=True)
        if size < 90_000:
            raise tools.ToolError(f"ffmpeg produced {size} bytes of audio, expected about 96000")
        return "decoded 3 s of audio to WAV"

    def whisper():
        name = db.get_setting("whisper_model") or "small"
        if not tools.whisper_model_present(name):
            progress(None, f"Self-test: Whisper '{name}' model not downloaded, skipped")
            return None
        import numpy as np
        from faster_whisper import WhisperModel
        model = WhisperModel(name, device="auto", compute_type="int8")
        segments, _info = model.transcribe(np.zeros(32000, dtype=np.float32), beam_size=1, vad_filter=False)
        list(segments)
        return f"'{name}' model loaded and ran on 2 s of silence"

    step("video_info", "fetch video info with yt-dlp", video_info)
    step("ffmpeg", "decode audio with ffmpeg", ffmpeg_audio)
    step("whisper", "run Whisper", whisper)
    failed = [s for s in steps if s["status"] == "failed"]
    record_event("selftest", json.dumps({"ok": not failed, "steps": steps}))
    progress(1.0, "Self-test passed" if not failed else f"Self-test: {len(failed)} step{'s' if len(failed) > 1 else ''} failed")
    if failed:
        raise tools.ToolError("Self-test failed:\n" + "\n".join(f"{s['label']}: {s['detail']}" for s in failed))


# ---------------------------------------------------------------- routes

def _active_job(type_: str) -> int | None:
    j = db.one("jobs", "type=? AND status IN ('queued','running')", (type_,))
    return j["id"] if j else None


def _events(limit: int = EVENT_LIMIT) -> list[dict]:
    try:
        rows = [dict(r) for r in db.execute("SELECT * FROM health_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    except Exception:  # noqa: BLE001
        return []
    for r in rows:
        if r["kind"] == "selftest":
            try:
                r["steps"] = json.loads(r["detail"])
            except ValueError:
                r["steps"] = None
    return rows


def status() -> dict:
    versions = tool_versions()
    failures = recent_failures()
    v = verdict(versions, failures)
    ev = _events()
    last_selftest = next((e for e in ev if e["kind"] == "selftest"), None)
    if last_selftest is None:
        try:
            r = db.execute("SELECT * FROM health_events WHERE kind='selftest' ORDER BY id DESC LIMIT 1").fetchone()
            last_selftest = dict(r, steps=json.loads(r["detail"])) if r else None
        except Exception:  # noqa: BLE001
            last_selftest = None
    return {"versions": versions["tools"], "yt_dlp": versions["yt_dlp"], "update_available": versions["yt_dlp"]["update_available"],
            "failures_24h": failures, "verdict": v["text"], "actionable": v["actionable"], "level": v["level"], "events": ev,
            "selftest": last_selftest, "update_job": _active_job("update_yt_dlp"), "selftest_job": _active_job("health_selftest")}


@router.get("/api/health")
def get_health():
    return status()


@router.post("/api/health/update_yt_dlp")
def post_update():
    jid = _active_job("update_yt_dlp")
    return {"job_id": jid or jobs.enqueue("update_yt_dlp", {}), "already_running": bool(jid)}


@router.post("/api/health/selftest")
def post_selftest():
    jid = _active_job("health_selftest")
    return {"job_id": jid or jobs.enqueue("health_selftest", {}), "already_running": bool(jid)}


def register() -> None:
    jobs.HANDLERS["update_yt_dlp"] = update_yt_dlp
    jobs.HANDLERS["health_selftest"] = selftest
    if _on_job_failed not in events._handlers.get("job.failed", []):
        events.on("job.failed", _on_job_failed)
