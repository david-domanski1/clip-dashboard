"""Background jobs: a small SQLite-backed queue with worker threads. Jobs survive restarts
(anything 'running' at startup goes back to 'queued')."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from . import db

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
    try:
        if handler is None:
            raise RuntimeError(f"Unknown job type {job['type']}")
        handler(job["payload"], progress)
        db.update("jobs", jid, {"status": "done", "progress": 1.0, "finished_at": db.now()})
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        db.update("jobs", jid, {"status": "error", "error": err[:4000], "finished_at": db.now()})
        _on_error(job, err)
        traceback.print_exc()


def _on_error(job: dict, err: str) -> None:
    from .pipeline import _set_video
    p = job["payload"]
    if job["type"] == "scan_video" and p.get("video_id"):
        _set_video(p["video_id"], status="error", stage="Scan failed", error=err[:1000])
    elif job["type"] == "render_clip" and p.get("clip_id"):
        clip = db.get("clips", p["clip_id"])
        if clip:
            db.update("clips", clip["id"], {"status": "error", "error": err[:1000]})
            remaining = db.execute("SELECT COUNT(*) c FROM clips WHERE video_id=? AND status='rendering'", (clip["video_id"],)).fetchone()["c"]
            if remaining == 0:
                _set_video(clip["video_id"], status="done", stage="Some clips failed", error=err[:300])
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


def start(n_workers: int = 2) -> None:
    db.execute("UPDATE jobs SET status='queued', message='Resumed after restart' WHERE status='running'")
    for i in range(n_workers):
        t = threading.Thread(target=_worker, args=(f"worker-{i}",), daemon=True, name=f"clipdash-worker-{i}")
        t.start()
        _workers.append(t)


def stop() -> None:
    _stop.set()
    _wake.set()


def retry(job_id: int) -> None:
    db.update("jobs", job_id, {"status": "queued", "error": None, "progress": 0, "message": "Retrying"})
    _wake.set()


def register_handlers() -> None:
    from . import pipeline, tools
    from .publish import runner

    HANDLERS["scan_video"] = lambda p, pr: pipeline.scan_video(p["video_id"], pr)
    HANDLERS["render_clip"] = lambda p, pr: pipeline.render_clip(p["clip_id"], pr)
    HANDLERS["finalize_clip"] = lambda p, pr: pipeline.finalize_clip(p["clip_id"], pr)
    HANDLERS["check_sources"] = lambda p, pr: pipeline.check_sources(pr, p.get("campaign_id"))
    HANDLERS["grab_frame"] = lambda p, pr: pipeline.grab_frame(p["source_id"], pr)
    HANDLERS["publish_post"] = lambda p, pr: runner.publish_post(p["post_id"], pr)
    HANDLERS["check_post"] = lambda p, pr: runner.check_post(p["post_id"], pr)
    HANDLERS["refresh_stats"] = lambda p, pr: runner.refresh_stats(pr, p.get("campaign_id"))
    HANDLERS["install_tdcli"] = lambda p, pr: tools.install_tdcli(lambda s: pr(None, s))
    HANDLERS["download_whisper"] = lambda p, pr: tools.download_whisper_model(db.get_setting("whisper_model"), lambda s: pr(None, s))
