"""API behaviour with dry-run publishers: approval gating, posting rules, submission timer."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(fresh_db):
    from clipdash.app import app
    with TestClient(app) as c:
        yield c


def make_clip(db, campaign, **over):
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "abc", "url": "u",
                               "published_at": "2026-11-19T00:00:00+00:00", "status": "done"})
    data = {"video_id": vid, "campaign_id": campaign["id"], "path": "/nonexistent.mp4", "source_start": 0, "source_length": 90,
            "duration": 80.0, "trim_start": 0, "trim_end": 70.0, "width": 1080, "height": 1920, "status": "pending", "hook": "LACY loses it.",
            "post_text": {"youtube": {"title": "LACY loses it #Shorts", "text": "LACY loses it\n\n#LACY #gta6"},
                          "tiktok": {"title": "", "text": "LACY loses it #LACY #gta6"}, "instagram": {"title": "", "text": "LACY loses it #LACY"}},
            "checklist": {"0": True, "1": True, "2": True, "3": True}}
    data.update(over)
    return db.insert("clips", data)


def test_approve_blocked_when_rule_fails(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign, trim_end=50.0)
    r = client.post(f"/api/clips/{cid}/approve", json={"checklist": {"0": True, "1": True, "2": True, "3": True}})
    assert r.status_code == 409 and any(f["rule"] == "length" for f in r.json()["failed"])


def test_approve_blocked_without_checklist(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign, checklist={})
    assert client.post(f"/api/clips/{cid}/approve", json={"checklist": {"0": True}}).status_code == 409


def test_approve_ok_and_post_dry_run_starts_submission_timer(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign)
    r = client.post(f"/api/clips/{cid}/approve", json={"checklist": {"0": True, "1": True, "2": True, "3": True}})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    acc = client.post("/api/accounts", json={"platform": "youtube", "route": "assisted", "label": "yt"}).json()
    posts = client.post("/api/posts", json={"clip_id": cid, "account_ids": [acc["id"]], "mode": "now"}).json()
    assert posts[0]["status"] == "publishing"
    live = client.post(f"/api/posts/{posts[0]['id']}/mark_live", json={"url": "https://youtube.com/shorts/x"}).json()
    assert live["status"] == "live" and live["submission_deadline"] and live["posted_at"]
    assert client.get(f"/api/clips/{cid}").json()["status"] == "posted"
    assert client.post(f"/api/posts/{posts[0]['id']}/submitted").json()["submitted_at"]
    assert client.delete(f"/api/posts/{posts[0]['id']}").status_code == 409


def test_cannot_post_same_platform_twice_or_disallowed_platform(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign, status="approved")
    a1 = client.post("/api/accounts", json={"platform": "tiktok", "route": "assisted"}).json()
    a2 = client.post("/api/accounts", json={"platform": "tiktok", "route": "assisted", "label": "second"}).json()
    r = client.post("/api/posts", json={"clip_id": cid, "account_ids": [a1["id"], a2["id"]], "mode": "now"})
    assert r.status_code == 400 and "one tiktok" in r.json()["message"].lower()
    fresh_db.update("campaigns", campaign["id"], {"platforms": ["youtube"]})
    r = client.post("/api/posts", json={"clip_id": cid, "account_ids": [a1["id"]], "mode": "now"})
    assert r.status_code == 400 and "does not allow" in r.json()["message"]


def test_post_text_without_required_tag_is_refused(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign, status="approved")
    a = client.post("/api/accounts", json={"platform": "tiktok", "route": "assisted"}).json()
    r = client.post("/api/posts", json={"clip_id": cid, "account_ids": [a["id"]], "mode": "now", "post_text": {str(a["id"]): "LACY no tag"}})
    assert r.status_code == 400 and "#LACY" in r.json()["message"]


def test_schedule_converts_timezone(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign, status="approved")
    a = client.post("/api/accounts", json={"platform": "instagram", "route": "assisted"}).json()
    r = client.post("/api/posts", json={"clip_id": cid, "account_ids": [a["id"]], "mode": "schedule", "scheduled_at": "2030-01-01T12:00", "tz": "America/New_York"})
    assert r.status_code == 200 and r.json()[0]["scheduled_at"] == "2030-01-01T17:00:00+00:00"


def test_tiktok_direct_requires_privacy_choice(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign, status="approved")
    a = client.post("/api/accounts", json={"platform": "tiktok", "route": "direct"}).json()
    r = client.post("/api/posts", json={"clip_id": cid, "account_ids": [a["id"]], "mode": "now"})
    assert r.status_code == 400 and "choose" in r.json()["message"].lower()


def test_posted_clip_is_locked(client, fresh_db, campaign):
    cid = make_clip(fresh_db, campaign, status="posted")
    assert client.put(f"/api/clips/{cid}", json={"hook": "x"}).status_code == 400
    assert client.delete(f"/api/clips/{cid}").status_code == 409
