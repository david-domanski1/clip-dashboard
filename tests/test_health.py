"""Health feature: outdated-yt-dlp classifier, the cached PyPI check, failure counting from job.failed, verdict wording,
the update and self-test jobs (no pip, no network) and the /api/health endpoints."""
import json
import sys
import time

import pytest
from fastapi.testclient import TestClient

from clipdash import db, events, jobs, tools
from clipdash.features import health


@pytest.fixture()
def hx(fresh_db, monkeypatch):
    """A clean health state: empty PyPI cache and in-memory counters, handlers registered, PyPI unreachable by default."""
    health._pypi_cache.update({"at": 0.0, "version": None, "error": None})
    health._mem_failures.clear()
    health._tdcli_cache.update({"key": None, "version": None})
    health.register()
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("offline")))
    yield health
    health._mem_failures.clear()
    health._pypi_cache.update({"at": 0.0, "version": None, "error": None})


class FakeResp:
    def __init__(self, version="2099.1.1"):
        self.version = version

    def raise_for_status(self):
        pass

    def json(self):
        return {"info": {"version": self.version}}


# ---------------------------------------------------------------- classifier

@pytest.mark.parametrize("text", [
    "ERROR: [youtube] QdBZY2fkU-0: Sign in to confirm you're not a bot. This helps protect our community.",
    "ERROR: [youtube] abc: Sign in to confirm you’re not a bot",
    "yt_dlp failed (exit 1):\nERROR: [youtube] abc: Unable to extract yt initial data",
    "ERROR: unable to download video data: HTTP Error 403: Forbidden",
    "ERROR: [youtube] abc: Requested format is not available. Use --list-formats",
    "WARNING: [youtube] abc: nsig extraction failed: Some formats may be missing",
    "ERROR: Unsupported URL: https://www.youtube.com/shorts/xyz",
    "ERROR: [youtube] abc: Precondition check failed. (caused by HTTPError 400)",
])
def test_classifier_matches_known_signatures(text):
    assert health.looks_like_outdated_downloader(text)


@pytest.mark.parametrize("text", [
    "", None, "Interrupted — retry", "OSError: No space left on device", "ToolError: ffmpeg timed out after 600s",
    "ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader",
    "ERROR: [youtube] abc: This video is private",
    "HTTP Error 429: Too Many Requests", "ConnectionError: Max retries exceeded",
    "RuntimeError: The caption model could not load",
])
def test_classifier_ignores_other_failures(text):
    assert not health.looks_like_outdated_downloader(text)
    assert not health.looks_like_outdated_downloader(text, video_age_days=0.5)


def test_not_available_counts_only_for_fresh_videos():
    err = "ERROR: [youtube] abc: This video is not available"
    assert health.looks_like_outdated_downloader(err, video_age_days=0.2)
    assert not health.looks_like_outdated_downloader(err, video_age_days=5)
    assert not health.looks_like_outdated_downloader(err)                       # age unknown: assume it is really gone


# ---------------------------------------------------------------- PyPI check and cache

def test_latest_yt_dlp_is_cached_for_six_hours(hx, monkeypatch):
    import requests
    calls = []

    def fake_get(url, timeout=None, **kw):
        calls.append((url, timeout))
        return FakeResp("2099.1.1")
    monkeypatch.setattr(requests, "get", fake_get)
    first = health.latest_yt_dlp()
    assert first["version"] == "2099.1.1" and first["error"] is None
    assert calls == [(health.PYPI_URL, 10)]
    assert health.latest_yt_dlp()["version"] == "2099.1.1" and len(calls) == 1      # served from the cache
    health._pypi_cache["at"] = time.time() - health.PYPI_TTL_S - 1
    assert health.latest_yt_dlp()["version"] == "2099.1.1" and len(calls) == 2      # stale: fetched again
    assert health.latest_yt_dlp(force=True)["version"] == "2099.1.1" and len(calls) == 3


def test_latest_yt_dlp_failure_keeps_known_version_and_backs_off(hx, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp("2099.1.1"))
    assert health.latest_yt_dlp()["version"] == "2099.1.1"
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "get", boom)
    health._pypi_cache["at"] = 0.0
    r = health.latest_yt_dlp()
    assert r["version"] == "2099.1.1" and "offline" in r["error"]
    health.latest_yt_dlp()
    assert len(calls) == 1                                                            # failures are not retried on every call


def test_tool_versions_reports_update_available(hx, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp("2099.1.1"))
    monkeypatch.setattr(health, "yt_dlp_version", lambda: "2026.10.26")
    v = health.tool_versions()
    assert v["yt_dlp"] == {"installed": "2026.10.26", "latest": "2099.1.1", "update_available": True, "checked_at": v["yt_dlp"]["checked_at"], "error": None}
    names = [t["name"] for t in v["tools"]]
    assert names == ["yt-dlp", "faster-whisper", "imageio-ffmpeg", "TwitchDownloaderCLI"]
    assert v["tools"][0]["updatable"] and v["tools"][0]["update_available"]
    monkeypatch.setattr(health, "yt_dlp_version", lambda: "2099.1.1")
    assert not health.tool_versions()["yt_dlp"]["update_available"]
    monkeypatch.setattr(health, "yt_dlp_version", lambda: "2099.1.2")
    assert not health.tool_versions()["yt_dlp"]["update_available"]


def test_health_never_blocks_startup(hx, monkeypatch):
    """Registering the feature (what app startup does) must not touch PyPI; only a request for /api/health does."""
    import requests
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(a) or FakeResp())
    health.register()
    health.tool_versions(check_pypi=False)
    assert calls == [] and health._pypi_cache["at"] == 0.0
    health.tool_versions()
    assert len(calls) == 1


# ---------------------------------------------------------------- counting failures from job.failed

def _fail(error, job_type="scan_video", payload=None):
    events.emit("job.failed", job={"id": 1, "type": job_type, "payload": payload or {}}, error=error)


def test_job_failed_events_are_counted_and_stored(hx):
    _fail("yt_dlp failed:\nERROR: [youtube] abc: Sign in to confirm you're not a bot")
    _fail("OSError: No space left on device")
    _fail("ERROR: unable to download video data: HTTP Error 403: Forbidden", "render_clip", {"clip_id": 99})
    assert health.recent_failures() == 2
    rows = [dict(r) for r in db.execute("SELECT * FROM health_events ORDER BY id").fetchall()]
    assert [r["kind"] for r in rows] == ["download_failure", "download_failure"]
    assert rows[0]["detail"].startswith("scan_video: ERROR: [youtube] abc: Sign in")
    assert rows[1]["detail"].startswith("render_clip: ")


def test_not_available_uses_the_video_age(hx, campaign):
    old = db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "old", "url": "u",
                               "published_at": "2026-01-01T00:00:00+00:00", "status": "error"})
    new = db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "new", "url": "u",
                               "published_at": db.now(), "status": "error"})
    _fail("ERROR: [youtube] old: This video is not available", payload={"video_id": old})
    assert health.recent_failures() == 0
    _fail("ERROR: [youtube] new: This video is not available", payload={"video_id": new})
    assert health.recent_failures() == 1


def test_old_failures_and_failures_before_an_update_do_not_count(hx):
    db.execute("INSERT INTO health_events (t, kind, detail) VALUES ('2020-01-01T00:00:00+00:00', 'download_failure', 'ancient')")
    _fail("ERROR: Unsupported URL: x")
    assert health.recent_failures() == 1
    health.record_event("update", "yt-dlp a → b")
    assert health.recent_failures() == 0
    _fail("ERROR: Unsupported URL: y")
    assert health.recent_failures() == 1


# ---------------------------------------------------------------- verdict wording

def _versions(installed="2026.10.26", latest="2099.1.1", error=None):
    upd = bool(installed and latest and health._vtuple(latest) > health._vtuple(installed))
    return {"yt_dlp": {"installed": installed, "latest": latest, "update_available": upd, "error": error}, "tools": []}


def test_verdict_update_and_failures_is_actionable():
    v = health.verdict(_versions(), 3)
    assert v == {"text": "Downloads are failing in a way that usually means yt-dlp needs an update (3 in the last 24 h). Update now.",
                 "actionable": True, "level": "warn"}
    assert health.verdict(_versions(), 1)["actionable"]


def test_verdict_three_failures_without_known_update_is_actionable():
    v = health.verdict(_versions(latest="2026.10.26"), 3)
    assert v["actionable"] and v["text"].startswith("Downloads are failing in a way that usually means yt-dlp needs an update (3 in the last 24 h).")
    assert "newest release" in v["text"]
    v = health.verdict(_versions(latest=None, error="offline"), 4)
    assert v["actionable"] and v["text"].endswith("Update now.")


def test_verdict_quiet_cases():
    assert not health.verdict(_versions(latest="2026.10.26"), 2)["actionable"]
    v = health.verdict(_versions(), 0)
    assert v == {"text": "A newer yt-dlp is available (2026.10.26 → 2099.1.1). Downloads are working; update when convenient.", "actionable": False, "level": "info"}
    v = health.verdict(_versions(latest="2026.10.26"), 0)
    assert v == {"text": "Everything looks healthy.", "actionable": False, "level": "ok"}
    v = health.verdict(_versions(latest=None, error="ConnectionError: offline"), 0)
    assert not v["actionable"] and "Could not check PyPI" in v["text"] and "offline" in v["text"]
    assert "not installed" in health.verdict(_versions(installed=None, latest=None), 0)["text"]


# ---------------------------------------------------------------- jobs (no pip, no network)

def _events_rows():
    return [dict(r) for r in db.execute("SELECT * FROM health_events ORDER BY id").fetchall()]


def test_update_job_runs_pip_and_records_the_new_version(hx, monkeypatch):
    ran = {}

    def fake_run(cmd, timeout=None, on_line=None, cwd=None):
        ran["cmd"], ran["timeout"] = cmd, timeout
        for line in ("Collecting yt-dlp", "  Downloading yt_dlp-2099.1.1-py3-none-any.whl", "", "Successfully installed yt-dlp-2099.1.1"):
            on_line(line)
        return ""
    monkeypatch.setattr(tools, "run", fake_run)
    monkeypatch.setattr(health, "upgrade_commands", lambda: [[sys.executable, "-m", "pip", "install", "--upgrade", "--no-input", "yt-dlp"]])
    monkeypatch.setattr(health, "yt_dlp_version", lambda: "2026.10.26")
    monkeypatch.setattr(health, "_reread_yt_dlp_version", lambda: "2099.1.1")
    msgs = []
    health.update_yt_dlp({}, lambda frac, msg=None: msgs.append(msg))
    assert ran["cmd"] == [sys.executable, "-m", "pip", "install", "--upgrade", "--no-input", "yt-dlp"] and ran["timeout"] == 600
    assert "Collecting yt-dlp" in msgs and "Successfully installed yt-dlp-2099.1.1" in msgs and "" not in msgs
    assert msgs[-1] == "yt-dlp 2026.10.26 → 2099.1.1"
    ev = _events_rows()
    assert len(ev) == 1 and ev[0]["kind"] == "update" and ev[0]["detail"] == "yt-dlp 2026.10.26 → 2099.1.1"


def test_upgrade_commands_pip_then_uv_then_ensurepip(monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: object() if name == "pip" else real(name, *a))
    assert health.upgrade_commands() == [[sys.executable, "-m", "pip", "install", "--upgrade", "--no-input", "yt-dlp"]]
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "pip" else real(name, *a))
    monkeypatch.setattr(health, "_find_uv", lambda: "/x/uv")
    assert health.upgrade_commands() == [["/x/uv", "pip", "install", "--python", sys.executable, "--upgrade", "yt-dlp"]]
    monkeypatch.setattr(health, "_find_uv", lambda: None)
    cmds = health.upgrade_commands()
    assert cmds[0] == [sys.executable, "-m", "ensurepip", "--upgrade"] and cmds[1][2:4] == ["pip", "install"]


def test_update_job_runs_every_upgrade_command(hx, monkeypatch):
    ran = []
    monkeypatch.setattr(tools, "run", lambda cmd, timeout=None, on_line=None, cwd=None: ran.append((cmd, timeout)) or "")
    monkeypatch.setattr(health, "upgrade_commands", lambda: [["a"], ["b"]])
    monkeypatch.setattr(health, "_reread_yt_dlp_version", lambda: health.yt_dlp_version())
    health.update_yt_dlp({}, lambda *a: None)
    assert ran == [(["a"], 600), (["b"], 600)]
    assert _events_rows()[0]["detail"].endswith("was already the newest")


def test_update_job_failure_propagates(hx, monkeypatch):
    def fake_run(cmd, timeout=None, on_line=None, cwd=None):
        raise tools.ToolError("python failed (exit 1):\nERROR: No matching distribution found for yt-dlp")
    monkeypatch.setattr(tools, "run", fake_run)
    with pytest.raises(tools.ToolError):
        health.update_yt_dlp({}, lambda *a: None)
    assert _events_rows() == []


def test_selftest_records_pass_per_step(hx, monkeypatch):
    from clipdash import sources
    monkeypatch.setattr(sources, "video_info", lambda url: {"title": "Me at the zoo", "duration": 19} if url == health.SELFTEST_URL else None)

    def fake_run(cmd, timeout=None, on_line=None, cwd=None):
        assert cmd[0] == "ffmpeg" and "lavfi" in cmd and cmd[-1].endswith(".wav")
        with open(cmd[-1], "wb") as f:
            f.write(b"\0" * 96000)
        return ""
    monkeypatch.setattr(tools, "run", fake_run)
    monkeypatch.setattr(tools, "whisper_model_present", lambda name: False)
    msgs = []
    health.selftest({}, lambda frac, msg=None: msgs.append(msg))
    ev = _events_rows()
    assert len(ev) == 1 and ev[0]["kind"] == "selftest"
    rec = json.loads(ev[0]["detail"])
    assert rec["ok"] and [(s["name"], s["status"]) for s in rec["steps"]] == [("video_info", "ok"), ("ffmpeg", "ok"), ("whisper", "skipped")]
    assert rec["steps"][0]["detail"] == "Me at the zoo (0:19)"
    assert msgs[-1] == "Self-test passed"


def test_selftest_failing_step_is_recorded_and_raises(hx, monkeypatch):
    from clipdash import sources

    def bad_info(url):
        raise tools.ToolError("yt_dlp failed:\nERROR: [youtube] QdBZY2fkU-0: Sign in to confirm you're not a bot")
    monkeypatch.setattr(sources, "video_info", bad_info)

    def fake_run(cmd, timeout=None, on_line=None, cwd=None):
        with open(cmd[-1], "wb") as f:
            f.write(b"\0" * 10)                                                     # too short: ffmpeg step fails too
        return ""
    monkeypatch.setattr(tools, "run", fake_run)
    monkeypatch.setattr(tools, "whisper_model_present", lambda name: False)
    with pytest.raises(tools.ToolError) as e:
        health.selftest({}, lambda *a: None)
    assert "Self-test failed" in str(e.value) and "not a bot" in str(e.value)
    rec = json.loads(_events_rows()[0]["detail"])
    assert not rec["ok"] and [s["status"] for s in rec["steps"]] == ["failed", "failed", "skipped"]
    assert "expected about 96000" in rec["steps"][1]["detail"]


# ---------------------------------------------------------------- endpoints

@pytest.fixture()
def client(hx):
    from clipdash.app import app
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResp("2099.1.1"))
    monkeypatch.setattr(health, "yt_dlp_version", lambda: "2026.10.26")
    r = client.get("/api/health").json()
    assert r["update_available"] and r["failures_24h"] == 0 and not r["actionable"] and r["level"] == "info"
    assert r["yt_dlp"]["installed"] == "2026.10.26" and r["yt_dlp"]["latest"] == "2099.1.1"
    assert [v["name"] for v in r["versions"]][0] == "yt-dlp" and r["events"] == [] and r["selftest"] is None
    assert r["update_job"] is None and r["selftest_job"] is None
    _fail("ERROR: [youtube] abc: Unable to extract player version")
    r = client.get("/api/health").json()
    assert r["failures_24h"] == 1 and r["actionable"]
    assert r["verdict"] == "Downloads are failing in a way that usually means yt-dlp needs an update (1 in the last 24 h). Update now."
    assert len(r["events"]) == 1 and r["events"][0]["kind"] == "download_failure"


def test_health_endpoint_lists_last_ten_events_and_last_selftest(client):
    health.record_event("selftest", json.dumps({"ok": True, "steps": [{"name": "video_info", "label": "x", "status": "ok", "detail": ""}]}))
    for i in range(12):
        health.record_event("update", f"yt-dlp {i} → {i + 1}")
    r = client.get("/api/health").json()
    assert len(r["events"]) == 10 and r["events"][0]["detail"] == "yt-dlp 11 → 12"
    assert r["selftest"]["kind"] == "selftest" and r["selftest"]["steps"]["ok"] is True
    assert r["failures_24h"] == 0 and r["events"][0]["kind"] == "update"


def test_update_and_selftest_endpoints_enqueue_jobs_once(client):
    r = client.post("/api/health/update_yt_dlp").json()
    job = db.get("jobs", r["job_id"])
    assert job["type"] == "update_yt_dlp" and job["status"] == "queued" and not r["already_running"]
    again = client.post("/api/health/update_yt_dlp").json()
    assert again == {"job_id": r["job_id"], "already_running": True}
    assert client.get("/api/health").json()["update_job"] == r["job_id"]
    s = client.post("/api/health/selftest").json()
    assert db.get("jobs", s["job_id"])["type"] == "health_selftest" and not s["already_running"]
    assert client.post("/api/health/selftest").json()["already_running"]
    assert jobs.HANDLERS["update_yt_dlp"] is health.update_yt_dlp and jobs.HANDLERS["health_selftest"] is health.selftest
    assert "/static/features/health.js" in client.get("/api/features").json()
