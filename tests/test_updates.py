"""Update check (semver compare, 12 h cache, offline) and the packaged app's data folder."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from clipdash import __version__, config
from clipdash.features import updates


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.mark.parametrize("a,b,newer", [
    ("v0.2.0", "0.1.0", True), ("0.1.0", "0.1.0", False), ("0.1.1", "0.1.0", True), ("1.0", "0.9.9", True),
    ("0.1.0-beta.1", "0.1.0", False), ("0.1.0", "0.1.0-rc1", True), ("v10.0.0", "9.9.9", True), ("junk", "0.1.0", False),
])
def test_semver_compare(a, b, newer):
    assert updates.is_newer(a, b) is newer


def test_notes_short_strips_markdown():
    s = updates.notes_short("## What's new\n- **Faster** scans ([#12](https://x))\n* `fix` for TikTok\n\n" + "x" * 500)
    assert s.startswith("What's new · Faster scans (#12) · fix for TikTok · xxx") and s.endswith("…") and len(s) <= 400


def test_check_caches_for_12_hours(fresh_db, monkeypatch):
    updates.reset_cache()
    calls = []

    def fake_get(url, timeout=None, headers=None):
        calls.append(url)
        return FakeResponse({"tag_name": "v9.9.9", "html_url": "https://github.com/x/y/releases/tag/v9.9.9", "body": "# Big\n- one\n- two"})

    monkeypatch.setattr(updates.requests, "get", fake_get)
    r = updates.check()
    assert r["available"] is True and r["latest"] == "9.9.9" and r["current"] == __version__
    assert r["url"].endswith("v9.9.9") and r["notes_short"] == "Big · one · two" and r["error"] is None
    assert calls == [f"https://api.github.com/repos/{updates.DEFAULT_REPO}/releases/latest"]
    assert updates.check() is r and len(calls) == 1                                # served from cache
    updates._cache["at"] = datetime.now(timezone.utc) - timedelta(hours=11, minutes=59)
    assert updates.check() is r and len(calls) == 1
    updates._cache["at"] = datetime.now(timezone.utc) - timedelta(hours=12, minutes=1)
    assert updates.check() is not r and len(calls) == 2
    updates.check(force=True)
    assert len(calls) == 3
    fresh_db.set_setting("update_repo", "someone/fork")
    assert updates.check(force=True)["repo"] == "someone/fork" and calls[-1].startswith("https://api.github.com/repos/someone/fork/")
    updates.reset_cache()


def test_check_handles_no_release_and_offline(fresh_db, monkeypatch):
    updates.reset_cache()
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: FakeResponse({"message": "Not Found"}, 404))
    r = updates.check(force=True)
    assert r["available"] is False and r["latest"] is None and r["error"] is None and r["url"].startswith("https://github.com/")
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("offline")))
    r = updates.check(force=True)
    assert r["available"] is False and "GitHub" in r["error"]
    assert updates.check() is r                                                     # errors are cached too (shorter)
    updates._cache["at"] = datetime.now(timezone.utc) - timedelta(hours=2)
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: FakeResponse({"tag_name": __version__, "html_url": "u", "body": ""}))
    r = updates.check()
    assert r["error"] is None and r["available"] is False and r["latest"] == __version__
    updates.reset_cache()


def test_updates_endpoint(fresh_db, monkeypatch):
    updates.reset_cache()
    monkeypatch.setattr(updates.requests, "get", lambda *a, **k: FakeResponse({"tag_name": "v0.0.1", "html_url": "u", "body": ""}))
    from clipdash.app import app
    with TestClient(app) as c:
        r = c.get("/api/updates").json()
        assert set(r) >= {"current", "latest", "url", "notes_short", "available"} and r["available"] is False
    updates.reset_cache()


def test_data_dir_defaults(monkeypatch):
    import sys
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert config.default_data_dir() == config.ROOT / "data"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/jane")))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert config.default_data_dir() == Path("/Users/jane/Library/Application Support/Clip Dashboard")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\jane\AppData\Roaming")
    assert config.default_data_dir() == Path(r"C:\Users\jane\AppData\Roaming") / "Clip Dashboard"
    monkeypatch.delenv("APPDATA")
    assert config.default_data_dir() == Path("/Users/jane/AppData/Roaming/Clip Dashboard")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert config.default_data_dir() == Path("/Users/jane/.local/share/Clip Dashboard")
    monkeypatch.setenv("XDG_DATA_HOME", "/srv/data")
    assert config.default_data_dir() == Path("/srv/data/Clip Dashboard")
