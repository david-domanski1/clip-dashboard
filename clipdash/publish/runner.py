"""Runs posts through their publisher and keeps post state (live, scheduled, pending, private, draft, failed)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db, rules
from .base import PublishResult, get_publisher


def _log(post_id: int, msg: str) -> None:
    post = db.get("posts", post_id)
    log = (post.get("log") or [])[-30:]
    log.append({"t": db.now(), "msg": msg[:500]})
    db.update("posts", post_id, {"log": log})


def apply_result(post: dict, res: PublishResult) -> None:
    fields: dict = {"state_detail": res.detail or ""}
    if res.url:
        fields["url"] = res.url
    if res.external_id:
        fields["external_id"] = res.external_id
    prev = post["status"]
    fields["status"] = res.status
    if res.status == "live" and prev != "live":
        campaign = db.get("campaigns", post["campaign_id"])
        posted_at = res.posted_at or db.now()
        fields["posted_at"] = posted_at
        mins = int((campaign or {}).get("submit_minutes") or 0)
        if mins > 0 and (campaign or {}).get("kind", "campaign") != "streamer":
            dl = datetime.fromisoformat(posted_at) + timedelta(minutes=mins)
            fields["submission_deadline"] = dl.replace(microsecond=0).isoformat()
        fields["error"] = None
        clip = db.get("clips", post["clip_id"])
        if clip and clip["status"] in ("approved", "pending"):
            db.update("clips", clip["id"], {"status": "posted"})
    if res.status == "failed":
        fields["error"] = res.detail
    if "views" in res.extra:
        fields["views"] = res.extra["views"]
        fields["views_checked_at"] = db.now()
    db.update("posts", post["id"], fields)
    _log(post["id"], f"{res.status}: {res.detail}")


def publish_post(post_id: int, progress) -> None:
    from .. import jobs
    post = db.get("posts", post_id)
    if not post or post["status"] in ("live", "cancelled"):
        return
    clip = db.get("clips", post["clip_id"])
    account = db.get("accounts", post["account_id"])
    campaign = db.get("campaigns", post["campaign_id"])
    if clip["status"] not in ("approved", "posted"):
        raise RuntimeError("Only approved clips can be posted")
    if not rules.platform_allowed(campaign, post["platform"]):
        raise RuntimeError(f"The campaign does not allow {post['platform']}")
    video = db.get("videos", clip["video_id"])
    results = rules.validate_clip(clip, campaign, video, platforms=[post["platform"]])
    if not rules.all_pass(results):
        failed = "; ".join(r["detail"] for r in results if not r["ok"])
        raise RuntimeError(f"Rule check failed: {failed}")
    path = clip.get("final_path")
    if not path or not Path(path).exists():
        progress(0.1, "Preparing final file")
        from ..pipeline import finalize_clip
        finalize_clip(clip["id"], progress)
        clip = db.get("clips", clip["id"])
        path = clip["final_path"]
    db.update("posts", post_id, {"status": "publishing", "attempts": post["attempts"] + 1, "error": None})
    progress(0.3, f"Posting to {post['platform']} via {post['route']}")
    pub = get_publisher(account)
    post = db.get("posts", post_id)
    post["duration"] = clip.get("duration")
    res = pub.publish(path, post, post.get("options") or {})
    apply_result(post, res)
    if res.status in ("pending", "scheduled", "draft"):
        jobs.enqueue("check_post", {"post_id": post_id})
    progress(1.0, res.detail)


def check_post(post_id: int, progress) -> None:
    post = db.get("posts", post_id)
    if not post or post["status"] in ("failed", "cancelled", "manual"):
        return
    if post["status"] == "live" and post.get("url"):
        return
    account = db.get("accounts", post["account_id"])
    res = get_publisher(account).check(post)
    if res:
        if post["status"] == "live" and res.status != "live":
            # Never demote a live post because a later poll says something else; only pick up the link.
            if res.url:
                db.update("posts", post_id, {"url": res.url, "state_detail": "Published by the service"})
            return
        apply_result(post, res)


def link_poll_state(post: dict) -> str | None:
    """'pending' while the service may still deliver the post link, 'gave_up' after the polling window, None otherwise."""
    from .service import LINK_POLL_MINUTES
    if post.get("status") != "live" or post.get("url") or post.get("route") != "service" or not post.get("external_id"):
        return None
    posted = post.get("posted_at")
    if not posted:
        return "gave_up"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(posted)
    return "pending" if age < timedelta(minutes=LINK_POLL_MINUTES) else "gave_up"


def refresh_stats(progress, campaign_id: int | None = None) -> None:
    where = "status='live'" + (" AND campaign_id=?" if campaign_id else "")
    for post in db.rows("posts", where, (campaign_id,) if campaign_id else ()):
        account = db.get("accounts", post["account_id"])
        if not account:
            continue
        try:
            s = get_publisher(account).stats(post)
        except Exception as e:  # noqa: BLE001
            _log(post["id"], f"stats failed: {e}")
            continue
        if s and "views" in s:
            db.update("posts", post["id"], {"views": int(s["views"]), "views_checked_at": db.now()})


def mark_live(post_id: int, url: str) -> dict:
    post = db.get("posts", post_id)
    apply_result(post, PublishResult(status="live", url=url.strip(), detail="Link pasted by you"))
    return db.get("posts", post_id)


def estimate_earnings(campaign: dict, views: int) -> float:
    if campaign.get("pay_model") == "pool":
        return 0.0
    rate = float(campaign.get("rate_per_1k") or 0)
    cap = float(campaign.get("per_clip_cap") or 0)
    e = views / 1000.0 * rate
    return min(e, cap) if cap else e
