"""Credentials live in data/secrets.json (gitignored, mode 0600) or environment variables.
Keys are documented in .env.example. Nothing here is ever logged or returned to the browser in full."""
import json
import os
import stat
from typing import Any

from . import config


def _load() -> dict:
    try:
        return json.loads(config.SECRETS_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save(d: dict) -> None:
    config.ensure_dirs()
    config.SECRETS_PATH.write_text(json.dumps(d, indent=2))
    try:
        os.chmod(config.SECRETS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def get(key: str, default: Any = None) -> Any:
    env = os.environ.get(key.upper())
    if env:
        return env
    return _load().get(key, default)


def set(key: str, value: Any) -> None:  # noqa: A001
    d = _load()
    if value in (None, ""):
        d.pop(key, None)
    else:
        d[key] = value
    _save(d)


def delete(key: str) -> None:
    set(key, None)


def has(key: str) -> bool:
    return bool(get(key))


def masked(key: str) -> str:
    v = get(key)
    if not v:
        return ""
    s = v if isinstance(v, str) else json.dumps(v)
    return s[:4] + "…" + s[-3:] if len(s) > 10 else "•••"


def keys() -> list[str]:
    return sorted(_load())
