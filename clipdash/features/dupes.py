"""Duplicate guard: the same footage must not be posted twice for the same campaign and platform (no inflated numbers).

Videos are keyed by platform + external_id per campaign, so one VOD can exist as several video rows (one per campaign).
Two clips show the same footage when their videos share platform + external_id (any campaign, any video row) and
either their source ranges overlap by at least OVERLAP_MIN_S seconds or their moment times are within MOMENT_NEAR_S.

Severity for a clip:
  * "blocked" when a match is in the same campaign and is already posted (POSTED_STATUSES) to a platform that campaign
    allows; the Review modal then keeps Approve disabled and says why.
  * "warn" for any other match (another campaign, or not posted yet): a yellow banner, approval stays possible.

This is a UI guard plus a banner. The core approval endpoint is unchanged; nothing here approves or posts anything."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from .. import db

log = logging.getLogger("clipdash.features.dupes")

OVERLAP_MIN_S = 20.0
MOMENT_NEAR_S = 45.0
POSTED_STATUSES = ("live", "scheduled", "pending", "private", "draft", "manual", "publishing")
CLIP_LABELS = {"rendering": "Making clip…", "pending": "Ready to review", "approved": "Approved", "rejected": "Rejected",
               "posted": "Posted", "error": "Failed"}

router = APIRouter()


# ---- pure functions ----

def overlap(a_start: float, a_len: float, b_start: float, b_len: float) -> float:
    """Seconds shared by [a_start, a_start + a_len) and [b_start, b_start + b_len); 0 when they do not touch."""
    a0, b0 = float(a_start or 0), float(b_start or 0)
    a1, b1 = a0 + max(0.0, float(a_len or 0)), b0 + max(0.0, float(b_len or 0))
    return round(max(0.0, min(a1, b1) - max(a0, b0)), 1)


def same_vod(a: dict, b: dict) -> bool:
    return (str(a.get("platform") or "").lower() == str(b.get("platform") or "").lower()
            and str(a.get("external_id") or "") != "" and str(a.get("external_id")) == str(b.get("external_id")))


def find_duplicates(target: dict, clips: list[dict]) -> list[dict]:
    """Pure: clips showing the same footage as target.

    target: {clip_id?, campaign_id, platform, external_id, start?, length?, t?}
    clips:  [{clip_id, campaign_id, campaign_name, status, platform, external_id, start, length, t?, posts: [{platform,
             status, posted_at?, scheduled_at?, created_at?}]}]
    → [{clip_id, campaign_id, campaign_name, status, status_label, posted: [{platform, status, date}], posted_platforms,
        overlap_s, same_campaign}] sorted same campaign first, then by overlap. target's own clip is never included."""
    out = []
    for c in clips:
        if target.get("clip_id") is not None and c.get("clip_id") == target.get("clip_id"):
            continue
        if not same_vod(target, c):
            continue
        ov = 0.0
        if target.get("start") is not None and target.get("length") and c.get("start") is not None and c.get("length"):
            ov = overlap(target["start"], target["length"], c["start"], c["length"])
        near = (target.get("t") is not None and c.get("t") is not None
                and abs(float(target["t"]) - float(c["t"])) <= MOMENT_NEAR_S)
        if ov < OVERLAP_MIN_S and not near:
            continue
        posted = [{"platform": p.get("platform"), "status": p.get("status"),
                   "date": p.get("posted_at") or p.get("scheduled_at") or p.get("created_at")}
                  for p in (c.get("posts") or []) if p.get("status") in POSTED_STATUSES]
        out.append({"clip_id": c.get("clip_id"), "campaign_id": c.get("campaign_id"),
                    "campaign_name": c.get("campaign_name") or "", "status": c.get("status"),
                    "status_label": CLIP_LABELS.get(c.get("status"), c.get("status") or ""),
                    "posted": posted, "posted_platforms": sorted({p["platform"] for p in posted if p["platform"]}),
                    "overlap_s": ov, "same_campaign": c.get("campaign_id") == target.get("campaign_id")})
    out.sort(key=lambda m: (not m["same_campaign"], -m["overlap_s"], m["clip_id"] or 0))
    return out


def _day(iso: str | None) -> str:
    return (iso or "")[:10] or "an earlier date"


def assess(matches: list[dict], campaign_platforms: list[str] | None) -> dict:
    """Pure: {severity: "blocked" | "warn" | None, message}. Blocked when a same-campaign match is already posted to a
    platform the campaign allows (no platform list → every platform counts)."""
    allowed = set(campaign_platforms or [])
    for m in matches:
        if not m["same_campaign"]:
            continue
        for p in m["posted"]:
            if not allowed or p["platform"] in allowed:
                return {"severity": "blocked", "message": f"Already posted for this campaign: {p['platform']} on {_day(p['date'])}"}
    if matches:
        m = matches[0]
        name = "this campaign" if m["same_campaign"] else (m["campaign_name"] or "another campaign")
        return {"severity": "warn", "message": f"This moment was already clipped for {name} ({m['status_label']})"}
    return {"severity": None, "message": ""}


# ---- data ----

def _candidates(platform: str, external_id: str) -> list[dict]:
    """Every clip (any campaign, any video row) cut from this platform + external_id, with its posts."""
    rows = db.execute(
        """SELECT c.id clip_id, c.campaign_id, c.status, c.source_start start, c.source_length length,
                  v.platform, v.external_id, m.t, cp.name campaign_name
           FROM clips c JOIN videos v ON v.id=c.video_id LEFT JOIN moments m ON m.id=c.moment_id
           LEFT JOIN campaigns cp ON cp.id=c.campaign_id
           WHERE lower(v.platform)=lower(?) AND v.external_id=?""", (platform, external_id)).fetchall()
    clips = [dict(r) for r in rows]
    if clips:
        ids = [c["clip_id"] for c in clips]
        posts: dict[int, list] = {}
        q = ",".join("?" * len(ids))
        for p in db.execute(f"SELECT clip_id, platform, status, posted_at, scheduled_at, created_at FROM posts WHERE clip_id IN ({q})",
                            tuple(ids)).fetchall():
            posts.setdefault(p["clip_id"], []).append(dict(p))
        for c in clips:
            c["posts"] = posts.get(c["clip_id"], [])
    return clips


def _campaign_platforms(campaign_id: int) -> list[str]:
    c = db.get("campaigns", campaign_id)
    return list((c or {}).get("platforms") or [])


def check_clip(clip_id: int) -> dict:
    clip = db.get("clips", clip_id)
    if not clip:
        raise HTTPException(404, "Clip not found")
    v = db.get("videos", clip["video_id"]) or {}
    m = db.get("moments", clip["moment_id"]) if clip.get("moment_id") else None
    target = {"clip_id": clip["id"], "campaign_id": clip["campaign_id"], "platform": v.get("platform"),
              "external_id": v.get("external_id"), "start": clip["source_start"], "length": clip["source_length"],
              "t": m["t"] if m else None}
    matches = find_duplicates(target, _candidates(v.get("platform") or "", v.get("external_id") or ""))
    return {"clip_id": clip_id, "matches": matches, **assess(matches, _campaign_platforms(clip["campaign_id"]))}


def check_video(video_id: int) -> dict:
    """{moment_id: matches} for every moment of one video row that has matches (its own clip excluded)."""
    v = db.get("videos", video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    cands = _candidates(v["platform"], v["external_id"])
    own = {c["moment_id"]: c for c in db.rows("clips", "video_id=? AND moment_id IS NOT NULL", (video_id,))}
    out = {}
    for m in db.rows("moments", "video_id=?", (video_id,)):
        clip = own.get(m["id"])
        target = {"clip_id": clip["id"] if clip else None, "campaign_id": v["campaign_id"], "platform": v["platform"],
                  "external_id": v["external_id"], "t": m["t"],
                  "start": clip["source_start"] if clip else None, "length": clip["source_length"] if clip else None}
        matches = find_duplicates(target, cands)
        if matches:
            out[str(m["id"])] = matches
    return out


def check_campaign(campaign_id: int) -> list[dict]:
    """Pending or approved clips of a campaign whose footage is already posted for that campaign."""
    out = []
    for c in db.rows("clips", "campaign_id=? AND status IN ('pending','approved')", (campaign_id,)):
        r = check_clip(c["id"])
        if r["severity"] == "blocked":
            out.append({"clip_id": c["id"], "status": c["status"], "hook": c.get("hook") or "", "message": r["message"]})
    return out


# ---- API ----

@router.get("/api/dupes/clip/{clip_id}")
def api_clip(clip_id: int):
    r = check_clip(clip_id)
    return {"severity": r["severity"], "matches": r["matches"], "message": r["message"]}


@router.get("/api/dupes/video/{video_id}")
def api_video(video_id: int):
    return check_video(video_id)


@router.get("/api/dupes/campaign/{campaign_id}")
def api_campaign(campaign_id: int):
    return {"blocked": check_campaign(campaign_id)}
