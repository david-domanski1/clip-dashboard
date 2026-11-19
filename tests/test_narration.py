"""Narrated intro: hook validation, the campaign lock, the disclosure flag, and workspace availability."""
import pytest
from fastapi.testclient import TestClient


def test_hook_validation():
    from clipdash import hooks
    ok, t = hooks.validate_hook("he had no idea the cops were already behind him")
    assert ok and t.endswith(".")
    assert not hooks.validate_hook("")[0]
    assert not hooks.validate_hook("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen")[0]
    assert not hooks.validate_hook("He had no fucking idea.")[0]
    assert not hooks.validate_hook("Watch this #gta6 moment")[0]
    assert hooks.parse_hooks('["He never saw the truck coming.", "Chat warned him. He ignored it.", "damn it all"]') == ["He never saw the truck coming.", "Chat warned him. He ignored it."]
    assert hooks.parse_hooks("1. The cops were already there.\n2. He thought the coast was clear.") == ["The cops were already there.", "He thought the coast was clear."]


def test_campaign_lock(fresh_db, campaign):
    from clipdash import db, rules
    assert rules.narration_allowed(campaign)[0] is False          # default off
    db.update("campaigns", campaign["id"], {"ai_narration": 1})
    assert rules.narration_allowed(db.get("campaigns", campaign["id"]))[0] is True
    db.update("campaigns", campaign["id"], {"bans_ai": 1})
    ok, why = rules.narration_allowed(db.get("campaigns", campaign["id"]))
    assert ok is False and "ban" in why
    ws = db.ensure_workspace()
    assert rules.narration_allowed(ws)[0] is True
    parsed = rules.parse_rules_text("No AI-generated content. No botting.")
    assert parsed["bans_ai"] == 1 and parsed["ai_narration"] == 0


def test_rule_results_carry_ai_voice_and_block_when_banned(fresh_db, campaign):
    from clipdash import db, rules
    clip = {"duration": 70.0, "width": 1080, "height": 1920, "watermark": 0, "ai_text": 1,
            "narration": {"enabled": True, "chosen": "He had no idea.", "intro_duration": 4.0},
            "post_text": {"tiktok": {"title": "", "text": "LACY loses it #LACY"}}}
    db.update("campaigns", campaign["id"], {"ai_narration": 1})
    res = rules.validate_clip(clip, db.get("campaigns", campaign["id"]), None)
    voice = next(r for r in res if r["rule"] == "ai_voice")
    assert voice["ok"] and "disclosure" in voice["detail"] and rules.all_pass(res)
    db.update("campaigns", campaign["id"], {"bans_ai": 1})
    res = rules.validate_clip(clip, db.get("campaigns", campaign["id"]), None)
    assert not next(r for r in res if r["rule"] == "ai_voice")["ok"]


def test_disclosure_flag_is_set_on_youtube_and_tiktok_bodies():
    from clipdash.publish.tiktok_direct import TikTokDirect
    from clipdash.publish.youtube_direct import YouTubeDirect
    post = {"title": "LACY #Shorts", "post_text": "LACY #LACY", "scheduled_at": None}
    assert YouTubeDirect.build_body(post, {"synthetic": True})["status"]["containsSyntheticMedia"] is True
    assert "containsSyntheticMedia" not in YouTubeDirect.build_body(post, {})["status"]
    assert TikTokDirect.build_post_info(post, {"synthetic": True}, "SELF_ONLY")["is_aigc"] is True
    assert TikTokDirect.build_post_info(post, {}, "SELF_ONLY")["is_aigc"] is False


@pytest.fixture()
def client(fresh_db):
    from clipdash.app import app
    with TestClient(app) as c:
        yield c


def test_api_locks_narration_and_sets_synthetic_on_posts(client, fresh_db, campaign, monkeypatch):
    from clipdash import db
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "x", "url": "u",
                               "published_at": "2026-11-19T00:00:00+00:00", "status": "done"})
    cid = db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "path": "/nonexistent.mp4", "source_start": 0, "source_length": 90,
                              "duration": 80.0, "trim_start": 0, "trim_end": 70.0, "width": 1080, "height": 1920, "status": "pending",
                              "post_text": {"youtube": {"title": "LACY #Shorts", "text": "LACY #LACY"}, "tiktok": {"title": "", "text": "LACY #LACY"}},
                              "checklist": {"0": True, "1": True, "2": True, "3": True}})
    # LACY bans AI → locked
    db.update("campaigns", campaign["id"], {"bans_ai": 1})
    r = client.put(f"/api/clips/{cid}/narration", json={"hook": "He had no idea.", "enabled": True})
    assert r.status_code == 400 and "ban" in r.json()["message"]
    r = client.post(f"/api/clips/{cid}/narration/hooks")
    assert r.status_code == 400
    # allowed campaign → enabling queues the intro render; posting carries the synthetic flag
    db.update("campaigns", campaign["id"], {"bans_ai": 0, "ai_narration": 1})
    monkeypatch.setattr("clipdash.jobs.enqueue", lambda *a, **k: 1)
    r = client.put(f"/api/clips/{cid}/narration", json={"hook": "he had no idea the cops were behind him", "enabled": True})
    assert r.status_code == 200 and r.json()["narration"]["enabled"] and r.json()["ai_text"] == 1
    assert any(x["rule"] == "ai_voice" and x["ok"] for x in r.json()["rule_results"])
    db.update("clips", cid, {"status": "approved", "narration": {**r.json()["narration"], "status": "ready", "intro_duration": 4.0}})
    acc = client.post("/api/accounts", json={"platform": "youtube", "route": "assisted"}).json()
    posts = client.post("/api/posts", json={"clip_id": cid, "account_ids": [acc["id"]], "mode": "now"}).json()
    assert posts[0]["options"]["synthetic"] is True
    # switching off restores the plain clip
    r = client.put(f"/api/clips/{cid}/narration", json={"enabled": False})
    assert r.status_code == 200 and not r.json()["narration"]["enabled"] and r.json()["ai_text"] == 0


def test_workspace_clip_can_use_narration(client, fresh_db):
    from clipdash import db
    ws = db.ensure_workspace()
    vid = db.insert("videos", {"campaign_id": ws["id"], "platform": "youtube", "external_id": "x", "url": "u", "status": "done"})
    cid = db.insert("clips", {"video_id": vid, "campaign_id": ws["id"], "path": "/nonexistent.mp4", "source_start": 0, "source_length": 90,
                              "duration": 40.0, "trim_start": 0, "trim_end": 40.0, "width": 1080, "height": 1920, "status": "pending",
                              "post_text": {"tiktok": {"title": "", "text": "Speed — clip from IShowSpeed (@IShowSpeed)"}}})
    import clipdash.jobs
    clipdash.jobs.enqueue = lambda *a, **k: 1
    r = client.put(f"/api/clips/{cid}/narration", json={"hook": "He walked straight into the ocean.", "enabled": True})
    assert r.status_code == 200 and r.json()["narration"]["enabled"]


def test_intro_duration_bounds():
    from clipdash import narration
    assert narration.intro_duration(1.0) == 3.0
    assert 3.0 < narration.intro_duration(2.7) < 4.0
    assert narration.intro_duration(9.0) == 6.0
