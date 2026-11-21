"""Campaign tracker and rule-change watch: when each campaign was joined and ends, what it pays, how many clips and posts
it has, what it has earned so far, and whether its public page (the rules) changed since the last look.

Columns added to campaigns: joined_at, ends_at (YYYY-MM-DD), status (active|paused|ended), payout_notes, and the watch
state page_hash, page_text (last seen rules text, the baseline for diffs), page_checked_at, page_changed_at, page_diff,
page_error.

Job campaign_watch {campaign_id}: fetch the campaign url, read its headings and visible text (campaign_import.extract_page,
scripts and styles are never read), turn it into lines ("# heading" lines plus one line per sentence) and hash them. The
first check only stores the baseline. A different hash later sets page_changed_at and a unified diff (≤ 40 lines) and
emits an event. Fetch failures and pages without readable text record page_checked_at + page_error and never raise.
The imported rules (rules_text, hashtags, lengths …) are never edited: the person reads the diff and updates the campaign
by hand. Acknowledging a change clears page_changed_at/page_diff and keeps the new baseline.

Scheduling: register() starts a daemon thread (not when CLIPDASH_NO_WORKERS=1) that after 90 s and then every 15 min
enqueues a check for every active campaign with a url last checked more than 12 h ago; GET /api/campaigns/tracker does
the same. A campaign with a queued or running check is never queued twice.

Event emitted by this feature (in addition to the core list in clipdash/events.py):
  campaign.rules_changed   campaign (row, after the update), diff (str)   the campaign page's text changed"""
from __future__ import annotations

import difflib
import hashlib
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from .. import db, events, jobs

log = logging.getLogger("clipdash.features.campaigns")

STATUSES = ("active", "paused", "ended")
USER_AGENT = "Mozilla/5.0 (compatible; ClipDashboard)"
FETCH_TIMEOUT = 30
STALE_AFTER = timedelta(hours=12)
FIRST_DELAY_S = 90
INTERVAL_S = 15 * 60
MAX_DIFF_LINES = 40
ENDING_SOON_DAYS = 7

for _name, _decl in (("joined_at", "TEXT"), ("ends_at", "TEXT"), ("status", "TEXT DEFAULT 'active'"),
                     ("payout_notes", "TEXT NOT NULL DEFAULT ''"), ("page_hash", "TEXT"), ("page_text", "TEXT"),
                     ("page_checked_at", "TEXT"), ("page_changed_at", "TEXT"), ("page_diff", "TEXT"), ("page_error", "TEXT")):
    db.register_column("campaigns", _name, _decl)

router = APIRouter()


def _today() -> date:
    return date.today()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _parse_day(s) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ---- page text, hashing, diff ----

def page_lines(html: str) -> list[str]:
    """Headings as "# …" lines, then the visible text split into sentences. Pure."""
    from ..campaign_import import extract_page
    page = extract_page(html or "")
    lines = []
    for h in page.get("headings") or []:
        h = re.sub(r"\s+", " ", str(h)).strip()
        if h:
            lines.append("# " + h)
    for s in re.split(r"(?<=[.!?])\s+", page.get("text") or ""):
        s = s.strip()
        if s:
            lines.append(s)
    return lines


def text_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def make_diff(old: list[str], new: list[str], limit: int = MAX_DIFF_LINES) -> str:
    out = list(difflib.unified_diff(old, new, "before", "now", lineterm="", n=1))
    if len(out) > limit:
        more = len(out) - (limit - 1)
        out = out[:limit - 1] + [f"… {more} more lines"]
    return "\n".join(out)


# ---- the watch job ----

def check_page(campaign_id: int, progress=None) -> dict:
    """Fetch the campaign page once and compare it with the baseline. Never raises for network or page problems."""
    c = db.get("campaigns", campaign_id)
    if not c:
        return {"ok": False, "error": "campaign not found"}
    url = (c.get("url") or "").strip()
    now = db.now()
    if not url:
        db.update("campaigns", campaign_id, {"page_checked_at": now, "page_error": "No campaign page URL to check"})
        return {"ok": False, "error": "no url"}
    if progress:
        progress(0.1, "Reading the campaign page")
    try:
        import requests
        r = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        lines = page_lines(r.text)
    except Exception as e:  # noqa: BLE001
        err = f"Could not fetch the page: {e}"[:300]
        db.update("campaigns", campaign_id, {"page_checked_at": now, "page_error": err})
        return {"ok": False, "error": err}
    if not lines:
        err = "The page has no readable text (it may need a login or JavaScript)"
        db.update("campaigns", campaign_id, {"page_checked_at": now, "page_error": err})
        return {"ok": False, "error": err}
    h = text_hash(lines)
    fields = {"page_checked_at": now, "page_error": None}
    if not c.get("page_hash"):
        fields.update({"page_hash": h, "page_text": "\n".join(lines)})
        db.update("campaigns", campaign_id, fields)
        return {"ok": True, "baseline": True, "changed": False}
    if h == c["page_hash"]:
        db.update("campaigns", campaign_id, fields)
        return {"ok": True, "baseline": False, "changed": False}
    old = (c.get("page_text") or "").split("\n") if c.get("page_text") else []
    diff = make_diff(old, lines)
    fields.update({"page_hash": h, "page_text": "\n".join(lines), "page_changed_at": now, "page_diff": diff})
    db.update("campaigns", campaign_id, fields)
    events.emit("campaign.rules_changed", campaign=db.get("campaigns", campaign_id), diff=diff)
    return {"ok": True, "baseline": False, "changed": True, "diff": diff}


def _job(payload: dict, progress) -> None:
    check_page(int(payload["campaign_id"]), progress)


def active_check(campaign_id: int) -> dict | None:
    """The queued or running campaign_watch job for this campaign, if any."""
    for j in db.rows("jobs", "type='campaign_watch' AND status IN ('queued','running')"):
        try:
            if int((j.get("payload") or {}).get("campaign_id")) == int(campaign_id):
                return j
        except (TypeError, ValueError):
            continue
    return None


def enqueue_check(campaign_id: int) -> tuple[int, bool]:
    """(job id, newly queued). Dedupes against a queued or running check."""
    j = active_check(campaign_id)
    if j:
        return j["id"], False
    return jobs.enqueue("campaign_watch", {"campaign_id": int(campaign_id)}), True


def effective_status(c: dict) -> str:
    s = c.get("status") or "active"
    if s not in STATUSES:
        s = "active"
    if s == "active" and not c.get("active", 1):
        return "paused"           # paused from the campaign editor's "Active" box
    return s


def is_stale(c: dict, now: datetime | None = None) -> bool:
    checked = _parse_dt(c.get("page_checked_at"))
    return checked is None or (now or _now()) - checked > STALE_AFTER


def enqueue_stale() -> list[int]:
    """Queue a check for every active campaign with a url whose page was last checked more than 12 h ago."""
    queued = []
    now = _now()
    for c in db.rows("campaigns", "COALESCE(kind,'campaign')!='streamer'"):
        if effective_status(c) != "active" or not (c.get("url") or "").strip() or not is_stale(c, now):
            continue
        _, new = enqueue_check(c["id"])
        if new:
            queued.append(c["id"])
    return queued


# ---- tracker ----

def _money(x: float) -> str:
    x = float(x or 0)
    return f"${x:,.0f}" if x == int(x) else f"${x:,.2f}"


def pays_summary(c: dict) -> str:
    if c.get("pay_model") == "pool":
        s = f"Pool {_money(c.get('pool_budget'))} split by share of views"
        if int(c.get("min_views") or 0):
            s += f" · min {int(c['min_views']):,} views"
        return s
    s = f"{_money(c.get('rate_per_1k'))} per 1K views"
    if float(c.get("per_clip_cap") or 0):
        s += f" · cap {_money(c['per_clip_cap'])} per clip"
    return s


def payout_line(c: dict, live_posts: list[dict]) -> tuple[str, float | None]:
    from ..publish.runner import estimate_earnings
    views = sum(int(p.get("views") or 0) for p in live_posts)
    if c.get("pay_model") == "pool":
        thr = int(c.get("min_views") or 0)
        line = f"pool {_money(c.get('pool_budget'))} · your views {views:,}" + (f" / threshold {thr:,}" if thr else " (no threshold)")
        return line, None
    est = round(sum(estimate_earnings(c, int(p.get("views") or 0)) for p in live_posts), 2)
    n = len(live_posts)
    return f"≈ {_money(est)} estimated from {n} live post{'' if n == 1 else 's'} ({views:,} views)", est


def _counts(cid: int) -> tuple[dict, list[dict]]:
    q = lambda sql: db.execute(sql, (cid,)).fetchone()["c"]
    counts = {"approved": q("SELECT COUNT(*) c FROM clips WHERE campaign_id=? AND status='approved'"),
              "pending": q("SELECT COUNT(*) c FROM clips WHERE campaign_id=? AND status='pending'"),
              "submitted": q("SELECT COUNT(*) c FROM posts WHERE campaign_id=? AND submitted_at IS NOT NULL")}
    live = db.rows("posts", "campaign_id=? AND status='live'", (cid,))
    counts["live"] = len(live)
    now = _now()
    missed = due = 0
    for p in live:
        if p.get("submitted_at"):
            continue
        dl = _parse_dt(p.get("submission_deadline"))
        if dl is None:
            continue
        if dl <= now:
            missed += 1
        else:
            due += 1
    counts["missed"], counts["due"] = missed, due
    return counts, live


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def tracker_row(c: dict) -> dict:
    status = effective_status(c)
    counts, live = _counts(c["id"])
    end = _parse_day(c.get("ends_at"))
    days_left = (end - _today()).days if end else None
    line, est = payout_line(c, live)
    warnings = []
    if c.get("page_changed_at"):
        warnings.append({"kind": "rules_changed", "text": "The campaign page changed since you last looked: check the rules"})
    if days_left is not None:
        if days_left < 0:
            warnings.append({"kind": "ended", "text": f"Ended {_plural(-days_left, 'day')} ago"})
        elif days_left <= ENDING_SOON_DAYS and status != "ended":
            warnings.append({"kind": "ending", "text": "Ends today" if days_left == 0 else f"Ends in {_plural(days_left, 'day')}"})
    if counts["missed"]:
        warnings.append({"kind": "missed", "text": f"{_plural(counts['missed'], 'missed submission')}"})
    if c.get("page_error"):
        warnings.append({"kind": "page_error", "text": f"Could not check the page: {c['page_error']}"})
    if not (c.get("url") or "").strip():
        warnings.append({"kind": "no_url", "text": "No campaign page URL, so rule changes cannot be watched"})
    return {
        "id": c["id"], "name": c["name"], "creator": c.get("creator"), "url": c.get("url"), "status": status,
        "joined_at": c.get("joined_at"), "ends_at": c.get("ends_at"), "days_left": days_left,
        "pay_model": c.get("pay_model"), "pays": pays_summary(c), "payout": line, "estimate": est,
        "payout_notes": c.get("payout_notes") or "", "counts": counts,
        "page_checked_at": c.get("page_checked_at"), "page_changed_at": c.get("page_changed_at"),
        "page_diff": c.get("page_diff"), "page_error": c.get("page_error"), "has_baseline": bool(c.get("page_hash")),
        "checking": bool(active_check(c["id"])), "warnings": warnings,
    }


def tracker(campaign_id: int | None = None) -> list[dict]:
    where, params = "COALESCE(kind,'campaign')!='streamer'", ()
    if campaign_id:
        where += " AND id=?"
        params = (campaign_id,)
    out = [tracker_row(c) for c in db.rows("campaigns", where, params)]
    order = {s: i for i, s in enumerate(STATUSES)}
    out.sort(key=lambda r: (order.get(r["status"], 9), r["days_left"] is None,
                            r["days_left"] if r["days_left"] is not None else 0, r["id"]))
    return out


# ---- API ----

def _campaign_or_404(cid: int) -> dict:
    c = db.get("campaigns", cid)
    if not c:
        raise HTTPException(404, "Campaign not found")
    if db.is_workspace(c):
        raise HTTPException(400, "The Streamer clips workspace has no campaign to track")
    return c


@router.get("/api/campaigns/tracker")
def api_tracker(campaign_id: int | None = None):
    queued = enqueue_stale()
    return {"campaigns": tracker(campaign_id), "queued": queued}


def _day_or_400(v, label: str) -> str | None:
    if v in (None, ""):
        return None
    d = _parse_day(v)
    if d is None or not re.match(r"^\d{4}-\d{2}-\d{2}", str(v)):
        raise HTTPException(400, f"{label} must be a date like 2026-12-31")
    return d.isoformat()


@router.put("/api/campaigns/{cid}/tracker")
def api_update(cid: int, body: dict):
    c = _campaign_or_404(cid)
    data: dict = {}
    if "joined_at" in body:
        data["joined_at"] = _day_or_400(body["joined_at"], "Joined date")
    if "ends_at" in body:
        data["ends_at"] = _day_or_400(body["ends_at"], "End date")
    joined = data.get("joined_at", c.get("joined_at"))
    ends = data.get("ends_at", c.get("ends_at"))
    if joined and ends and ends < joined:
        raise HTTPException(400, "The end date is before the joined date")
    if "status" in body:
        s = str(body["status"] or "").strip().lower()
        if s not in STATUSES:
            raise HTTPException(400, "Status must be active, paused or ended")
        data["status"] = s
        data["active"] = 0 if s == "ended" else 1
    if "payout_notes" in body:
        data["payout_notes"] = str(body["payout_notes"] or "")[:2000]
    db.update("campaigns", cid, data)
    return tracker_row(db.get("campaigns", cid))


@router.post("/api/campaigns/{cid}/watch_now")
def api_watch_now(cid: int):
    c = _campaign_or_404(cid)
    if not (c.get("url") or "").strip():
        raise HTTPException(400, "This campaign has no page URL to check; add it under Campaign rules")
    jid, new = enqueue_check(cid)
    return {"job_id": jid, "queued": new}


@router.post("/api/campaigns/{cid}/ack_change")
def api_ack(cid: int):
    _campaign_or_404(cid)
    db.update("campaigns", cid, {"page_changed_at": None, "page_diff": None})
    return tracker_row(db.get("campaigns", cid))


# ---- registration / scheduler ----

_registered = False
_stop = threading.Event()


def _loop() -> None:
    if _stop.wait(FIRST_DELAY_S):
        return
    while True:
        try:
            q = enqueue_stale()
            if q:
                log.info("queued campaign page checks for %s", q)
        except Exception:  # noqa: BLE001
            log.exception("campaign watch scheduling failed")
        if _stop.wait(INTERVAL_S):
            return


def register() -> None:
    global _registered
    jobs.HANDLERS["campaign_watch"] = _job
    if _registered:
        return
    _registered = True
    if os.environ.get("CLIPDASH_NO_WORKERS") != "1":
        threading.Thread(target=_loop, daemon=True, name="clipdash-campaign-watch").start()
