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


def test_workspace_clip_can_use_narration(client, fresh_db, monkeypatch):
    from clipdash import db
    ws = db.ensure_workspace()
    vid = db.insert("videos", {"campaign_id": ws["id"], "platform": "youtube", "external_id": "x", "url": "u", "status": "done"})
    cid = db.insert("clips", {"video_id": vid, "campaign_id": ws["id"], "path": "/nonexistent.mp4", "source_start": 0, "source_length": 90,
                              "duration": 40.0, "trim_start": 0, "trim_end": 40.0, "width": 1080, "height": 1920, "status": "pending",
                              "post_text": {"tiktok": {"title": "", "text": "Speed — clip from IShowSpeed (@IShowSpeed)"}}})
    monkeypatch.setattr("clipdash.jobs.enqueue", lambda *a, **k: 1)
    r = client.put(f"/api/clips/{cid}/narration", json={"hook": "He walked straight into the ocean.", "enabled": True})
    assert r.status_code == 200 and r.json()["narration"]["enabled"]


def test_intro_duration_bounds():
    from clipdash import narration
    assert narration.intro_duration(1.0) == 3.0
    assert 3.0 < narration.intro_duration(2.7) < 4.0
    assert narration.intro_duration(9.0) == 6.0


def test_words_are_timed_to_the_voice_and_written_as_karaoke(tmp_path):
    from clipdash import narration
    # one-to-one when Whisper found every word
    spoken = [[0.1, 0.4, "He"], [0.45, 0.7, "had"], [0.75, 0.9, "no"], [0.95, 1.4, "idea."]]
    words = narration.align_words("He had no idea.", spoken, 1.6)
    assert [w[2] for w in words] == ["He", "had", "no", "idea."] and words[0][0] == 0.1 and words[-1][1] == 1.4
    # spread by letter count between the first and last spoken word when the counts differ
    words = narration.align_words("He had no idea.", [[0.2, 0.5, "he had"], [0.6, 1.4, "no idea"]], 1.6)
    assert len(words) == 4 and words[0][0] == 0.2 and abs(words[-1][1] - 1.4) < 0.01 and words[3][1] - words[3][0] > words[2][1] - words[2][0]
    # no transcript at all: spread over the voice length
    words = narration.align_words("He had no idea.", [], 2.0)
    assert words[0][0] == 0.0 and abs(words[-1][1] - 2.0) < 0.01
    ass = narration.write_intro_ass("He had no idea.", tmp_path / "i.ass", words).read_text()
    assert "Style: Cap,Arial,78" in ass and "HE " in ass and "IDEA." in ass       # clip caption style
    starts = [int(x) for x in __import__("re").findall(r"\\t\((\d+),", ass)]
    assert len(starts) == 4 and starts[0] == 300 and starts == sorted(starts)     # each word appears at its spoken time, after the 0.3 s voice delay
    assert ass.count("\\alpha&HFF&") == 4                                       # every word starts invisible (fill, outline and shadow)
    plain = narration.write_intro_ass("Hook", tmp_path / "p.ass").read_text()
    assert "\\fad" in plain and "\\alpha" not in plain


def test_voice_engine_auto_order_follows_saved_keys(fresh_db, monkeypatch):
    from clipdash import secrets_store, tts
    monkeypatch.setattr(tts, "piper_installed", lambda: ["en_US-ryan-high"])
    monkeypatch.setattr(secrets_store, "has", lambda k: False)
    assert tts.default_order()[0] == "piper"
    monkeypatch.setattr(secrets_store, "has", lambda k: k == "openai_api_key")
    assert tts.default_order()[:2] == ["openai", "piper"]
    monkeypatch.setattr(secrets_store, "has", lambda k: k in ("openai_api_key", "elevenlabs_api_key"))
    assert tts.default_order()[:3] == ["elevenlabs", "openai", "piper"]
    assert tts.piper_voice() == "en_US-ryan-high"
    fresh_db.set_setting("piper_voice", "en_GB-alan-medium")           # chosen but not installed: best installed wins
    assert tts.piper_voice() == "en_US-ryan-high"
    v = tts.voices()
    assert v["auto"] == "elevenlabs" and v["openai"]["current"] == "ash" and v["piper"]["voices"][0]["id"] == "en_US-ryan-high"


def test_sample_is_cached_on_disk(fresh_db, monkeypatch, tmp_path):
    from clipdash import tts
    calls = []
    def fake(text, out, voice=None):
        calls.append(voice); out.write_bytes(b"x" * 2000); return out
    monkeypatch.setitem(tts.ENGINES, "openai", fake)
    monkeypatch.setattr(tts, "SAMPLE_DIR", tmp_path)
    a = tts.sample("openai", "nova"); b = tts.sample("openai", "nova"); c = tts.sample("openai", "onyx")
    assert a == b and a != c and calls == ["nova", "onyx"]


def test_player_defaults_to_the_narrated_file(client, fresh_db, campaign, tmp_path):
    prev = tmp_path / "prev.mp4"; prev.write_bytes(b"0")
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "p1", "url": "u", "status": "done"})
    cid = fresh_db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "path": "/nonexistent.mp4", "source_start": 0, "source_length": 90, "duration": 80.0, "status": "pending",
                                    "narration": {"enabled": True, "status": "ready", "preview_path": str(prev), "intro_duration": 4.2}})
    c = client.get(f"/api/clips/{cid}").json()
    assert c["play_url"] == f"/media/clip/{cid}?narrated=1" and c["intro_offset"] == 4.2
    fresh_db.update("clips", cid, {"narration": {"enabled": False, "status": "ready", "preview_path": str(prev)}})
    c = client.get(f"/api/clips/{cid}").json()
    assert c["play_url"] == c["video_url"] and c["intro_offset"] == 0
