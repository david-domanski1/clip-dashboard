"""The Streamer clips workspace: no campaign rules, consent required, campaign guard, credit captions, no submissions."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(fresh_db):
    from clipdash.app import app
    with TestClient(app) as c:
        yield c


def workspace(db):
    return db.ensure_workspace()


def test_workspace_exists_and_is_protected(client, fresh_db):
    ws = [c for c in client.get("/api/campaigns").json() if c["workspace"]]
    assert len(ws) == 1 and ws[0]["name"].startswith("Streamer clips")
    assert client.delete(f"/api/campaigns/{ws[0]['id']}").status_code == 400


def test_workspace_clip_skips_campaign_rules(fresh_db):
    from clipdash import rules
    ws = workspace(fresh_db)
    clip = {"duration": 25.0, "width": 1080, "height": 1920, "watermark": 0, "ai_text": 0,
            "post_text": {"tiktok": {"title": "", "text": "Speed falls off the bike — clip from IShowSpeed's stream (@IShowSpeed) #gta6"}}}
    res = rules.validate_clip(clip, ws, {"published_at": "2020-01-01T00:00:00+00:00"})
    assert rules.all_pass(res)
    assert {r["rule"] for r in res} <= {"length", "format", "no_ai_text", "clean_text"}
    assert not any(r["rule"] in ("name", "hashtag", "source_date", "no_watermark") for r in res)
    assert not rules.all_pass(rules.validate_clip(dict(clip, width=1920, height=1080), ws, None))


def test_source_requires_consent_and_blocks_campaign_creators(client, fresh_db, campaign, monkeypatch):
    from clipdash import sources
    monkeypatch.setattr(sources, "resolve_channel", lambda kind, ident: {"kind": kind, "name": ident.lstrip("@"), "handle": ident, "external_id": "UC" + ident.lstrip("@"), "url": f"https://www.youtube.com/{ident}"})
    ws = workspace(fresh_db)
    r = client.post("/api/sources", json={"campaign_id": ws["id"], "url": "https://www.youtube.com/@IShowSpeed"})
    assert r.status_code == 400 and "allows clipping" in r.json()["message"]
    r = client.post("/api/sources", json={"campaign_id": ws["id"], "url": "https://www.youtube.com/@IShowSpeed", "clip_terms_ok": True,
                                          "terms_url": "https://example.com/speed-clipping", "check_now": False})
    assert r.status_code == 200 and r.json()["source"]["clip_terms_ok"] == 1 and r.json()["source"]["terms_url"].endswith("clipping")
    # LACY has an active campaign in the app → blocked in the workspace, pointed to the campaign
    r = client.post("/api/sources", json={"campaign_id": ws["id"], "url": "https://www.youtube.com/@Lacy", "clip_terms_ok": True, "check_now": False})
    assert r.status_code == 409 and r.json()["campaign_id"] == campaign["id"] and "LACY GTA VI" in r.json()["message"]


def test_workspace_captions_credit_streamer_by_name_and_handle(fresh_db):
    from clipdash import captions
    ws = workspace(fresh_db)
    src = {"name": "CaseOh", "handle": "caseoh_", "caption_template": None}
    pt = captions.build_all(ws, "He walked straight into the ocean.", ["lmao"], source=src)
    assert "CaseOh" in pt["tiktok"]["text"] and "@caseoh_" in pt["tiktok"]["text"]
    assert "#LACY" not in pt["tiktok"]["text"]
    src["caption_template"] = "{creator} moment: {hook} {handle}"
    pt = captions.build_all(ws, "He walked straight into the ocean.", [], source=src)
    assert pt["instagram"]["text"].startswith("CaseOh moment: He walked straight into the ocean. @caseoh_")


def test_workspace_post_has_no_submission_deadline(fresh_db):
    from clipdash import db
    from clipdash.publish import runner
    from clipdash.publish.base import PublishResult
    ws = workspace(fresh_db)
    vid = db.insert("videos", {"campaign_id": ws["id"], "platform": "youtube", "external_id": "x", "url": "u", "status": "done"})
    cid = db.insert("clips", {"video_id": vid, "campaign_id": ws["id"], "source_start": 0, "source_length": 90, "status": "approved"})
    aid = db.insert("accounts", {"platform": "tiktok", "label": "t", "route": "assisted"})
    pid = db.insert("posts", {"clip_id": cid, "account_id": aid, "campaign_id": ws["id"], "platform": "tiktok", "route": "assisted", "status": "manual"})
    runner.apply_result(db.get("posts", pid), PublishResult(status="live", url="https://t.example/1"))
    post = db.get("posts", pid)
    assert post["status"] == "live" and post["posted_at"] and post["submission_deadline"] is None
