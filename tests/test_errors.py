"""Failures are stored raw and shown as one plain sentence; interrupted work is marked failed at startup."""
from clipdash import errors


def test_friendly_maps_known_causes_and_falls_back_to_first_line():
    assert "disk is full" in errors.friendly("OSError: [Errno 28] No space left on device")
    assert "took too long" in errors.friendly("ToolError: yt-dlp timed out after 600s")
    assert "incomplete" in errors.friendly("ToolError: TwitchDownloaderCLI archive was corrupt (123 bytes): EOFError")
    assert errors.friendly("Interrupted — retry") == errors.INTERRUPTED
    assert errors.friendly("") .startswith("It failed and no reason was recorded")
    assert errors.friendly("RuntimeError: Something odd happened\nline 2") == "Something odd happened"
    assert "reconnect" in errors.friendly("RuntimeError: YouTube token expired. Reconnect the account")


def test_suggest_fix_uses_platform_message():
    assert "Connect" in errors.suggest_fix("youtube", "direct", "YouTube account not connected. Click Connect on the account.")
    assert "quota" in errors.suggest_fix("youtube", "direct", "HttpError 403 quotaExceeded")
    assert "assisted" in errors.suggest_fix("tiktok", "service", "Service refused the post (400): {'error': 'bad media'}")
    assert errors.platform_message("Service refused the post (400): {'error': 'bad media'}") == "{'error': 'bad media'}"


def test_startup_marks_interrupted_work_failed(fresh_db, campaign):
    from clipdash import jobs
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "x", "url": "u", "status": "scanning"})
    vid2 = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "y", "url": "u", "status": "scanning"})
    cid = fresh_db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "source_start": 0, "source_length": 90, "status": "rendering"})
    cid2 = fresh_db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "source_start": 0, "source_length": 90, "status": "rendering"})
    running = fresh_db.insert("jobs", {"type": "render_clip", "payload": {"clip_id": cid}, "status": "running"})
    queued = fresh_db.insert("jobs", {"type": "render_clip", "payload": {"clip_id": cid2}, "status": "queued"})
    fresh_db.insert("jobs", {"type": "scan_video", "payload": {"video_id": vid2}, "status": "queued"})
    acc = fresh_db.insert("accounts", {"platform": "youtube", "route": "assisted", "label": "a"})
    pid = fresh_db.insert("posts", {"clip_id": cid, "campaign_id": campaign["id"], "account_id": acc, "platform": "youtube", "route": "assisted", "status": "publishing", "post_text": ""})
    n = jobs.mark_interrupted()
    assert n >= 3
    assert fresh_db.get("jobs", running)["status"] == "error" and fresh_db.get("jobs", running)["error"] == errors.INTERRUPTED
    assert fresh_db.get("jobs", queued)["status"] == "queued"                       # queued work still runs
    assert fresh_db.get("clips", cid)["status"] == "error" and fresh_db.get("clips", cid)["error"] == errors.INTERRUPTED
    assert fresh_db.get("clips", cid2)["status"] == "rendering"                     # has a queued job behind it
    assert fresh_db.get("videos", vid)["status"] == "error" and fresh_db.get("videos", vid2)["status"] == "scanning"
    assert fresh_db.get("posts", pid)["status"] == "failed"


def test_api_rows_carry_plain_error_text(fresh_db, campaign):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "x", "url": "u", "status": "error",
                                     "error": "ToolError: yt-dlp failed:\nERROR: HTTP Error 429: Too Many Requests"})
    cid = fresh_db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "source_start": 0, "source_length": 90, "status": "error", "error": "Interrupted — retry"})
    acc = fresh_db.insert("accounts", {"platform": "tiktok", "route": "service", "label": "a"})
    fresh_db.insert("posts", {"clip_id": cid, "campaign_id": campaign["id"], "account_id": acc, "platform": "tiktok", "route": "service", "status": "failed",
                              "error": "Service refused the post (400): {'error': 'bad media'}", "post_text": ""})
    with TestClient(app) as c:
        v = c.get(f"/api/videos?campaign_id={campaign['id']}").json()[0]
        assert "rate-limiting" in v["error_text"] and "429" in v["error"]
        assert c.get(f"/api/clips/{cid}").json()["error_text"] == errors.INTERRUPTED
        p = c.get(f"/api/posts?campaign_id={campaign['id']}").json()[0]
        assert p["platform_message"] == "{'error': 'bad media'}" and "assisted" in p["fix"]
        t = c.get(f"/api/today?campaign_id={campaign['id']}").json()
        assert any(a["reason"] == errors.INTERRUPTED for a in t["attention"])


def test_friendly_prefers_the_tool_error_line():
    assert "hasn't started" in errors.friendly("ToolError: python failed:\nERROR: [youtube] yX4DH_msPkI: This live event will begin in 26 minutes.\n")
    assert errors.friendly("ToolError: python failed:\nERROR: [youtube] abc: Something unusual happened\n") == "Something unusual happened"
    assert errors.friendly("ToolError: TwitchDownloaderCLI failed:\n") == "TwitchDownloaderCLI failed:"
