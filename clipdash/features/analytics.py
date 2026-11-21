"""Earnings analytics: what the clipping earns and what works.

Data: one `view_samples` row (post_id, t, views) per `post.views` event, so views can be read back over time. Posts that
already had a view count before this feature existed get one backfilled sample: register() tries it (when the database
and table already exist) and every read repeats it, because a fresh database is only created in the app lifespan.

Every number is computed by a pure function over lists of dicts (see `totals`, `earnings`, `by_platform`, `clip_rows`,
`best_hours`, `moments_that_earned`, `daily_timeline`) so it can be tested without the database. The endpoint only
collects the rows and calls them.

Honesty rules: views in the last 24 h / 7 d are a lower bound (a post whose first sample is inside the window counts
only what it gained since that sample); the pool estimate is an upper bound and says so; nothing is extrapolated."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone, tzinfo

from fastapi import APIRouter

from .. import db, events
from ..publish import runner

router = APIRouter()

db.register_table("""
CREATE TABLE IF NOT EXISTS view_samples (
  id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  t TEXT NOT NULL, views INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS view_samples_post ON view_samples(post_id, t);
""")

POOL_NOTE = "Estimate, upper bound: assumes you are the only clipper over the threshold. The pool is shared, so expect less."
MIN_POSTS_FOR_HOURS = 3
MIN_POSTS_PER_BUCKET = 2
STRENGTH_LABELS = ("Chat exploded", "Big reaction", "Some reaction", "Quiet")
LENGTH_BUCKETS = (("61–89 s", 90), ("90–119 s", 120), ("120 s+", None))   # the 61 / 90 / 120 s presets; upper bound exclusive


# ---------------------------------------------------------------- samples

def record_sample(post: dict, views: int) -> None:
    """Handler for the post.views event: one row per refresh."""
    if not post or views is None:
        return
    db.insert("view_samples", {"post_id": post["id"], "t": db.now(), "views": int(views)})


def backfill_samples() -> int:
    """One sample from posts.views for every post that has a count but no sample yet. Idempotent."""
    rows = db.execute("SELECT p.id, p.views, p.views_checked_at, p.posted_at FROM posts p "
                      "WHERE p.views IS NOT NULL AND NOT EXISTS (SELECT 1 FROM view_samples s WHERE s.post_id=p.id)").fetchall()
    for r in rows:
        db.insert("view_samples", {"post_id": r["id"], "t": r["views_checked_at"] or r["posted_at"] or db.now(), "views": int(r["views"])})
    return len(rows)


_registered = False


def register() -> None:
    global _registered
    if not _registered:
        events.on("post.views", record_sample)
        _registered = True
    try:
        backfill_samples()
    except Exception:  # noqa: BLE001  (no database or table yet: the first read backfills instead)
        pass


# ---------------------------------------------------------------- pure helpers

def _dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _money(x: float) -> float:
    return round(float(x), 2)


def moment_strength(reasons: list[str] | None) -> str:
    """Same words the Review UI uses for a moment: the numbers in its reasons decide the label."""
    txt = " ".join(reasons or [])

    def num(pattern: str) -> float:
        m = re.search(pattern, txt)
        return float(m.group(1)) if m else 0.0

    spike, hype, heat = num(r"Chat ([\d.]+)×"), num(r"(\d+)% hype"), num(r"most replayed' ([\d.]+)×")
    loud, cues = num(r"Audio \+(\d+) dB"), num(r"(\d+) cue words")
    if spike >= 8 or hype >= 40 or heat >= 4:
        return "Chat exploded"
    if spike >= 3 or heat >= 2 or loud >= 8 or cues >= 6:
        return "Big reaction"
    if spike >= 1.5 or hype >= 20 or heat >= 1.5 or loud >= 4 or cues >= 3:
        return "Some reaction"
    return "Quiet"


def length_bucket(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    for name, upper in LENGTH_BUCKETS:
        if upper is None or seconds < upper:
            return name
    return None


def views_since(samples: list[dict], since: datetime, posted_at: str | None, current: int) -> int:
    """Views a post gained since `since`, never inflated: the baseline is the latest sample at or before `since`; a post
    posted after `since` starts from 0; a post with no sample that early counts only the gain since its first sample."""
    posted = _dt(posted_at)
    if posted is not None and posted >= since:
        return max(0, int(current))
    ordered = sorted((s for s in samples if _dt(s.get("t")) is not None), key=lambda s: _dt(s["t"]))
    before = [s for s in ordered if _dt(s["t"]) <= since]
    if before:
        baseline = int(before[-1]["views"])
    elif ordered:
        baseline = int(ordered[0]["views"])
    else:
        return 0
    return max(0, int(current) - baseline)


def _group_samples(samples: list[dict]) -> dict[int, list[dict]]:
    g: dict[int, list[dict]] = defaultdict(list)
    for s in samples:
        g[int(s["post_id"])].append(s)
    return g


def totals(posts: list[dict], samples: list[dict], now: datetime | None = None) -> dict:
    """posts: live post dicts (id, views, posted_at). samples: view_samples rows for those posts."""
    now = now or _utcnow()
    by_post = _group_samples(samples)
    views = sum(int(p.get("views") or 0) for p in posts)
    v24 = sum(views_since(by_post.get(p["id"], []), now - timedelta(hours=24), p.get("posted_at"), int(p.get("views") or 0)) for p in posts)
    v7 = sum(views_since(by_post.get(p["id"], []), now - timedelta(days=7), p.get("posted_at"), int(p.get("views") or 0)) for p in posts)
    counted = sum(1 for p in posts if p.get("views") is not None)
    return {"live_posts": len(posts), "views": views, "views_24h": v24, "views_7d": v7, "posts_with_views": counted}


def pool_estimate(campaign: dict, views: int) -> dict:
    """Upper bound for a pool campaign: the whole pool scaled by your views over the threshold (or over your own views
    once you pass it). It is what you would get if nobody else reached the threshold, which is never a promise."""
    budget = float(campaign.get("pool_budget") or 0)
    need = int(campaign.get("min_views") or 0)
    denom = max(int(views), need, 0)
    share = (int(views) / denom) if denom else 0.0
    return {"amount": _money(budget * share), "model": "pool", "upper_bound": True, "note": POOL_NOTE,
            "share": round(share, 4), "threshold": need, "pool_budget": budget}


def earnings(campaign: dict, posts: list[dict]) -> dict:
    """Estimated $ for a list of live posts. CPM: the runner's per-post estimate (rate per 1k, capped per clip). Pool: the
    upper bound above. Anything else: nothing to estimate."""
    model = campaign.get("pay_model") or "none"
    total_views = sum(int(p.get("views") or 0) for p in posts)
    if model == "pool":
        return pool_estimate(campaign, total_views)
    if model == "cpm":
        amt = sum(runner.estimate_earnings(campaign, int(p.get("views") or 0)) for p in posts)
        return {"amount": _money(amt), "model": "cpm", "upper_bound": False,
                "note": f"${float(campaign.get('rate_per_1k') or 0):g} per 1,000 views" + (f", capped at ${float(campaign['per_clip_cap']):g} per clip" if campaign.get("per_clip_cap") else "")}
    return {"amount": 0.0, "model": model, "upper_bound": False, "note": "This campaign has no pay rate to estimate from."}


def _post_estimate(campaign: dict, post: dict, campaign_total: dict | None = None) -> float:
    """$ attributed to one post: the runner's number for CPM; for a pool, the campaign's upper bound split by view share."""
    if campaign.get("pay_model") == "pool":
        tot = campaign_total or {}
        views_all = int(tot.get("views") or 0)
        return _money(float(tot.get("amount") or 0) * (int(post.get("views") or 0) / views_all)) if views_all else 0.0
    return _money(runner.estimate_earnings(campaign, int(post.get("views") or 0)))


def _pool_context(campaign: dict, posts: list[dict]) -> dict | None:
    if campaign.get("pay_model") != "pool":
        return None
    e = earnings(campaign, posts)
    return {"amount": e["amount"], "views": sum(int(p.get("views") or 0) for p in posts)}


def by_platform(campaign: dict, posts: list[dict]) -> list[dict]:
    """Per platform: posts, views, views per post, estimated $ and $ per 1,000 views (RPM). Sorted by views."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for p in posts:
        groups[p.get("platform") or "unknown"].append(p)
    ctx = _pool_context(campaign, posts)
    out = []
    for plat, ps in groups.items():
        views = sum(int(p.get("views") or 0) for p in ps)
        est = sum(_post_estimate(campaign, p, ctx) for p in ps)
        out.append({"platform": plat, "posts": len(ps), "views": views, "views_per_post": round(views / len(ps), 1) if ps else 0.0,
                    "est": _money(est), "rpm": _money(est / views * 1000) if views else 0.0})
    return sorted(out, key=lambda r: (-r["views"], r["platform"]))


def clip_rows(campaign: dict, posts: list[dict]) -> list[dict]:
    """One row per live post for the table. Each post dict may carry hook, strength, narration, length, url, clip_id."""
    ctx = _pool_context(campaign, posts)
    rows = []
    for p in posts:
        rows.append({"post_id": p["id"], "clip_id": p.get("clip_id"), "hook": p.get("hook") or "", "platform": p.get("platform") or "",
                     "posted_at": p.get("posted_at"), "views": p.get("views"), "est": _post_estimate(campaign, p, ctx),
                     "strength": p.get("strength") or "Quiet", "narration": bool(p.get("narration")), "length": p.get("length"),
                     "url": p.get("url"), "views_checked_at": p.get("views_checked_at")})
    return sorted(rows, key=lambda r: -(r["views"] or 0))


def best_hours(posts: list[dict], tz: tzinfo | None = None) -> list[dict]:
    """Views per post by the local hour the post went live. Empty until there are at least 3 posts with a time."""
    timed = [(p, _dt(p.get("posted_at"))) for p in posts]
    timed = [(p, d) for p, d in timed if d is not None]
    if len(timed) < MIN_POSTS_FOR_HOURS:
        return []
    buckets: dict[int, list[int]] = defaultdict(list)
    for p, d in timed:
        local = d.astimezone(tz) if tz is not None else d.astimezone()
        buckets[local.hour].append(int(p.get("views") or 0))
    return [{"hour": h, "posts": len(v), "views": sum(v), "views_per_post": round(sum(v) / len(v), 1)} for h, v in sorted(buckets.items())]


def _avg_buckets(items: list[tuple[str, int]], order: list[str] | None = None) -> list[dict]:
    groups: dict[str, list[int]] = defaultdict(list)
    for key, views in items:
        if key is not None:
            groups[key].append(views)
    keys = [k for k in order if k in groups] if order else sorted(groups)
    return [{"key": k, "posts": len(groups[k]), "avg_views": round(sum(groups[k]) / len(groups[k]), 1)}
            for k in keys if len(groups[k]) >= MIN_POSTS_PER_BUCKET]


def moments_that_earned(posts: list[dict]) -> dict:
    """Average views by moment strength, by clip length bucket and by narration on/off; buckets under 2 posts are left out."""
    v = lambda p: int(p.get("views") or 0)  # noqa: E731
    return {
        "by_label": _avg_buckets([(p.get("strength") or "Quiet", v(p)) for p in posts], list(STRENGTH_LABELS)),
        "by_length": _avg_buckets([(length_bucket(p.get("length")), v(p)) for p in posts], [n for n, _ in LENGTH_BUCKETS]),
        "by_narration": _avg_buckets([("Narration on" if p.get("narration") else "Narration off", v(p)) for p in posts], ["Narration off", "Narration on"]),
    }


def daily_timeline(samples: list[dict], days: int, now: datetime | None = None, tz: tzinfo | None = None) -> list[dict]:
    """Total views per local day over the window: for each day, the sum over posts of the latest sample at or before
    the end of that day (days before any sample read 0). `gained` is the change from the day before, never below 0; a
    post's first sample shows up whole on the day it was taken."""
    now = now or _utcnow()
    local_now = now.astimezone(tz) if tz is not None else now.astimezone()
    by_post = {pid: sorted((s for s in ss if _dt(s.get("t")) is not None), key=lambda s: _dt(s["t"])) for pid, ss in _group_samples(samples).items()}
    out = []
    prev = None
    for i in range(days, -1, -1):
        day = (local_now - timedelta(days=i)).date()
        end = datetime.combine(day, datetime.max.time()).replace(tzinfo=local_now.tzinfo)
        total = 0
        for ss in by_post.values():
            upto = [s for s in ss if _dt(s["t"]) <= end]
            if upto:
                total += int(upto[-1]["views"])
        if prev is not None:
            out.append({"day": day.isoformat(), "views": total, "gained": max(0, total - prev)})
        prev = total
    return out


# ---------------------------------------------------------------- endpoint

def _tzinfo(name: str | None) -> tzinfo | None:
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return None


def _live_posts(campaign_id: int) -> list[dict]:
    """Live posts of a campaign with the clip facts the pure functions want (hook, strength, narration, length)."""
    out = []
    for p in db.rows("posts", "campaign_id=? AND status='live'", (campaign_id,), order="posted_at DESC"):
        clip = db.get("clips", p["clip_id"]) or {}
        moment = db.get("moments", clip["moment_id"]) if clip.get("moment_id") else None
        narr = clip.get("narration") or {}
        base = narr.get("base_duration") or clip.get("duration")
        length = None
        if base is not None:
            start = float(clip.get("trim_start") or 0)
            end = float(clip.get("trim_end") or base)
            length = round(max(0.0, end - start) + (float(narr.get("intro_duration") or 0) if narr.get("enabled") else 0.0), 1)
        p.update({"hook": clip.get("hook") or clip.get("title") or p.get("title") or "", "strength": moment_strength((moment or {}).get("reasons")),
                  "narration": bool(narr.get("enabled")), "length": length})
        out.append(p)
    return out


def summary_for(campaign: dict, posts: list[dict], samples: list[dict], days: int, tz: tzinfo | None, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    return {
        "campaign": {k: campaign.get(k) for k in ("id", "name", "pay_model", "rate_per_1k", "per_clip_cap", "pool_budget", "min_views")},
        "days": days, "totals": totals(posts, samples, now), "earnings": earnings(campaign, posts),
        "platforms": by_platform(campaign, posts), "clips": clip_rows(campaign, posts), "hours": best_hours(posts, tz),
        "earned": moments_that_earned(posts), "timeline": daily_timeline(samples, days, now, tz),
        "last_checked": max((p.get("views_checked_at") for p in posts if p.get("views_checked_at")), default=None),
        "min_posts_for_hours": MIN_POSTS_FOR_HOURS, "min_posts_per_bucket": MIN_POSTS_PER_BUCKET,
    }


@router.get("/api/analytics/summary")
def analytics_summary(campaign_id: int | None = None, days: int = 30, tz: str | None = None):
    campaign = (db.get("campaigns", campaign_id) if campaign_id else None) or db.one("campaigns", "active=1 AND kind!='streamer'") or db.ensure_workspace()
    backfill_samples()
    days = max(1, min(int(days or 30), 365))
    posts = _live_posts(campaign["id"])
    ids = [p["id"] for p in posts]
    samples = db.rows("view_samples", f"post_id IN ({','.join('?' * len(ids))})", tuple(ids), order="t") if ids else []
    return summary_for(campaign, posts, samples, days, _tzinfo(tz))


@router.get("/api/analytics/today")
def analytics_today(campaign_id: int | None = None):
    """The small Today card: views in the last 24 h and the estimate, only meaningful with a live post."""
    campaign = db.get("campaigns", campaign_id) if campaign_id else None
    if not campaign:
        return {"live_posts": 0}
    backfill_samples()
    posts = _live_posts(campaign["id"])
    ids = [p["id"] for p in posts]
    samples = db.rows("view_samples", f"post_id IN ({','.join('?' * len(ids))})", tuple(ids), order="t") if ids else []
    t = totals(posts, samples)
    return {"live_posts": t["live_posts"], "views": t["views"], "views_24h": t["views_24h"], "earnings": earnings(campaign, posts)}
