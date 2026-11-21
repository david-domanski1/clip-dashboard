"""Update check: compares clipdash.__version__ with the newest GitHub release of the setting update_repo
(default "david-domanski1/clip-dashboard") at most once every 12 hours, and tells the UI where to download it.

GitHub REST (public, no token, 60 requests/hour per IP): GET https://api.github.com/repos/{owner}/{repo}/releases/latest
  -> {tag_name: "v0.2.0", name, html_url, body (markdown release notes), published_at, assets: [{name, browser_download_url}]}
  404 when the repo has no release yet (treated as "no update"). Pre-releases are never returned by /latest.

The app never downloads or installs anything itself: a person opens the release page and runs the installer. That keeps
the packaged build simple to sign and keeps a broken download from touching a working install."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter

from .. import __version__, config, db

router = APIRouter()

CHECK_INTERVAL = timedelta(hours=12)
RETRY_AFTER_ERROR = timedelta(hours=1)
TIMEOUT = 10
DEFAULT_REPO = "david-domanski1/clip-dashboard"

config.register_settings({"update_repo": DEFAULT_REPO})

_cache: dict = {"at": None, "result": None}


def parse_version(v: str) -> tuple[tuple[int, ...], int]:
    """'v1.2.3-beta.1' -> ((1, 2, 3), 0); a release with no pre-release tag ranks above one with it: ((1,2,3), 1)."""
    s = str(v or "").strip()
    s = s[1:] if s[:1] in "vV" else s
    m = re.match(r"^(\d+(?:\.\d+)*)(.*)$", s)
    if not m:
        return ((0,), 0)
    nums = tuple(int(x) for x in m.group(1).split("."))
    nums = nums + (0,) * (3 - len(nums))
    return (nums, 0 if m.group(2).strip("-+. ") else 1)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def notes_short(body: str | None, limit: int = 400) -> str:
    """First few plain lines of the release notes: markdown headings, bullets and links stripped."""
    lines = []
    for raw in str(body or "").splitlines():
        t = re.sub(r"^\s*(#+|[-*]|\d+\.)\s*", "", raw).strip()
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
        t = t.replace("**", "").replace("`", "")
        if t:
            lines.append(t)
    out = " · ".join(lines)
    return out if len(out) <= limit else out[:limit - 1].rstrip() + "…"


def fetch_latest(repo: str) -> dict:
    """One GitHub call; raises requests.RequestException when offline."""
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=TIMEOUT,
                     headers={"Accept": "application/vnd.github+json", "User-Agent": "clip-dashboard"})
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, dict) else {}


def check(force: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    cached = _cache["result"]
    if cached and not force and _cache["at"]:
        ttl = RETRY_AFTER_ERROR if cached.get("error") else CHECK_INTERVAL
        if now - _cache["at"] < ttl:
            return cached
    repo = str(db.get_setting("update_repo", DEFAULT_REPO) or DEFAULT_REPO).strip().strip("/")
    out = {"current": __version__, "latest": None, "url": f"https://github.com/{repo}/releases", "notes_short": "",
           "available": False, "checked_at": now.isoformat(timespec="seconds"), "error": None, "repo": repo}
    try:
        rel = fetch_latest(repo)
    except (requests.RequestException, ValueError) as e:
        out["error"] = "Could not reach GitHub to check for updates." if isinstance(e, requests.RequestException) else str(e)
        rel = None
    if rel:
        tag = str(rel.get("tag_name") or rel.get("name") or "")
        out["latest"] = tag[1:] if tag[:1] in "vV" else tag
        out["url"] = rel.get("html_url") or out["url"]
        out["notes_short"] = notes_short(rel.get("body"))
        out["published_at"] = rel.get("published_at")
        out["available"] = is_newer(tag, __version__)
    _cache.update({"at": now, "result": out})
    return out


def reset_cache() -> None:
    _cache.update({"at": None, "result": None})


@router.get("/api/updates")
def get_updates(force: bool = False):
    return check(force=force)
