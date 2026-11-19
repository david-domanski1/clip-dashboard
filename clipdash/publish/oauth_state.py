"""In-memory store for OAuth flows that come back through the app's own /oauth/callback route."""
from __future__ import annotations

import secrets
import threading
import time

_lock = threading.Lock()
_pending: dict[str, dict] = {}
_results: dict[int, dict] = {}


def new_state(account_id: int, data: dict | None = None) -> str:
    s = secrets.token_urlsafe(24)
    with _lock:
        _pending[s] = {"account_id": account_id, "created": time.time(), **(data or {})}
    return s


def pop_state(state: str) -> dict | None:
    with _lock:
        return _pending.pop(state, None)


def set_result(account_id: int, result: dict) -> None:
    with _lock:
        _results[account_id] = result


def take_result(account_id: int) -> dict | None:
    with _lock:
        return _results.pop(account_id, None)
