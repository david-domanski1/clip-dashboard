"""Background jobs: a small SQLite-backed queue with worker threads. Queued jobs survive restarts; anything that was
'running' when the app stopped has no live process any more, so at startup it is marked failed ("Interrupted — retry")
together with the clip, video or post it was working on. The person retries it from Today or the row itself."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from . import db, tools

_cancelled: set[int] = set()
_workers: list[threading.Thread] = []
_stop = threading.Event()
_wake = threading.Event()
HANDLERS: dict[str, Callable] = {}


def enqueue(type_: str, payload: dict | None = None) -> int:
    jid = db.insert("jobs", {"type": type_, "payload": payload or {}, "status": "queued"})
    _wake.set()
    return jid


def _claim() -> dict | None:
    c = db.conn()
    row = c.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
    if not row:
        return None
    cur = c.execute("UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=? AND status='queued'",
                    (db.now(), row["id"]))
    if cur.rowcount != 1:
        return None
    return db.get("jobs", row["id"])


def _run(job: dict) -> None:
    jid = job["id"]
    last = [0.0]

    def progress(frac, message=None):
        fields = {}
        if frac is not None:
            fields["progress"] = float(frac)
        if message:
            fields["message"] = str(message)[:200]
        if fields and (time.time() - last[0] > 0.5 or frac in (1.0, 0.0)):
            last[0] = time.time()
            db.update("jobs", jid, fields)

    handler = HANDLERS.get(job["type"])
    tools.set_current_job(jid)
    try:
        if handler is None:
            raise RuntimeError(f"Unknown job type {job['type']}")
        handler(job["payload"], progress)
        if jid in _cancelled:
            raise tools.Cancelled("cancelled")
        db.update("jobs", jid, {"status": "done", "progress": 1.0, "finished_at": db.now()})
    except tools.Cancelled:
        _cancelled.discard(jid)
        db.update("jobs", jid, {"status": "cancelled", "message": "Cancelled", "finished_at": db.now()})
        _on_cancel(job)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        db.update("jobs", jid, {"status": "error", "error": err[:4000], "finished_at": db.now()})
        _on_error(job, err)
        traceback.print_exc()


def _on_cancel(job: dict) -> None:
    p = job["payload"]
    if job["type"] == "render_clip" and p.get("clip_id"):
        clip = db.get("clips", p["clip_id"])
        if clip and clip["status"] == "rendering":
            if clip.get("moment_id"):
                db.update("moments", clip["moment_id"], {"status": "found"})
            db.delete("clips", clip["id"])
    elif job["type"] == "scan_video" and p.get("video_id"):
        v = db.get("videos", p["video_id"])
        if v and v["status"] in ("scanning", "queued"):
            db.update("videos", v["id"], {"status": "done" if v.get("scanned_ranges") else "error", "stage": "Scan cancelled"})


def cancel(job_id: int) -> bool:
    """Cancel a queued or running job. Running jobs get their child process killed; pure-Python steps
    (Whisper) finish their current step and then stop."""
    job = db.get("jobs", job_id)
    if not job:
        return False
    if job["status"] == "queued":
        db.update("jobs", job_id, {"status": "cancelled", "message": "Cancelled", "finished_at": db.now()})
        _on_cancel(job)
        return True
    if job["status"] == "running":
        _cancelled.add(job_id)
        tools.kill_job_processes(job_id)
        return True
    return False


def check_cancelled() -> None:
    jid = getattr(tools._current, "job_id", None)
    if jid is not None and jid in _cancelled:
        raise tools.Cancelled("cancelled")


def _on_error(job: dict, err: str) -> None:
    from .pipeline import _set_video
    p = job["payload"]
    if job["type"] == "scan_video" and p.get("video_id"):
        _set_video(p["video_id"], status="error", stage="Scan failed", error=err[:1000])
    elif job["type"] == "render_clip" and p.get("clip_id"):
        clip = db.get("clips", p["clip_id"])
        if clip:
            from . import config
            for stub in (config.CACHE).glob(f"*/seg_{clip['id']}.*"):
                stub.unlink(missing_ok=True)  # never reuse a half-downloaded segment on retry
            db.update("clips", clip["id"], {"status": "error", "error": err[:1000]})
            remaining = db.execute("SELECT COUNT(*) c FROM clips WHERE video_id=? AND status='rendering'", (clip["video_id"],)).fetchone()["c"]
            if remaining == 0:
                _set_video(clip["video_id"], status="done", stage="Some clips failed", error=err[:300])
    elif job["type"] == "render_intro" and p.get("clip_id"):
        clip = db.get("clips", p["clip_id"])
        if clip:
            n = dict(clip.get("narration") or {})
            n.update({"status": "error", "error": err[:300]})
            db.update("clips", clip["id"], {"narration": n})
    elif job["type"] == "publish_post" and p.get("post_id"):
        post = db.get("posts", p["post_id"])
        if post and post["status"] in ("publishing", "scheduled"):
            db.update("posts", post["id"], {"status": "failed", "error": err[:1000]})


def _worker(name: str) -> None:
    while not _stop.is_set():
        job = None
        try:
            job = _claim()
        except Exception:
            traceback.print_exc()
        if job is None:
            _wake.wait(timeout=5)
            _wake.clear()
            continue
        _run(job)
        tools.set_current_job(None)


def mark_interrupted() -> int:
    """Called at startup: running jobs, and clips/videos/posts left mid-flight with no job behind them, become failed."""
    from .errors import INTERRUPTED
    n = 0
    for job in db.rows("jobs", "status='running'"):
        db.update("jobs", job["id"], {"status": "error", "error": INTERRUPTED, "message": INTERRUPTED, "finished_at": db.now()})
        _on_error(job, INTERRUPTED)
        n += 1
    active = lambda t, key, val: db.one("jobs", f"type='{t}' AND status='queued' AND payload LIKE ?", (f'%"{key}": {val}%',))
    for clip in db.rows("clips", "status='rendering'"):
        if not active("render_clip", "clip_id", clip["id"]):
            db.update("clips", clip["id"], {"status": "error", "error": INTERRUPTED}); n += 1
    for v in db.rows("videos", "status IN ('scanning','rendering')"):
        scan_pending = active("scan_video", "video_id", v["id"])
        clip_pending = v["status"] == "rendering" and db.one("clips", "video_id=? AND status='rendering'", (v["id"],))
        if not scan_pending and not clip_pending:
            db.update("videos", v["id"], {"status": "error", "stage": "Interrupted", "error": INTERRUPTED}); n += 1
    for post in db.rows("posts", "status='publishing'"):
        if not active("publish_post", "post_id", post["id"]):
            db.update("posts", post["id"], {"status": "failed", "error": INTERRUPTED}); n += 1
    return n


def start(n_workers: int = 2) -> None:
    mark_interrupted()
    for i in range(n_workers):
        t = threading.Thread(target=_worker, args=(f"worker-{i}",), daemon=True, name=f"clipdash-worker-{i}")
        t.start()
        _workers.append(t)


def stop() -> None:
    _stop.set()
    _wake.set()
    tools.kill_all_processes()


def retry(job_id: int) -> None:
    db.update("jobs", job_id, {"status": "queued", "error": None, "progress": 0, "message": "Retrying"})
    _wake.set()


def register_handlers() -> None:
    from . import pipeline, tools
    from .publish import runner

    HANDLERS["scan_video"] = lambda p, pr: pipeline.scan_video(p["video_id"], pr, p.get("window"))
    HANDLERS["render_clip"] = lambda p, pr: pipeline.render_clip(p["clip_id"], pr)
    HANDLERS["finalize_clip"] = lambda p, pr: pipeline.finalize_clip(p["clip_id"], pr)
    HANDLERS["check_sources"] = lambda p, pr: pipeline.check_sources(pr, p.get("campaign_id"))
    HANDLERS["grab_frame"] = lambda p, pr: pipeline.grab_frame(p["source_id"], pr)
    HANDLERS["publish_post"] = lambda p, pr: runner.publish_post(p["post_id"], pr)
    HANDLERS["check_post"] = lambda p, pr: runner.check_post(p["post_id"], pr)
    HANDLERS["refresh_stats"] = lambda p, pr: runner.refresh_stats(pr, p.get("campaign_id"))
    HANDLERS["install_tdcli"] = lambda p, pr: tools.install_tdcli(lambda s: pr(None, s))
    HANDLERS["download_whisper"] = lambda p, pr: tools.download_whisper_model(db.get_setting("whisper_model"), lambda s: pr(None, s))
    HANDLERS["render_intro"] = lambda p, pr: pipeline.render_intro(p["clip_id"], pr)
    HANDLERS["download_piper_voice"] = lambda p, pr: __import__("clipdash.tts", fromlist=["x"]).download_piper_voice(lambda s: pr(None, s))
