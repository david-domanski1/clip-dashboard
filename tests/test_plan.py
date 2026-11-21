"""Plans and licensing: free limits (workspace excluded), 402 payload, DEV keys, store activation, offline grace."""
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from clipdash import db
from clipdash.features import plan


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


@pytest.fixture()
def client(fresh_db, monkeypatch):
    monkeypatch.delenv("CLIPDASH_PLAN", raising=False)
    plan.write_license(None)
    plan.last_402.update({"at": None, "feature": None, "message": None})
    monkeypatch.setattr(plan.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected network call")))
    from clipdash.app import app
    with TestClient(app) as c:
        yield c
    plan.write_license(None)


def _clip(campaign_id: int) -> int:
    vid = db.insert("videos", {"campaign_id": campaign_id, "platform": "youtube", "external_id": "abc", "url": "u",
                               "published_at": "2026-11-19T00:00:00+00:00", "status": "done"})
    return db.insert("clips", {"video_id": vid, "campaign_id": campaign_id, "path": "/nonexistent.mp4", "source_start": 0,
                               "source_length": 90, "duration": 80.0, "trim_start": 0, "trim_end": 70.0, "width": 1080,
                               "height": 1920, "status": "pending", "hook": "LACY loses it.", "narration": {"chosen": "LACY loses it."}})


def test_free_plan_counts_campaigns_without_the_workspace(client, fresh_db):
    p = client.get("/api/plan").json()
    assert p["plan"] == "free" and p["license"] is None and p["limit_hit"] is None
    assert p["limits"]["campaigns"] == {"used": 0, "allowed": 1, "ok": True}      # the Streamer clips workspace row exists already
    assert client.post("/api/campaigns", json={"name": "A", "creator": "A"}).status_code == 200
    assert client.get("/api/plan").json()["limits"]["campaigns"]["used"] == 1


def test_second_campaign_is_a_402_with_a_plain_message(client, fresh_db, campaign):
    r = client.post("/api/campaigns", json={"name": "B", "creator": "B"})
    assert r.status_code == 402
    body = r.json()
    assert body["feature"] == "campaigns" and body["message"] == plan.UPGRADE_MESSAGES["campaigns"]
    assert "Settings → Plan" in body["message"] and "upgrade_url" in body
    hit = client.get("/api/plan").json()["limit_hit"]
    assert hit and hit["feature"] == "campaigns" and hit["message"] == body["message"]
    assert fresh_db.execute("SELECT COUNT(*) c FROM campaigns WHERE kind IS NULL OR kind != 'streamer'").fetchone()["c"] == 1


@pytest.fixture(autouse=True)
def _no_plan_override(monkeypatch):
    monkeypatch.delenv("CLIPDASH_PLAN", raising=False)


def test_plan_override_env(fresh_db, monkeypatch):
    plan.write_license(None)
    monkeypatch.setenv("CLIPDASH_PLAN", "pro")
    assert plan.current_plan() == "pro"
    plan.require("narration")                       # no exception
    monkeypatch.setenv("CLIPDASH_PLAN", "free")
    assert plan.current_plan() == "free"
    monkeypatch.setenv("CLIPDASH_PLAN", "bogus")
    assert plan.current_plan() == "free"            # ignored: falls back to the (absent) licence


def test_limit_hit_card_expires_after_a_day():
    plan.last_402.update({"at": datetime.now(timezone.utc) - timedelta(hours=25), "feature": "campaigns", "message": "x"})
    assert plan.limit_hit_recently() is None
    plan.last_402.update({"at": datetime.now(timezone.utc) - timedelta(hours=2)})
    assert plan.limit_hit_recently()["feature"] == "campaigns"


def test_direct_posting_and_narration_are_pro(client, fresh_db):
    assert client.post("/api/accounts", json={"platform": "youtube", "route": "assisted"}).status_code == 200
    r = client.post("/api/accounts", json={"platform": "youtube", "route": "direct"})
    assert r.status_code == 402 and r.json()["feature"] == "direct_posting"
    ws = fresh_db.ensure_workspace()
    cid = _clip(ws["id"])
    r = client.put(f"/api/clips/{cid}/narration", json={"enabled": True})
    assert r.status_code == 402 and r.json()["feature"] == "narration"
    assert client.put(f"/api/clips/{cid}/narration", json={"enabled": False, "hook": "LACY loses it."}).status_code == 200


def test_dev_key_activates_pro_locally(client, fresh_db, campaign):
    r = client.post("/api/plan/activate", json={"key": "DEV-local-demo"})
    assert r.status_code == 200
    p = r.json()
    assert p["plan"] == "pro" and p["license"]["dev"] is True and p["license"]["key_masked"] == "DEV-…demo"
    assert "DEV-local-demo" not in str(p)
    assert p["limits"]["campaigns"]["allowed"] is None and p["grace_days_left"] is None
    assert client.post("/api/campaigns", json={"name": "B", "creator": "B"}).status_code == 200
    assert client.post("/api/accounts", json={"platform": "tiktok", "route": "direct", "label": "t"}).status_code == 200
    if os.name == "posix":
        assert stat.S_IMODE(plan.license_path().stat().st_mode) == 0o600
    assert client.post("/api/plan/validate").json()["plan"] == "pro"        # no network call for a DEV key
    assert client.post("/api/plan/deactivate").json()["plan"] == "free"
    assert not plan.license_path().exists()


def test_dev_keys_can_be_switched_off(client, fresh_db, monkeypatch):
    fresh_db.set_setting("license_dev_keys", False)
    monkeypatch.setattr(plan.requests, "post", lambda *a, **k: FakeResponse({"activated": False, "error": "license_key not found"}, 404))
    r = client.post("/api/plan/activate", json={"key": "DEV-nope"})
    assert r.status_code == 400 and "not recognised" in r.json()["message"]


def test_store_activation_checks_product_and_masks_email(client, fresh_db, monkeypatch):
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append((url.rsplit("/", 1)[1], dict(data)))
        if url.endswith("/activate"):
            return FakeResponse({"activated": True, "error": None,
                                 "license_key": {"id": 1, "status": "active", "key": data["license_key"], "activation_limit": 2,
                                                 "activation_usage": 1, "created_at": "2026-11-01T00:00:00.000000Z", "expires_at": "2027-11-01T00:00:00.000000Z"},
                                 "instance": {"id": "inst-uuid", "name": data["instance_name"], "created_at": "2026-11-21T00:00:00.000000Z"},
                                 "meta": {"store_id": 111, "product_id": 222, "customer_email": "jane@example.com", "customer_name": "Jane"}})
        return FakeResponse({"deactivated": True, "error": None})

    monkeypatch.setattr(plan.requests, "post", fake_post)
    fresh_db.set_setting("license_store_id", "111")
    fresh_db.set_setting("license_product_id", "999")
    r = client.post("/api/plan/activate", json={"key": "ABCD1234-KEY"})
    assert r.status_code == 400 and "different product" in r.json()["message"]
    assert [c[0] for c in calls] == ["activate", "deactivate"] and calls[1][1]["instance_id"] == "inst-uuid"
    assert client.get("/api/plan").json()["plan"] == "free"

    fresh_db.set_setting("license_product_id", "222")
    p = client.post("/api/plan/activate", json={"key": "ABCD1234-KEY"}).json()
    assert calls[-1][1] == {"license_key": "ABCD1234-KEY", "instance_name": calls[-1][1]["instance_name"]}
    assert calls[-1][1]["instance_name"].startswith("Clip Dashboard on ")
    assert p["plan"] == "pro" and p["license"]["email_masked"] == "j…@example.com" and p["license"]["key_masked"] == "ABCD…-KEY"
    assert p["license"]["valid_until"].startswith("2027-11-01") and p["grace_days_left"] == plan.GRACE_DAYS
    assert plan.read_license()["instance_id"] == "inst-uuid"


def test_activation_errors_are_plain(client, fresh_db, monkeypatch):
    monkeypatch.setattr(plan.requests, "post", lambda *a, **k: FakeResponse({"activated": False, "error": "This license key has reached the activation limit."}, 400))
    r = client.post("/api/plan/activate", json={"key": "FULL-KEY"})
    assert r.status_code == 400 and "already in use" in r.json()["message"]
    monkeypatch.setattr(plan.requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down")))
    r = client.post("/api/plan/activate", json={"key": "SOME-KEY"})
    assert r.status_code == 503 and "connection" in r.json()["message"].lower()
    assert client.post("/api/plan/activate", json={"key": "  "}).status_code == 400


def _pro_license(days_ago: float, **over):
    now = datetime.now(timezone.utc)
    d = {"key": "REAL-KEY-1234", "plan": "pro", "email_masked": "j…@x.com", "activated_at": (now - timedelta(days=40)).isoformat(),
         "last_validated_at": (now - timedelta(days=days_ago)).isoformat(), "valid_until": None, "instance_id": "inst-1", "status": "active"}
    d.update(over)
    plan.write_license(d)


def test_offline_grace_keeps_pro_for_14_days_then_falls_back(client, fresh_db, monkeypatch):
    offline = lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("offline"))  # noqa: E731
    monkeypatch.setattr(plan.requests, "post", offline)
    _pro_license(days_ago=0.5)
    p = client.get("/api/plan").json()                       # fresh enough: no re-validation attempted
    assert p["plan"] == "pro" and p["grace_days_left"] == 14
    _pro_license(days_ago=12.5)
    p = client.get("/api/plan").json()                       # stale: re-validation tried, offline, grace still running
    assert p["plan"] == "pro" and p["grace_days_left"] == 2
    assert client.post("/api/plan/validate").json()["validation"] == "offline"
    _pro_license(days_ago=14.5)
    p = client.get("/api/plan").json()
    assert p["plan"] == "free" and p["grace_days_left"] == 0 and p["license"]["key_masked"] == "REAL…1234"
    assert client.post("/api/campaigns", json={"name": "A", "creator": "A"}).status_code == 200
    assert client.post("/api/campaigns", json={"name": "B", "creator": "B"}).status_code == 402


def test_successful_validation_resets_grace_and_invalid_key_drops_to_free(client, fresh_db, monkeypatch):
    _pro_license(days_ago=10)
    body = {"valid": True, "error": None, "license_key": {"status": "active", "expires_at": "2027-01-01T00:00:00Z"},
            "instance": {"id": "inst-1"}, "meta": {"store_id": 1, "product_id": 2, "customer_email": "jane@example.com"}}
    seen = []
    monkeypatch.setattr(plan.requests, "post", lambda url, data=None, **k: (seen.append(dict(data)), FakeResponse(body))[1])
    r = client.post("/api/plan/validate").json()
    assert r["validation"] == "ok" and r["grace_days_left"] == 14 and r["license"]["valid_until"].startswith("2027-01-01")
    assert seen[-1] == {"license_key": "REAL-KEY-1234", "instance_id": "inst-1"}
    monkeypatch.setattr(plan.requests, "post", lambda *a, **k: FakeResponse({"valid": False, "error": "This license key has expired.", "license_key": {"status": "expired"}}, 400))
    r = client.post("/api/plan/validate").json()
    assert r["validation"] == "invalid" and r["plan"] == "free" and "expired" in r["license"]["last_error"]
    assert plan.read_license()["plan"] == "free"


def test_require_unknown_feature_is_a_programming_error(fresh_db):
    with pytest.raises(ValueError):
        plan.require("teleport")
    plan.write_license(None)
    with pytest.raises(Exception) as ei:
        plan.require("analytics")
    assert ei.value.status_code == 402 and ei.value.detail["feature"] == "analytics"


def test_masking():
    assert plan.mask_key("ABCDEFGH-1234") == "ABCD…1234" and plan.mask_key("short") == "short" and plan.mask_key(None) == ""
    assert plan.mask_email("jane.doe@example.com") == "j…@example.com" and plan.mask_email("nope") == ""


def test_free_plan_gates_earnings_analytics(fresh_db, campaign, monkeypatch):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    monkeypatch.setenv("CLIPDASH_PLAN", "free")
    with TestClient(app) as c:
        r = c.get(f"/api/analytics/summary?campaign_id={campaign['id']}")
        assert r.status_code == 402 and r.json()["feature"] == "analytics"
        assert c.get(f"/api/analytics/today?campaign_id={campaign['id']}").json() == {"live_posts": 0}
    monkeypatch.setenv("CLIPDASH_PLAN", "pro")
    with TestClient(app) as c:
        assert c.get(f"/api/analytics/summary?campaign_id={campaign['id']}").status_code == 200
