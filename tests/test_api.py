"""API behaviour with dry-run publishers: approval gating, posting rules, submission timer."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(fresh_db):
    from clipdash.app import app
    with TestClient(app) as c:
        yield c


_n = [0]


def make_clip(db, campaign, **over):
    _n[0] += 1
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": f"abc{_n[0]}", "url": "u",
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


def test_today_checklist_counts_and_attention(client, fresh_db, campaign):
    pending = make_clip(fresh_db, campaign)
    make_clip(fresh_db, campaign, status="approved")
    broken = make_clip(fresh_db, campaign, status="error", error="ToolError: ffmpeg failed:\nsomething")
    stuck = make_clip(fresh_db, campaign, status="rendering")  # no job behind it → interrupted
    fresh_db.update("videos", fresh_db.get("clips", pending)["video_id"], {"status": "error", "error": "Scan failed"})
    sid = fresh_db.insert("sources", {"campaign_id": campaign["id"], "kind": "twitch_channel", "url": "https://twitch.tv/lacy", "external_id": "lacy", "name": "LACY", "watch": 1})
    fresh_db.insert("videos", {"campaign_id": campaign["id"], "source_id": sid, "platform": "twitch", "external_id": "live1", "url": "u2", "status": "scanning", "live": 1})
    t = client.get(f"/api/today?campaign_id={campaign['id']}").json()
    assert t["review"]["count"] == 1 and t["review"]["best"]["id"] == pending
    assert t["publish"]["count"] == 1
    assert t["sources"][0]["name"] == "LACY" and t["sources"][0]["live"] is True
    kinds = {(a["kind"], a["id"]): a for a in t["attention"]}
    assert kinds[("clip", broken)]["reason"] == "ToolError: ffmpeg failed:" and kinds[("clip", broken)]["retry"].endswith("/rerender")
    assert kinds[("clip", stuck)]["reason"].startswith("Interrupted")
    assert any(a["kind"] == "video" for a in t["attention"])
    # the live video is scanning with no job behind it, so it is also flagged as interrupted
    assert t["submissions"]["count"] == 0 and t["submissions"]["soonest"] is None


def test_source_no_facecam_is_remembered_and_clip_reports_it(client, fresh_db, campaign):
    sid = fresh_db.insert("sources", {"campaign_id": campaign["id"], "kind": "twitch_channel", "url": "https://twitch.tv/lacy", "external_id": "lacy", "name": "LACY"})
    cid = make_clip(fresh_db, campaign)
    fresh_db.update("videos", fresh_db.get("clips", cid)["video_id"], {"source_id": sid})
    c = client.get(f"/api/clips/{cid}").json()
    assert c["source"]["facecam_decided"] is False and c["rule_count"] == 0  # not validated until rendered
    r = client.put(f"/api/sources/{sid}", json={"facecam": {"none": True}, "layout": "full"}).json()
    assert r["facecam"] == {"none": True} and r["layout"] == "full"
    c = client.get(f"/api/clips/{cid}").json()
    assert c["source"]["facecam_decided"] is True and c["source"]["facecam"] is None
    assert client.put(f"/api/sources/{sid}", json={"facecam": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.3}}).json()["layout"] == "stacked"
