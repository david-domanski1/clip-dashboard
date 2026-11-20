"""Tiny in-process event bus so features can react to pipeline moments without editing the pipeline.

Emitted today (name → payload keys):
  video.new        video (row)                       a watched channel surfaced a new video / live stream
  scan.done        video_id, moments (int)           a section search finished
  clip.ready       clip_id                           a clip rendered and is waiting for review
  clip.failed      clip_id, error                    a render failed
  job.failed       job (row), error                  any background job failed
  post.live        post (row)                        a post went live (submission countdown started)
  submission.due   post (row), minutes_left (int)    a live post's submission deadline is 10 minutes away (once per post)
  submission.missed post (row)                       the deadline passed without a submission (once per post)
  moments.scored   video (row), moments (list of dicts, mutable), settings   after scoring, before ranking: adjust in place
  post.views       post (row), views (int)           a view count was refreshed for a live post

Handlers run synchronously on the emitting thread and must never raise; exceptions are logged and swallowed."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

log = logging.getLogger("clipdash.events")
_handlers: dict[str, list[Callable]] = defaultdict(list)


def on(name: str, fn: Callable) -> Callable:
    """Subscribe fn(**payload) to an event. Returns fn so it can be used as a decorator: @events.on("clip.ready")"""
    _handlers[name].append(fn)
    return fn


def emit(name: str, **payload) -> None:
    for fn in list(_handlers.get(name, [])) + list(_handlers.get("*", [])):
        try:
            fn(name, **payload) if fn in _handlers.get("*", []) else fn(**payload)
        except Exception:  # noqa: BLE001
            log.exception("event handler for %s failed", name)


def clear() -> None:
    _handlers.clear()
