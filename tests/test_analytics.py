"""Earnings analytics: pure functions with hand-computed expectations, the pool upper-bound wording, hour buckets in a
time zone, the view-sample recorder and the summary endpoint on a small fixture."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from clipdash.features import analytics as an

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
CPM = {"id": 1, "name": "cpm", "pay_model": "cpm", "rate_per_1k": 2.0, "per_clip_cap": 5.0, "pool_budget": 0, "min_views": 0}
POOL = {"id": 2, "name": "pool", "pay_model": "pool", "rate_per_1k": 0, "per_clip_cap": 0, "pool_budget": 12500, "min_views": 100000}


def iso(dt):
    return dt.isoformat()


def post(pid, views, hours_ago, platform="youtube", **extra):
    d = {"id": pid, "views": views, "posted_at": iso(NOW - timedelta(hours=hours_ago)), "platform": platform, "clip_id": pid}
    d.update(extra)
    return d


def sample(pid, views, hours_ago):
    return {"post_id": pid, "t": iso(NOW - timedelta(hours=hours_ago)), "views": views}


# ---------------------------------------------------------------- labels and buckets

def test_moment_strength_matches_review_words():
    assert an.moment_strength(["Chat 9.0× its usual rate (40 msgs/10s), 50% hype words"]) == "Chat exploded"
    assert an.moment_strength(["Chat 3.2× its usual rate (12 msgs/10s)"]) == "Big reaction"
    assert an.moment_strength(["Audio +9 dB louder than usual"]) == "Big reaction"
    assert an.moment_strength(["Excited speech: 4 cue words/laughs in the transcript"]) == "Some reaction"
    assert an.moment_strength(["YouTube 'most replayed' 1.6× the video's median"]) == "Some reaction"
    assert an.moment_strength([]) == "Quiet"
    assert an.moment_strength(None) == "Quiet"


def test_length_buckets():
    assert an.length_bucket(45) == "61–89 s"
    assert an.length_bucket(66) == "61–89 s"          # a 61 s clip with a narrated intro
    assert an.length_bucket(89.9) == "61–89 s"
    assert an.length_bucket(90) == "90–119 s"
    assert an.length_bucket(119) == "90–119 s"
    assert an.length_bucket(120) == "120 s+"
    assert an.length_bucket(181) == "120 s+"
    assert an.length_bucket(None) is None


# ---------------------------------------------------------------- views over time (never inflated)

def test_views_since_uses_latest_sample_before_cutoff():
    samples = [sample(1, 100, 30), sample(1, 400, 20), sample(1, 900, 1)]
    assert an.views_since(samples, NOW - timedelta(hours=24), iso(NOW - timedelta(hours=48)), 900) == 800


def test_views_since_post_newer_than_cutoff_counts_everything():
    assert an.views_since([], NOW - timedelta(hours=24), iso(NOW - timedelta(hours=2)), 350) == 350


def test_views_since_old_post_without_early_sample_is_a_lower_bound():
    samples = [sample(1, 600, 5)]
    assert an.views_since(samples, NOW - timedelta(hours=24), iso(NOW - timedelta(days=3)), 650) == 50
    assert an.views_since([], NOW - timedelta(hours=24), iso(NOW - timedelta(days=3)), 650) == 0


def test_totals_sums_posts_and_windows():
    # post 1 went live 200 h ago: 24 h baseline is the 30 h sample (100); 7 d has no earlier sample, so the first one
    # inside the window (50, at 150 h) is the baseline (a lower bound). Post 2 is 2 h old and counts in full.
    posts = [post(1, 900, 200), post(2, 350, 2), post(3, None, 200)]
    samples = [sample(1, 100, 30), sample(1, 900, 1), sample(1, 50, 150)]
    t = an.totals(posts, samples, NOW)
    assert t["live_posts"] == 3 and t["views"] == 1250 and t["posts_with_views"] == 2
    assert t["views_24h"] == 800 + 350
    assert t["views_7d"] == 850 + 350


# ---------------------------------------------------------------- money

def test_cpm_earnings_use_runner_estimate_with_cap():
    e = an.earnings(CPM, [post(1, 1000, 1), post(2, 10000, 1)])
    assert e["amount"] == 2.0 + 5.0 and e["model"] == "cpm" and e["upper_bound"] is False
    assert "$2 per 1,000 views" in e["note"] and "capped at $5" in e["note"]


def test_pool_estimate_is_a_labelled_upper_bound():
    e = an.earnings(POOL, [post(1, 25000, 1)])
    assert e["amount"] == 12500 * 25000 / 100000 == 3125.0
    assert e["upper_bound"] is True and e["model"] == "pool"
    assert "only clipper" in e["note"] and "upper bound" in e["note"]
    full = an.earnings(POOL, [post(1, 250000, 1)])
    assert full["amount"] == 12500.0 and full["share"] == 1.0 and full["upper_bound"] is True
    assert an.earnings(POOL, [post(1, 0, 1)])["amount"] == 0.0
    assert an.earnings({**POOL, "min_views": 0}, [post(1, 40, 1)])["amount"] == 12500.0


def test_no_pay_model_estimates_nothing():
    e = an.earnings({"pay_model": "none"}, [post(1, 5000, 1)])
    assert e["amount"] == 0.0 and "no pay rate" in e["note"]


def test_by_platform_views_per_post_and_rpm():
    posts = [post(1, 1000, 1, "youtube"), post(2, 3000, 1, "youtube"), post(3, 500, 1, "tiktok")]
    rows = an.by_platform(CPM, posts)
    assert [r["platform"] for r in rows] == ["youtube", "tiktok"]
    yt = rows[0]
    assert yt["posts"] == 2 and yt["views"] == 4000 and yt["views_per_post"] == 2000.0
    assert yt["est"] == 2.0 + 5.0 and yt["rpm"] == round(7.0 / 4000 * 1000, 2) == 1.75
    tt = rows[1]
    assert tt["est"] == 1.0 and tt["rpm"] == 2.0


def test_by_platform_pool_splits_upper_bound_by_view_share():
    posts = [post(1, 30000, 1, "youtube"), post(2, 10000, 1, "tiktok")]
    rows = an.by_platform(POOL, posts)
    assert rows[0]["est"] == 3750.0 and rows[1]["est"] == 1250.0
    assert rows[0]["rpm"] == 125.0


# ---------------------------------------------------------------- per clip, hours, what earns

def test_clip_rows_sorted_by_views_with_clip_facts():
    posts = [post(1, 10, 1, hook="small", strength="Quiet", narration=False, length=70.0),
             post(2, 500, 1, hook="big", strength="Chat exploded", narration=True, length=95.5),
             post(3, None, 1, hook="unknown")]
    rows = an.clip_rows(CPM, posts)
    assert [r["hook"] for r in rows] == ["big", "small", "unknown"]
    assert rows[0]["est"] == 1.0 and rows[0]["narration"] is True and rows[0]["length"] == 95.5 and rows[0]["strength"] == "Chat exploded"
    assert rows[2]["views"] is None and rows[2]["strength"] == "Quiet" and rows[2]["narration"] is False


def test_best_hours_needs_three_posts_and_buckets_by_local_hour():
    assert an.best_hours([post(1, 100, 0), post(2, 300, 0)], timezone.utc) == []
    posts = [{"id": 1, "views": 100, "posted_at": "2026-11-20T12:00:00+00:00"},
             {"id": 2, "views": 300, "posted_at": "2026-11-20T12:30:00+00:00"},
             {"id": 3, "views": 50, "posted_at": "2026-11-20T23:30:00+00:00"},
             {"id": 4, "views": 999, "posted_at": None}]
    assert an.best_hours(posts, timezone.utc) == [{"hour": 12, "posts": 2, "views": 400, "views_per_post": 200.0},
                                                   {"hour": 23, "posts": 1, "views": 50, "views_per_post": 50.0}]
    ny = an.best_hours(posts, ZoneInfo("America/New_York"))        # UTC-5 in November
    assert [(h["hour"], h["posts"]) for h in ny] == [(7, 2), (18, 1)]
    berlin = an.best_hours(posts, ZoneInfo("Europe/Berlin"))        # UTC+1: 23:30 rolls into the next day's 00
    assert [(h["hour"], h["posts"]) for h in berlin] == [(0, 1), (13, 2)]


def test_best_hours_default_is_system_local():
    posts = [{"id": i, "views": 10, "posted_at": "2026-11-20T12:00:00+00:00"} for i in range(3)]
    expect = datetime(2026, 11, 20, 12, tzinfo=timezone.utc).astimezone().hour
    assert an.best_hours(posts) == [{"hour": expect, "posts": 3, "views": 30, "views_per_post": 10.0}]


def test_moments_that_earned_averages_only_buckets_with_two_posts():
    posts = [post(1, 1000, 1, strength="Chat exploded", length=60, narration=True),
             post(2, 3000, 1, strength="Chat exploded", length=61, narration=True),
             post(3, 100, 1, strength="Quiet", length=120, narration=False),
             post(4, 300, 1, strength="Quiet", length=150, narration=False),
             post(5, 50, 1, strength="Big reaction", length=95, narration=False)]
    e = an.moments_that_earned(posts)
    assert e["by_label"] == [{"key": "Chat exploded", "posts": 2, "avg_views": 2000.0}, {"key": "Quiet", "posts": 2, "avg_views": 200.0}]
    assert e["by_length"] == [{"key": "61–89 s", "posts": 2, "avg_views": 2000.0}, {"key": "120 s+", "posts": 2, "avg_views": 200.0}]
    assert e["by_narration"] == [{"key": "Narration off", "posts": 3, "avg_views": 150.0}, {"key": "Narration on", "posts": 2, "avg_views": 2000.0}]
    assert an.moments_that_earned([]) == {"by_label": [], "by_length": [], "by_narration": []}


def test_daily_timeline_is_cumulative_per_day():
    samples = [sample(1, 100, 72), sample(1, 400, 30), sample(2, 50, 20), sample(1, 900, 1)]
    tl = an.daily_timeline(samples, 4, NOW, timezone.utc)
    assert [d["day"] for d in tl] == ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]
    assert [d["views"] for d in tl] == [100, 100, 450, 950]
    assert [d["gained"] for d in tl] == [100, 0, 350, 500]
    assert an.daily_timeline([], 2, NOW, timezone.utc) == [{"day": "2026-11-19", "views": 0, "gained": 0},
                                                           {"day": "2026-11-20", "views": 0, "gained": 0}]


# ---------------------------------------------------------------- database pieces and the endpoint

def _live_post(db, campaign, views=None, posted_at=None, platform="youtube", reasons=None, narration=None, trim_end=70.0):
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": f"v{db.now()}{views}{platform}", "url": "u", "status": "done"})
    mid = db.insert("moments", {"video_id": vid, "t": 10, "score": 5, "reasons": reasons or [], "status": "found"})
    cid = db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "moment_id": mid, "source_start": 0, "source_length": 90, "duration": 80.0,
                              "trim_start": 0, "trim_end": trim_end, "status": "posted", "hook": f"hook {views}", "narration": narration or {}})
    aid = db.insert("accounts", {"platform": platform, "label": platform, "route": "assisted"})
    return db.insert("posts", {"clip_id": cid, "account_id": aid, "campaign_id": campaign["id"], "platform": platform, "route": "assisted",
                               "status": "live", "posted_at": posted_at or db.now(), "views": views, "views_checked_at": db.now() if views is not None else None})


def test_record_and_backfill_samples(fresh_db, campaign, monkeypatch):
    p1 = _live_post(fresh_db, campaign, views=120)
    p2 = _live_post(fresh_db, campaign, views=None)
    assert an.backfill_samples() == 1 and an.backfill_samples() == 0
    assert [s["views"] for s in fresh_db.rows("view_samples")] == [120]
    an.record_sample(fresh_db.get("posts", p1), 200)
    an.record_sample(fresh_db.get("posts", p2), 7)
    assert [(s["post_id"], s["views"]) for s in fresh_db.rows("view_samples")] == [(p1, 120), (p1, 200), (p2, 7)]
    from clipdash import events
    events.clear()
    monkeypatch.setattr(an, "_registered", False)
    an.register()
    events.emit("post.views", post=fresh_db.get("posts", p1), views=250)
    assert fresh_db.rows("view_samples")[-1]["views"] == 250
    events.clear()


def test_register_backfills_existing_counts(fresh_db, campaign, monkeypatch):
    from clipdash import events
    p = _live_post(fresh_db, campaign, views=640)
    monkeypatch.setattr(an, "_registered", False)
    an.register()
    an.register()                                   # subscribes once, backfill is idempotent
    assert [(s["post_id"], s["views"]) for s in fresh_db.rows("view_samples")] == [(p, 640)]
    events.clear()


def test_summary_endpoint_on_small_fixture(fresh_db, campaign):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    fresh_db.update("campaigns", campaign["id"], {"pay_model": "cpm", "rate_per_1k": 1.0, "per_clip_cap": 0})
    _live_post(fresh_db, campaign, views=4000, posted_at="2026-11-20T12:00:00+00:00", reasons=["Chat 9.0× its usual rate (40 msgs/10s)"],
               narration={"enabled": True, "intro_duration": 2.5, "base_duration": 80.0})
    _live_post(fresh_db, campaign, views=1000, posted_at="2026-11-20T12:30:00+00:00", platform="tiktok")
    _live_post(fresh_db, campaign, views=None, posted_at="2026-11-19T22:00:00+00:00", platform="tiktok")
    with TestClient(app) as c:
        r = c.get(f"/api/analytics/summary?campaign_id={campaign['id']}&days=7&tz=America/New_York")
        assert r.status_code == 200
        s = r.json()
        assert s["totals"]["live_posts"] == 3 and s["totals"]["views"] == 5000 and s["totals"]["posts_with_views"] == 2
        assert s["earnings"] == {"amount": 5.0, "model": "cpm", "upper_bound": False, "note": "$1 per 1,000 views"}
        assert [(p["platform"], p["views"], p["posts"]) for p in s["platforms"]] == [("youtube", 4000, 1), ("tiktok", 1000, 2)]
        top = s["clips"][0]
        assert top["hook"] == "hook 4000" and top["strength"] == "Chat exploded" and top["narration"] is True and top["length"] == 72.5 and top["est"] == 4.0
        assert s["clips"][1]["length"] == 70.0 and s["clips"][1]["narration"] is False
        assert [(h["hour"], h["posts"]) for h in s["hours"]] == [(7, 2), (17, 1)]
        assert s["earned"]["by_narration"] == [{"key": "Narration off", "posts": 2, "avg_views": 500.0}]
        assert len(s["timeline"]) == 7 and s["timeline"][-1]["views"] == 5000
        assert fresh_db.execute("SELECT COUNT(*) c FROM view_samples").fetchone()["c"] == 2
        t = c.get(f"/api/analytics/today?campaign_id={campaign['id']}").json()
        assert t["live_posts"] == 3 and t["views"] == 5000 and t["earnings"]["amount"] == 5.0
        assert c.get("/api/analytics/today").json() == {"live_posts": 0}
        fresh_db.update("campaigns", campaign["id"], {"pay_model": "pool", "pool_budget": 12500, "min_views": 100000})
        e = c.get(f"/api/analytics/summary?campaign_id={campaign['id']}").json()["earnings"]
        assert e["amount"] == 625.0 and e["upper_bound"] is True and "upper bound" in e["note"]


def test_summary_endpoint_empty_campaign(fresh_db, campaign):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        s = c.get(f"/api/analytics/summary?campaign_id={campaign['id']}&days=3").json()
        assert s["totals"]["live_posts"] == 0 and s["platforms"] == [] and s["clips"] == [] and s["hours"] == []
        assert s["earnings"]["model"] == "pool" and s["earnings"]["amount"] == 0.0
        assert [d["views"] for d in s["timeline"]] == [0, 0, 0]
