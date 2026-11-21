"""First-run wizard: the practice campaign and its demo video, the state endpoint, the Today/Delete path."""
import pytest
from fastapi.testclient import TestClient

from clipdash import db, rules, sources, tools
from clipdash.features import wizard

DEMO_INFO = {"title": "Grand Theft Auto VI Trailer 1", "channel": "Rockstar Games", "published_at": "2023-12-04T23:07:10+00:00",
             "duration": 90, "is_stream": 0, "live_now": False, "heatmap": [{"start_time": 30.0, "end_time": 31.0, "value": 1.0}],
             "chapters": [], "has_auto_subs": True, "has_live_chat": False}


@pytest.fixture()
def client(fresh_db, monkeypatch):
    monkeypatch.setattr(sources, "video_info", lambda url: dict(DEMO_INFO))
    from clipdash.app import app
    with TestClient(app) as c:
        yield c


def jobs_count():
    return db.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]


def test_demo_is_idempotent_and_never_queues_a_search(client):
    first = client.post("/api/wizard/demo").json()
    second = client.post("/api/wizard/demo").json()
    assert first["campaign"]["id"] == second["campaign"]["id"] and first["video"]["id"] == second["video"]["id"]
    assert db.execute("SELECT COUNT(*) c FROM campaigns WHERE name=?", (wizard.DEMO_NAME,)).fetchone()["c"] == 1
    assert db.execute("SELECT COUNT(*) c FROM videos").fetchone()["c"] == 1
    assert jobs_count() == 0, "the wizard UI starts the search itself; creating the demo must not"
    v = first["video"]
    assert v["status"] == "new" and v["url"] == wizard.DEMO_VIDEO_URL and v["duration"] == 90 and v["title"].startswith("Grand Theft Auto VI")
    assert first["clip_len"] == 61 and first["window"]["start"] > 0, "a window starting at 0 would skip the whole trailer (skip_start)"


def test_demo_campaign_is_practice_only(client):
    c = client.post("/api/wizard/demo").json()["campaign"]
    assert c["name"] == "Demo campaign (practice)" and c["creator"] == "Rockstar Games"
    assert c["required_names"] == ["Rockstar"] and c["required_hashtags"] == ["#GTA6"]
    assert (c["min_len"], c["max_len"]) == (61, 120) and c["platforms"] == ["youtube", "tiktok", "instagram"]
    assert c["submit_minutes"] == 0 and c["pay_model"] == "cpm" and c["rate_per_1k"] == 0
    assert c["checklist"] == rules.DEFAULT_CHECKLIST
    assert "practice" in c["rules_text"].lower() and "post" in c["rules_text"].lower()
    # It is an ordinary campaign to the rest of the app: listed, selectable, searchable, deletable.
    listed = [x for x in client.get("/api/campaigns").json() if x["id"] == c["id"]]
    assert listed and not listed[0]["workspace"] and listed[0]["counts"]["videos"] == 1
    assert client.get(f"/api/today?campaign_id={c['id']}").status_code == 200


def test_state_endpoint_follows_progress(client):
    st = client.get("/api/wizard/state").json()
    assert st == {**st, "setup_done": False, "wizard_done": False, "demo_campaign_id": None, "demo_video_id": None, "step": "tools"}
    assert st["demo"]["clip_len"] == 61 and st["demo"]["url"] == wizard.DEMO_VIDEO_URL
    r = client.post("/api/wizard/demo").json()
    st = client.get("/api/wizard/state").json()
    assert st["demo_campaign_id"] == r["campaign"]["id"] and st["demo_video_id"] == r["video"]["id"]
    assert st["demo_video_status"] == "new" and st["step"] == "clip" and st["has_own_campaign"] is False
    db.insert("clips", {"video_id": r["video"]["id"], "campaign_id": r["campaign"]["id"], "source_start": 30, "source_length": 61, "status": "pending"})
    assert client.get("/api/wizard/state").json()["step"] == "review"
    client.post("/api/setup/done")
    assert client.put("/api/settings", json={"wizard_done": True}).json()["wizard_done"] is True
    st = client.get("/api/wizard/state").json()
    assert st["setup_done"] is True and st["wizard_done"] is True
    assert db.all_settings()["wizard_done"] is True


def test_demo_video_search_uses_a_window_inside_the_trailer(client):
    """The wizard posts the suggested window to /rescan; the video goes to the queue with a range window and clip_len 61."""
    r = client.post("/api/wizard/demo").json()
    assert jobs_count() == 0
    res = client.post(f"/api/videos/{r['video']['id']}/rescan", json={"window": r["window"], "clip_len": r["clip_len"], "lead_in": r["lead_in"]})
    assert res.status_code == 200 and jobs_count() == 1
    v = db.get("videos", r["video"]["id"])
    assert v["status"] == "queued" and v["scan_window"]["kind"] == "range" and v["scan_window"]["start"] == 1.0
    assert v["scan_window"]["end"] is None and v["scan_window"]["clip_len"] == 61.0 and v["scan_window"]["lead_in"] == 10.0
    sec = client.get(f"/api/videos/{v['id']}/section").json()
    assert sec["clip_len"] == 61 and sec["status"] == "queued"


def test_demo_survives_no_network_and_can_be_deleted(fresh_db, monkeypatch):
    def offline(url):
        raise tools.ToolError("yt-dlp failed: no network")
    monkeypatch.setattr(sources, "video_info", offline)
    from clipdash.app import app
    with TestClient(app) as client:
        r = client.post("/api/wizard/demo")
        assert r.status_code == 200
        v = r.json()["video"]
        assert v["status"] == "new" and v["duration"] is None and "details unavailable" in v["stage"]
        cid = r.json()["campaign"]["id"]
        assert client.delete(f"/api/campaigns/{cid}").json() == {"ok": True}
        assert db.get("videos", v["id"]) is None, "deleting the practice campaign removes its video too"
        st = client.get("/api/wizard/state").json()
        assert st["demo_campaign_id"] is None and st["step"] == "tools"


def test_practice_card_condition():
    assert wizard.is_practice({"id": 3, "name": wizard.DEMO_NAME, "kind": "campaign"})
    assert not wizard.is_practice({"id": 3, "name": "LACY GTA VI", "kind": "campaign"})
    assert not wizard.is_practice({"id": 3, "name": wizard.DEMO_NAME, "kind": "streamer"})
    assert not wizard.is_practice(None)


def test_demo_clip_fits_the_trailer():
    from clipdash import pipeline
    start, length, _ = pipeline.clip_bounds(80, wizard.DEMO_CLIP_LEN, wizard.DEMO_LEAD_IN, duration=90)
    assert start >= 0 and start + length <= 90
