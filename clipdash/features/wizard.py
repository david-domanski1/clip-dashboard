"""First-run wizard: a practice campaign with one public video, so a stranger has a clip to review within minutes.

The UI (clipdash/web/features/wizard.js) walks four steps: tools, first campaign, first clip, review. This module
only adds what those steps need from the server:

  POST /api/wizard/demo   create the practice campaign and add the demo video (idempotent, never queues a search)
  GET  /api/wizard/state  where the person is: setup/wizard flags, the demo's ids and the step to suggest

The demo video is the Grand Theft Auto VI official trailer (public, 1 m 31 s). YouTube exposes a "most replayed"
curve for it, so the moment search works without a chat replay. The practice campaign pays nothing, has no
submission deadline and says in its rules that nothing from it should be posted; that is advisory, the normal
approval and posting flow is unchanged (a person still approves every clip).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import config, db, pipeline, rules

router = APIRouter()
config.register_settings({"wizard_done": False})

DEMO_NAME = "Demo campaign (practice)"
DEMO_VIDEO_URL = "https://www.youtube.com/watch?v=QdBZY2fkU-0"
DEMO_CLIP_LEN = 61
DEMO_LEAD_IN = 10      # a 61 s clip starting 10 s before the moment fits a 90 s trailer almost anywhere
# The search window starts one second in: a window starting at 0 skips the first `skip_start` seconds (stream-start
# flood guard), which would be the whole trailer. The end is left open so it always reaches the end of the video.
DEMO_WINDOW = {"start": 1, "end": None}
DEMO_RULES_TEXT = (
    "Practice only. This campaign exists so you can try the app on a short public video: find a moment, render a clip, "
    "review it. Nothing made here should be posted or submitted anywhere; it pays nothing and has no submission page. "
    "Delete this campaign from Today when you are done."
)
DEMO_CAMPAIGN = {
    "name": DEMO_NAME, "creator": "Rockstar Games", "required_names": ["Rockstar"], "required_hashtags": ["#GTA6"],
    "min_len": DEMO_CLIP_LEN, "max_len": 120, "platforms": ["youtube", "tiktok", "instagram"], "submit_minutes": 0,
    "pay_model": "cpm", "rate_per_1k": 0, "per_clip_cap": 0, "pool_budget": 0, "min_views": 0, "watermark_allowed": 0,
    "checklist": list(rules.DEFAULT_CHECKLIST), "rules_text": DEMO_RULES_TEXT,
    "notes": "Practice campaign created by the first-run wizard.",
}


def is_practice(campaign: dict | None) -> bool:
    """Whether Today shows the Practice card for this campaign (wizard.js mirrors this: same name, not the workspace)."""
    return bool(campaign) and campaign.get("name") == DEMO_NAME and campaign.get("kind", "campaign") == "campaign"


def demo_campaign() -> dict | None:
    return db.one("campaigns", "name=? AND kind='campaign'", (DEMO_NAME,))


def demo_video(campaign_id: int) -> dict | None:
    return db.one("videos", "campaign_id=? AND platform='youtube' AND external_id=?", (campaign_id, "QdBZY2fkU-0"))


@router.post("/api/wizard/demo")
def create_demo():
    """Create the practice campaign and add the demo video. Calling it again returns the same rows; nothing is queued
    (the wizard starts the search itself through POST /api/videos/{id}/rescan so the person sees it happen)."""
    c = demo_campaign()
    if c is None:
        c = db.get("campaigns", db.insert("campaigns", dict(DEMO_CAMPAIGN)))
    v = demo_video(c["id"])
    if v is None:
        try:
            v = pipeline.add_video(c["id"], DEMO_VIDEO_URL, queue=False)
        except ValueError as e:
            raise HTTPException(400, f"Could not read the demo video ({e}). Check the internet connection and try again.")
    return {"campaign": c, "video": v, "clip_len": DEMO_CLIP_LEN, "lead_in": DEMO_LEAD_IN,
            "window": DEMO_WINDOW}


@router.get("/api/wizard/state")
def wizard_state():
    c = demo_campaign()
    v = demo_video(c["id"]) if c else None
    real = db.one("campaigns", "kind='campaign' AND active=1" + (" AND id!=?" if c else ""), (c["id"],) if c else ())
    pending = db.execute("SELECT COUNT(*) c FROM clips WHERE status IN ('pending','approved')").fetchone()["c"]
    if pending:
        step = "review"
    elif v is not None or real is not None:
        step = "clip"
    elif c is not None:
        step = "campaign"
    else:
        step = "tools"
    return {
        "setup_done": bool(db.get_setting("setup_done", False)), "wizard_done": bool(db.get_setting("wizard_done", False)),
        "demo_campaign_id": c["id"] if c else None, "demo_video_id": v["id"] if v else None,
        "demo_video_status": v["status"] if v else None, "has_own_campaign": real is not None,
        "clips_waiting": pending, "step": step,
        "demo": {"name": DEMO_NAME, "url": DEMO_VIDEO_URL, "clip_len": DEMO_CLIP_LEN, "lead_in": DEMO_LEAD_IN,
            "window": DEMO_WINDOW},
    }
