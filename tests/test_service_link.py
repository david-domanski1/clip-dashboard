"""Service posts: 'published' without a platform link stays live, is polled for the link, never goes negative on the countdown side."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from clipdash.publish.base import PublishResult


@pytest.fixture()
def client(fresh_db):
    from clipdash.app import app
    with TestClient(app) as c:
        yield c


def _post(fresh_db, campaign, **over):
    acc = fresh_db.insert("accounts", {"platform": "tiktok", "route": "service", "label": "tt", "handle": "vicecity", "status": "connected", "external_id": "svc1"})
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "v1", "url": "u", "status": "done"})
    cid = fresh_db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "source_start": 0, "source_length": 90, "status": "posted", "hook": "h"})
    row = {"clip_id": cid, "campaign_id": campaign["id"], "account_id": acc, "platform": "tiktok", "route": "service", "status": "live",
           "external_id": "6aff80c371696782856dabce", "posted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
           "submission_deadline": (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0).isoformat(), "post_text": "x"}
    row.update(over)
    return fresh_db.insert("posts", row)


def test_published_without_link_is_live_and_fetching():
    from clipdash.publish.service import ServiceTikTok
    pub = ServiceTikTok({"id": 1, "platform": "tiktok", "route": "service"})
    res = pub._from_post({"status": "published", "platforms": [{"platform": "tiktok", "status": "published", "platformPostUrl": None}]}, "abc", scheduled=False)
    assert res.status == "live" and res.url is None and "fetching link" in res.detail
    res = pub._from_post({"platforms": [{"platform": "tiktok", "status": "published", "platformPostUrl": "https://www.tiktok.com/@v/video/1"}]}, "abc", scheduled=False)
    assert res.status == "live" and res.url.endswith("/video/1")


def test_scheduler_polls_live_service_post_until_link_arrives(client, fresh_db, campaign, monkeypatch):
    from clipdash import jobs
    from clipdash.publish import runner, scheduler
    pid = _post(fresh_db, campaign)
    p = client.get(f"/api/posts?campaign_id={campaign['id']}").json()[0]
    assert p["link_state"] == "pending" and p["profile_url"] == "https://www.tiktok.com/@vicecity" and p["missed"] is False
    scheduler._link_checks.clear()
    scheduler.tick()
    job = fresh_db.one("jobs", "type='check_post' AND status='queued'")
    assert job and job["payload"]["post_id"] == pid
    scheduler.tick()   # within 30 s: no second job
    assert fresh_db.execute("SELECT COUNT(*) c FROM jobs WHERE type='check_post'").fetchone()["c"] == 1, fresh_db.rows("jobs")
    # the service now returns the link; a later 'pending' status must never demote the live post
    monkeypatch.setattr(runner, "get_publisher", lambda a: type("P", (), {"check": lambda self, post: PublishResult(status="live", url="https://www.tiktok.com/@vicecity/video/77", detail="Published by the service")})())
    runner.check_post(pid, lambda *a: None)
    post = fresh_db.get("posts", pid)
    assert post["url"].endswith("/video/77") and post["status"] == "live" and post["submission_deadline"]
    assert client.get(f"/api/posts?campaign_id={campaign['id']}").json()[0]["link_state"] is None


def test_link_poll_gives_up_after_window_and_missed_flag(client, fresh_db, campaign):
    from clipdash.publish import scheduler
    old = (datetime.now(timezone.utc) - timedelta(minutes=40)).replace(microsecond=0).isoformat()
    pid = _post(fresh_db, campaign, posted_at=old, submission_deadline=(datetime.now(timezone.utc) - timedelta(minutes=10)).replace(microsecond=0).isoformat())
    scheduler.tick()
    assert fresh_db.execute("SELECT COUNT(*) c FROM jobs WHERE type='check_post'").fetchone()["c"] == 0
    p = client.get(f"/api/posts?campaign_id={campaign['id']}").json()[0]
    assert p["link_state"] == "gave_up" and p["missed"] is True and p["state_detail"] == "No link from the service"
    # the link can be pasted by hand without restarting the submission timer
    r = client.post(f"/api/posts/{pid}/mark_live", json={"url": "https://www.tiktok.com/@vicecity/video/9"}).json()
    assert r["url"].endswith("/video/9") and r["posted_at"] == old and r["missed"] is True
    assert client.post(f"/api/posts/{pid}/submitted").json()["submitted_at"]
