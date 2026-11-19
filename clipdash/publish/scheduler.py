"""Fires scheduled posts, polls pending ones, watches sources and refreshes stats. Runs only while the app runs
(the UI says so); YouTube direct posts use YouTube's own publishAt instead."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from .. import db, jobs

_stop = threading.Event()
_last = {"watch": 0.0, "stats": 0.0, "check": 0.0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def tick() -> None:
    now = _now()
    # 1. due scheduled posts (native YouTube schedule uploads immediately at creation, so they are 'scheduled' with external_id)
    for post in db.rows("posts", "status='scheduled' AND scheduled_at IS NOT NULL"):
        due = _parse(post["scheduled_at"])
        if post.get("external_id"):
            if due and now >= due and (time.time() - _last["check"]) > 60:
                jobs.enqueue("check_post", {"post_id": post["id"]})
            continue
        if due and now >= due:
            db.update("posts", post["id"], {"status": "publishing", "state_detail": "Scheduled time reached"})
            jobs.enqueue("publish_post", {"post_id": post["id"]})
    # 2. poll pending/draft posts every 2 minutes
    if time.time() - _last["check"] > 120:
        _last["check"] = time.time()
        for post in db.rows("posts", "status IN ('pending','draft')"):
            jobs.enqueue("check_post", {"post_id": post["id"]})
    # 3. watch sources
    interval = float(db.get_setting("watch_interval_min", 30)) * 60
    if time.time() - _last["watch"] > interval:
        _last["watch"] = time.time()
        if db.execute("SELECT COUNT(*) c FROM jobs WHERE type='check_sources' AND status IN ('queued','running')").fetchone()["c"] == 0:
            jobs.enqueue("check_sources", {})
    # 4. stats every 6 hours
    if time.time() - _last["stats"] > 6 * 3600:
        _last["stats"] = time.time()
        jobs.enqueue("refresh_stats", {})


def _loop() -> None:
    _last["watch"] = time.time() - float(db.get_setting("watch_interval_min", 30)) * 60 + 60
    _last["stats"] = time.time() - 6 * 3600 + 300
    while not _stop.is_set():
        try:
            tick()
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()
        _stop.wait(20)


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="clipdash-scheduler").start()


def stop() -> None:
    _stop.set()
