"""Campaign tracker and rule-change watch: baseline/no false change, change → diff + event with rules untouched, fetch
failures recorded, job registration and dedupe, tracker counts/payout/days_left/warnings, ack, PUT validation and the
stale-enqueue logic. Network is always mocked."""
from datetime import date, datetime, timedelta, timezone

import pytest

from clipdash import db as dbmod
from clipdash import events, jobs
from clipdash.features import campaigns as ct

PAGE_A = """<html><head><title>LACY GTA VI</title><script>var x = 1;</script></head><body>
<h1>LACY GTA VI Clipping</h1><p>Clips must be 61 seconds or longer. Use #LACY in every post.</p>
<h2>Payout</h2><p>Pool of $12,500 split by views. Minimum 10,000 views.</p></body></html>"""
PAGE_B = PAGE_A.replace("61 seconds", "90 seconds").replace("Use #LACY in every post.", "Use #LACY and #GTA6 in every post.")


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code} error")


def _serve(monkeypatch, pages):
    import requests
    calls = []

    def fake_get(url, timeout=None, headers=None):
        calls.append({"url": url, "timeout": timeout, "headers": headers})
        p = pages.pop(0) if len(pages) > 1 else pages[0]
        if isinstance(p, Exception):
            raise p
        return p if isinstance(p, Resp) else Resp(p)
    monkeypatch.setattr(requests, "get", fake_get)
    return calls


@pytest.fixture()
def camp(campaign):
    dbmod.update("campaigns", campaign["id"], {"url": "https://example.com/lacy", "min_views": 10000})
    return dbmod.get("campaigns", campaign["id"])


@pytest.fixture()
def seen(monkeypatch):
    got = []
    monkeypatch.setattr(events, "emit", lambda name, **kw: got.append((name, kw)))
    return got


def test_page_lines_headings_and_sentences():
    lines = ct.page_lines(PAGE_A)
    assert lines[0] == "# LACY GTA VI Clipping" and "# Payout" in lines
    assert any(l.endswith("Clips must be 61 seconds or longer.") for l in lines) and "Use #LACY in every post." in lines
    assert not any("var x" in l for l in lines)
    assert ct.page_lines("<html><body><script>a()</script></body></html>") == []


def test_first_check_is_baseline_and_same_page_is_no_change(camp, monkeypatch, seen):
    calls = _serve(monkeypatch, [PAGE_A])
    r = ct.check_page(camp["id"])
    assert r == {"ok": True, "baseline": True, "changed": False}
    c = dbmod.get("campaigns", camp["id"])
    assert c["page_hash"] and c["page_text"] and c["page_checked_at"] and not c["page_changed_at"] and not c["page_diff"]
    assert calls[0]["timeout"] == 30 and calls[0]["headers"]["User-Agent"] == "Mozilla/5.0 (compatible; ClipDashboard)"
    assert ct.check_page(camp["id"])["changed"] is False
    assert not dbmod.get("campaigns", camp["id"])["page_changed_at"] and seen == []


def test_change_records_diff_emits_event_and_leaves_rules_alone(camp, monkeypatch, seen):
    before = dict(camp)
    _serve(monkeypatch, [PAGE_A, PAGE_B])
    ct.check_page(camp["id"])
    r = ct.check_page(camp["id"])
    assert r["changed"] is True
    c = dbmod.get("campaigns", camp["id"])
    assert c["page_changed_at"] and "Clips must be 61 seconds or longer.\n" in c["page_diff"]
    diff = c["page_diff"].split("\n")
    assert any(l.startswith("-") and "61 seconds" in l for l in diff) and any(l.startswith("+") and "90 seconds" in l for l in diff)
    assert "+Use #LACY and #GTA6 in every post." in diff and "+Use #LACY and #GTA6 in every post." in c["page_diff"]
    assert len(c["page_diff"].split("\n")) <= 40 and "90 seconds" in c["page_text"]
    for k in ("rules_text", "required_hashtags", "min_len", "max_len", "required_names", "checklist", "pay_model", "pool_budget"):
        assert c[k] == before[k]
    assert [n for n, _ in seen] == ["campaign.rules_changed"]
    assert seen[0][1]["campaign"]["id"] == camp["id"] and seen[0][1]["diff"] == c["page_diff"]


def test_diff_is_capped_at_40_lines():
    d = ct.make_diff([f"old {i}." for i in range(100)], [f"new {i}." for i in range(100)])
    lines = d.split("\n")
    assert len(lines) == 40 and lines[-1].startswith("…") and "more lines" in lines[-1]


def test_fetch_failure_and_textless_page_are_recorded_not_raised(camp, monkeypatch, seen):
    _serve(monkeypatch, [ConnectionError("boom")])
    r = ct.check_page(camp["id"])
    c = dbmod.get("campaigns", camp["id"])
    assert r["ok"] is False and c["page_checked_at"] and "boom" in c["page_error"] and not c["page_hash"]
    _serve(monkeypatch, [Resp("nope", 503)])
    ct.check_page(camp["id"])
    assert "503" in dbmod.get("campaigns", camp["id"])["page_error"]
    _serve(monkeypatch, ["<html><body><script>app()</script></body></html>"])
    ct.check_page(camp["id"])
    assert "no readable text" in dbmod.get("campaigns", camp["id"])["page_error"]
    _serve(monkeypatch, [PAGE_A])
    ct.check_page(camp["id"])
    c = dbmod.get("campaigns", camp["id"])
    assert c["page_error"] is None and c["page_hash"] and seen == []
    # the job handler never raises either
    _serve(monkeypatch, [TimeoutError("slow")])
    ct._job({"campaign_id": camp["id"]}, lambda *a, **k: None)
    assert "slow" in dbmod.get("campaigns", camp["id"])["page_error"]


def test_register_adds_job_and_watch_now_dedupes(camp, monkeypatch):
    monkeypatch.setattr(ct, "_registered", False)
    ct.register()
    assert jobs.HANDLERS["campaign_watch"] is ct._job
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        a = c.post(f"/api/campaigns/{camp['id']}/watch_now").json()
        b = c.post(f"/api/campaigns/{camp['id']}/watch_now").json()
        assert a["queued"] is True and b == {"job_id": a["job_id"], "queued": False}
        dbmod.update("jobs", a["job_id"], {"status": "running"})
        assert c.post(f"/api/campaigns/{camp['id']}/watch_now").json()["queued"] is False
        dbmod.update("jobs", a["job_id"], {"status": "done"})
        assert c.post(f"/api/campaigns/{camp['id']}/watch_now").json()["queued"] is True
        ws = dbmod.ensure_workspace()
        assert c.post(f"/api/campaigns/{ws['id']}/watch_now").status_code == 400
        assert c.post("/api/campaigns/9999/watch_now").status_code == 404
        dbmod.update("campaigns", camp["id"], {"url": ""})
        assert c.post(f"/api/campaigns/{camp['id']}/watch_now").status_code == 400


def _clip_post(db, campaign, clip_status="approved", post_status=None, views=None, submitted=False, deadline=None, n=[0]):
    n[0] += 1
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": f"tv{n[0]}", "url": "u"})
    clip = db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "source_start": 0, "source_length": 60,
                               "status": clip_status})
    if post_status:
        acc = db.insert("accounts", {"platform": "youtube", "label": "yt"})
        db.insert("posts", {"clip_id": clip, "account_id": acc, "campaign_id": campaign["id"], "platform": "youtube",
                            "route": "assisted", "status": post_status, "views": views,
                            "submitted_at": dbmod.now() if submitted else None,
                            "submission_deadline": deadline.isoformat() if deadline else None})


def test_tracker_counts_pool_line_days_left_and_warnings(camp, monkeypatch):
    now = datetime.now(timezone.utc)
    _clip_post(dbmod, camp, "approved")
    _clip_post(dbmod, camp, "pending")
    _clip_post(dbmod, camp, "pending")
    _clip_post(dbmod, camp, "posted", "live", views=3000, submitted=True, deadline=now - timedelta(hours=1))
    _clip_post(dbmod, camp, "posted", "live", views=400, deadline=now - timedelta(minutes=5))      # missed
    _clip_post(dbmod, camp, "posted", "live", views=0, deadline=now + timedelta(minutes=20))       # due
    monkeypatch.setattr(ct, "_today", lambda: date(2026, 11, 21))
    dbmod.update("campaigns", camp["id"], {"ends_at": "2026-11-25", "joined_at": "2026-11-18",
                                           "page_changed_at": dbmod.now(), "page_diff": "-a\n+b", "page_error": "Could not fetch"})
    r = ct.tracker(camp["id"])[0]
    assert r["counts"] == {"approved": 1, "pending": 2, "submitted": 1, "live": 3, "missed": 1, "due": 1}
    assert r["days_left"] == 4 and r["status"] == "active"
    assert r["payout"] == "pool $12,500 · your views 3,400 / threshold 10,000" and r["estimate"] is None
    assert r["pays"].startswith("Pool $12,500")
    kinds = {w["kind"]: w["text"] for w in r["warnings"]}
    assert set(kinds) == {"rules_changed", "ending", "missed", "page_error"}
    assert kinds["ending"] == "Ends in 4 days" and kinds["missed"] == "1 missed submission"
    dbmod.update("campaigns", camp["id"], {"ends_at": "2026-11-18", "url": "", "page_changed_at": None, "page_error": None})
    r = ct.tracker(camp["id"])[0]
    kinds = {w["kind"]: w["text"] for w in r["warnings"]}
    assert r["days_left"] == -3 and kinds["ended"] == "Ended 3 days ago" and "no_url" in kinds and "rules_changed" not in kinds
    dbmod.update("campaigns", camp["id"], {"ends_at": "2026-12-31"})
    assert "ending" not in {w["kind"] for w in ct.tracker(camp["id"])[0]["warnings"]}


def test_cpm_payout_uses_estimate_earnings_per_live_post(fresh_db):
    cid = fresh_db.insert("campaigns", {"name": "CPM", "creator": "x", "pay_model": "cpm", "rate_per_1k": 2.0,
                                        "per_clip_cap": 5.0, "url": "https://e.com"})
    c = fresh_db.get("campaigns", cid)
    _clip_post(fresh_db, c, "posted", "live", views=1000)     # $2
    _clip_post(fresh_db, c, "posted", "live", views=10000)    # $20 capped at $5
    _clip_post(fresh_db, c, "posted", "failed", views=50000)  # not live: ignored
    r = ct.tracker(cid)[0]
    assert r["estimate"] == 7.0 and r["payout"] == "≈ $7 estimated from 2 live posts (11,000 views)"
    assert r["pays"] == "$2 per 1K views · cap $5 per clip"


def test_tracker_sorting_and_workspace_excluded(fresh_db, monkeypatch):
    monkeypatch.setattr(ct, "_today", lambda: date(2026, 11, 21))
    mk = lambda name, **kw: fresh_db.insert("campaigns", {"name": name, "creator": "x", **kw})
    mk("ended", status="ended", active=0, ends_at="2026-11-01")
    mk("late", ends_at="2026-12-30")
    mk("paused", status="paused")
    mk("soon", ends_at="2026-11-23")
    mk("open")
    mk("editor-paused", active=0)
    names = [r["name"] for r in ct.tracker()]
    assert names == ["soon", "late", "open", "paused", "editor-paused", "ended"]


def test_ack_clears_change_keeps_baseline(camp, monkeypatch, seen):
    _serve(monkeypatch, [PAGE_A, PAGE_B, PAGE_B])
    ct.check_page(camp["id"])
    ct.check_page(camp["id"])
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        r = c.post(f"/api/campaigns/{camp['id']}/ack_change").json()
    assert r["page_changed_at"] is None and r["page_diff"] is None
    row = dbmod.get("campaigns", camp["id"])
    assert row["page_hash"] and "90 seconds" in row["page_text"]
    assert ct.check_page(camp["id"])["changed"] is False      # same page as the acknowledged one


def test_put_tracker_validation_and_status(camp):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    url = f"/api/campaigns/{camp['id']}/tracker"
    with TestClient(app) as c:
        assert c.put(url, json={"ends_at": "next week"}).status_code == 400
        assert c.put(url, json={"joined_at": "2026-13-01"}).status_code == 400
        assert c.put(url, json={"status": "done"}).status_code == 400
        assert c.put(url, json={"joined_at": "2026-11-20", "ends_at": "2026-11-10"}).status_code == 400
        r = c.put(url, json={"joined_at": "2026-11-18", "ends_at": "2026-12-15", "payout_notes": "Paid via Whop on Fridays",
                             "status": "ended"})
        assert r.status_code == 200
        j = r.json()
        assert j["joined_at"] == "2026-11-18" and j["ends_at"] == "2026-12-15" and j["status"] == "ended"
        assert j["payout_notes"] == "Paid via Whop on Fridays" and dbmod.get("campaigns", camp["id"])["active"] == 0
        assert c.put(url, json={"status": "paused"}).json()["status"] == "paused"
        assert dbmod.get("campaigns", camp["id"])["active"] == 1
        assert c.put(url, json={"status": "active", "ends_at": ""}).json()["ends_at"] is None
        assert c.put("/api/campaigns/9999/tracker", json={}).status_code == 404
        ws = dbmod.ensure_workspace()
        assert c.put(f"/api/campaigns/{ws['id']}/tracker", json={"status": "ended"}).status_code == 400


def test_stale_enqueue(fresh_db):
    mk = lambda name, **kw: fresh_db.insert("campaigns", {"name": name, "creator": "x", **kw})
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    never = mk("never", url="https://e.com/a")
    stale = mk("stale", url="https://e.com/b", page_checked_at=old)
    mk("recent", url="https://e.com/c", page_checked_at=recent)
    mk("nourl")
    mk("paused", url="https://e.com/d", status="paused")
    mk("ended", url="https://e.com/e", status="ended", active=0)
    mk("editor-off", url="https://e.com/f", active=0)
    assert sorted(ct.enqueue_stale()) == sorted([never, stale])
    assert ct.enqueue_stale() == []                         # already queued
    js = fresh_db.rows("jobs", "type='campaign_watch'")
    assert sorted(j["payload"]["campaign_id"] for j in js) == sorted([never, stale])
    fresh_db.execute("UPDATE jobs SET status='done'")
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        r = c.get("/api/campaigns/tracker").json()
        assert sorted(r["queued"]) == sorted([never, stale])
        assert {x["name"] for x in r["campaigns"]} == {"never", "stale", "recent", "nourl", "paused", "ended", "editor-off"}
        one = c.get(f"/api/campaigns/tracker?campaign_id={never}").json()["campaigns"]
        assert [x["id"] for x in one] == [never] and one[0]["checking"] is True
        assert "/static/features/campaigns.js" in c.get("/api/features").json()


def test_no_thread_without_workers(monkeypatch):
    started = []
    import threading
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    monkeypatch.setattr(ct, "_registered", False)
    monkeypatch.setenv("CLIPDASH_NO_WORKERS", "1")
    ct.register()
    assert started == []
    monkeypatch.setattr(ct, "_registered", False)
    monkeypatch.setenv("CLIPDASH_NO_WORKERS", "0")
    ct.register()
    assert started == ["clipdash-campaign-watch"]
