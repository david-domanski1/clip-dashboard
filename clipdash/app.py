"""HTTP API and static UI. Everything the browser does goes through here."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, captions, config, db, disk, jobs, pipeline, rules, secrets_store, sources, tools
from .publish import get_publisher, is_dry_run, oauth_state, route_info, runner
from .publish import scheduler as sched

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    config.ensure_dirs()
    db.init()
    jobs.register_handlers()
    if os.environ.get("CLIPDASH_NO_WORKERS") != "1":
        jobs.start(2)
        sched.start()
    yield
    jobs.stop()
    sched.stop()


app = FastAPI(title="Clip Dashboard", version=__version__, lifespan=_lifespan)


def _404(msg="Not found"):
    raise HTTPException(404, msg)


def _bad(msg):
    raise HTTPException(400, msg)


# ------------------------------------------------------------------ overview / setup / settings

@app.get("/api/state")
def state():
    counts = {}
    for t, w in (("campaigns", "active=1"), ("videos", "status IN ('queued','scanning','rendering')"),
                 ("clips", "status='pending'"), ("clips", "status='approved'"), ("posts", "status='scheduled'"),
                 ("posts", "status='live' AND submitted_at IS NULL AND submission_deadline IS NOT NULL")):
        counts[f"{t}:{w}"] = db.execute(f"SELECT COUNT(*) c FROM {t} WHERE {w}").fetchone()["c"]
    running = db.rows("jobs", "status='running'")
    queued = db.execute("SELECT COUNT(*) c FROM jobs WHERE status='queued'").fetchone()["c"]
    return {
        "version": __version__, "dry_run": is_dry_run(), "disk": disk.usage(), "jobs_running": running, "jobs_queued": queued,
        "active_campaigns": counts["campaigns:active=1"],
        "videos_in_progress": counts["videos:status IN ('queued','scanning','rendering')"],
        "clips_to_review": counts["clips:status='pending'"], "clips_approved": counts["clips:status='approved'"],
        "posts_scheduled": counts["posts:status='scheduled'"],
        "submissions_due": counts["posts:status='live' AND submitted_at IS NULL AND submission_deadline IS NOT NULL"],
        "setup_done": bool(db.get_setting("setup_done", False)), "os": platform.system(),
    }


@app.get("/api/setup/checks")
def setup_checks():
    return {"checks": tools.check_dependencies(), "secrets": {k: secrets_store.masked(k) for k in (
        "youtube_client_secret", "tiktok_client_key", "tiktok_client_secret", "facebook_app_id", "facebook_app_secret", "service_api_key")},
        "callback_urls": {"tiktok": f"http://127.0.0.1:{config.PORT}/oauth/callback/tiktok/",
                          "instagram": f"http://localhost:{config.PORT}/oauth/callback/instagram/"}}


@app.post("/api/setup/action")
def setup_action(body: dict):
    action = body.get("action")
    if action not in ("install_tdcli", "download_whisper"):
        _bad("Unknown action")
    return {"job_id": jobs.enqueue(action, {})}


@app.post("/api/setup/secrets")
def setup_secrets(body: dict):
    allowed = {"youtube_client_secret", "tiktok_client_key", "tiktok_client_secret", "facebook_app_id", "facebook_app_secret", "service_api_key"}
    for k, v in body.items():
        if k not in allowed:
            _bad(f"Unknown secret {k}")
        if k == "youtube_client_secret" and v:
            import json
            try:
                j = json.loads(v)
                if "installed" not in j and "web" not in j:
                    _bad("That JSON is not a Google OAuth client file (expected an 'installed' key: create a Desktop app client).")
            except ValueError:
                _bad("Paste the full contents of the client_secret JSON file.")
        secrets_store.set(k, v)
    return {"ok": True}


@app.post("/api/setup/done")
def setup_done():
    db.set_setting("setup_done", True)
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    return db.all_settings()


@app.put("/api/settings")
def put_settings(body: dict):
    for k, v in body.items():
        if k in config.DEFAULT_SETTINGS or k in ("dry_run", "auto_render_top", "service_base_url", "tiktok_pkce_hex", "setup_done"):
            db.set_setting(k, v)
    return db.all_settings()


@app.get("/api/disk")
def disk_usage():
    return disk.usage()


@app.post("/api/disk/prune")
def disk_prune():
    freed = disk.prune_cache(0)
    for p in (config.CACHE / "frames").glob("*"):
        p.unlink(missing_ok=True)
    return {"freed": freed, **disk.usage()}


@app.get("/api/jobs")
def list_jobs(limit: int = 40):
    return db.rows("jobs", "1=1", order="id DESC")[:limit]


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    return {"cancelled": jobs.cancel(job_id)}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int):
    jobs.retry(job_id)
    return {"ok": True}


# ------------------------------------------------------------------ campaigns

CAMPAIGN_FIELDS = {"name", "creator", "url", "submission_url", "required_names", "required_hashtags", "min_len", "max_len",
                   "platforms", "source_start", "source_end", "watermark_allowed", "submit_minutes", "pay_model", "rate_per_1k",
                   "per_clip_cap", "pool_budget", "min_views", "checklist", "notes", "rules_text", "active"}


def _campaign_summary(c: dict) -> dict:
    c = dict(c)
    c["counts"] = {
        "sources": db.execute("SELECT COUNT(*) c FROM sources WHERE campaign_id=?", (c["id"],)).fetchone()["c"],
        "videos": db.execute("SELECT COUNT(*) c FROM videos WHERE campaign_id=?", (c["id"],)).fetchone()["c"],
        "pending": db.execute("SELECT COUNT(*) c FROM clips WHERE campaign_id=? AND status='pending'", (c["id"],)).fetchone()["c"],
        "approved": db.execute("SELECT COUNT(*) c FROM clips WHERE campaign_id=? AND status='approved'", (c["id"],)).fetchone()["c"],
        "posted": db.execute("SELECT COUNT(*) c FROM posts WHERE campaign_id=? AND status='live'", (c["id"],)).fetchone()["c"],
    }
    if not c.get("checklist"):
        c["checklist"] = rules.DEFAULT_CHECKLIST
    return c


@app.get("/api/campaigns")
def list_campaigns():
    return [_campaign_summary(c) for c in db.rows("campaigns", order="active DESC, id DESC")]


@app.post("/api/campaigns")
def create_campaign(body: dict):
    data = {k: v for k, v in body.items() if k in CAMPAIGN_FIELDS}
    if not data.get("name") or not data.get("creator"):
        _bad("A campaign needs a name and a creator")
    data.setdefault("required_names", [data["creator"]])
    data.setdefault("checklist", rules.DEFAULT_CHECKLIST)
    cid = db.insert("campaigns", data)
    return _campaign_summary(db.get("campaigns", cid))


@app.put("/api/campaigns/{cid}")
def update_campaign(cid: int, body: dict):
    if not db.get("campaigns", cid):
        _404()
    db.update("campaigns", cid, {k: v for k, v in body.items() if k in CAMPAIGN_FIELDS})
    return _campaign_summary(db.get("campaigns", cid))


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: int):
    db.delete("campaigns", cid)
    return {"ok": True}


@app.post("/api/campaigns/parse")
def parse_campaign(body: dict):
    text = body.get("text") or ""
    url = (body.get("url") or "").strip()
    fetched = ""
    if url and not text:
        try:
            import requests
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            import re
            fetched = re.sub(r"<[^>]+>", " ", r.text)
            fetched = re.sub(r"\s+", " ", fetched)
        except Exception as e:  # noqa: BLE001
            return {"fields": {}, "error": f"Could not fetch the page: {e}. Paste the rules text instead."}
    parsed = rules.parse_rules_text(text or fetched)
    if url:
        parsed["url"] = url
    parsed["rules_text"] = text or fetched[:5000]
    return {"fields": parsed}


@app.get("/api/suggestions")
def suggestions():
    return config.SUGGESTED_CREATORS


# ------------------------------------------------------------------ sources

@app.get("/api/campaigns/{cid}/sources")
def list_sources(cid: int):
    out = []
    for s in db.rows("sources", "campaign_id=?", (cid,)):
        s["frame_url"] = f"/media/frame/{s['id']}" if (config.FRAMES / f"source_{s['id']}.jpg").exists() else None
        s["video_count"] = db.execute("SELECT COUNT(*) c FROM videos WHERE source_id=?", (s["id"],)).fetchone()["c"]
        out.append(s)
    return out


@app.post("/api/sources")
def add_source(body: dict):
    cid = int(body.get("campaign_id") or 0)
    if not db.get("campaigns", cid):
        _404("Campaign not found")
    try:
        p = sources.parse(body.get("url") or "")
    except ValueError as e:
        _bad(str(e))
    if p["kind"].endswith("_video"):
        v = pipeline.add_video(cid, p["url"])
        return {"video": v}
    try:
        info = sources.resolve_channel(p["kind"], p["id"])
    except tools.ToolError as e:
        _bad(f"Could not resolve that channel: {str(e)[-300:]}")
    existing = db.one("sources", "campaign_id=? AND kind=? AND external_id=?", (cid, p["kind"], info["external_id"]))
    if existing:
        return {"source": existing}
    sid = db.insert("sources", {"campaign_id": cid, "kind": p["kind"], "url": info["url"], "external_id": info["external_id"],
                                "name": info["name"], "handle": info.get("handle"), "watch": 1 if body.get("watch", True) else 0,
                                "layout": "full"})
    if body.get("check_now", True):
        jobs.enqueue("check_sources", {"campaign_id": cid})
    return {"source": db.get("sources", sid)}


@app.put("/api/sources/{sid}")
def update_source(sid: int, body: dict):
    if not db.get("sources", sid):
        _404()
    data = {k: v for k, v in body.items() if k in ("facecam", "layout", "watch", "name")}
    if "facecam" in data and data["facecam"]:
        fc = data["facecam"]
        if not all(k in fc for k in ("x", "y", "w", "h")) or fc["w"] <= 0.02 or fc["h"] <= 0.02:
            _bad("Facecam box is too small")
        data.setdefault("layout", "stacked")
    db.update("sources", sid, data)
    return db.get("sources", sid)


@app.delete("/api/sources/{sid}")
def delete_source(sid: int):
    db.delete("sources", sid)
    return {"ok": True}


@app.post("/api/sources/{sid}/check")
def check_source(sid: int):
    s = db.get("sources", sid) or _404()
    return {"job_id": jobs.enqueue("check_sources", {"campaign_id": s["campaign_id"]})}


@app.post("/api/sources/{sid}/grab_frame")
def grab_frame(sid: int):
    if not db.get("sources", sid):
        _404()
    return {"job_id": jobs.enqueue("grab_frame", {"source_id": sid})}


@app.get("/api/sources/{sid}/recent")
def source_recent(sid: int):
    s = db.get("sources", sid) or _404()
    try:
        vids = sources.list_recent(s, limit=12)
    except tools.ToolError as e:
        _bad(str(e)[-300:])
    for v in vids:
        v["gta"] = sources.looks_like_gta(v["title"])
        v["added"] = bool(db.one("videos", "campaign_id=? AND platform=? AND external_id=?", (s["campaign_id"], v["platform"], v["external_id"])))
    return vids


# ------------------------------------------------------------------ videos & moments

@app.get("/api/videos")
def list_videos(campaign_id: int | None = None):
    where, params = ("campaign_id=?", (campaign_id,)) if campaign_id else ("1=1", ())
    out = []
    for v in db.rows("videos", where, params, order="id DESC"):
        v["unscanned"] = pipeline.unscanned_chunks(v.get("scanned_ranges") or [], float(v["duration"])) if v.get("duration") else []
        v["clip_counts"] = {r["status"]: r["c"] for r in db.execute(
            "SELECT status, COUNT(*) c FROM clips WHERE video_id=? GROUP BY status", (v["id"],)).fetchall()}
        v["moment_count"] = db.execute("SELECT COUNT(*) c FROM moments WHERE video_id=?", (v["id"],)).fetchone()["c"]
        out.append(v)
    return out


@app.post("/api/videos")
def add_video(body: dict):
    """Add a video and scan it. body.window: 'auto' (default: last 2 hours for VODs over 4 hours), 'last2h', 'last6h',
    'whole', or {'start': seconds, 'end': seconds}. body.override skips the campaign date gate."""
    cid = int(body.get("campaign_id") or 0)
    if not db.get("campaigns", cid):
        _404("Campaign not found")
    try:
        return pipeline.add_video(cid, body.get("url") or "", body.get("source_id"), window=body.get("window"),
                                  override=bool(body.get("override")))
    except ValueError as e:
        _bad(str(e))


@app.post("/api/videos/{vid}/rescan")
def rescan_video(vid: int, body: dict | None = None):
    v = db.get("videos", vid) or _404()
    body = body or {}
    try:
        w = pipeline.parse_window(body.get("window", "auto"))
    except ValueError as e:
        _bad(str(e))
    data = {"status": "queued", "stage": "Waiting to scan", "error": None, "progress": 0, "scan_window": w}
    if body.get("override"):
        data["override"] = 1
    db.update("videos", vid, data)
    return {"job_id": jobs.enqueue("scan_video", {"video_id": vid, "window": w})}


@app.post("/api/videos/{vid}/scan_rest")
def scan_rest(vid: int):
    v = db.get("videos", vid) or _404()
    if not v.get("duration"):
        _bad("Scan a window first so the video's length is known")
    n = pipeline.scan_rest(vid)
    return {"queued_chunks": n}


@app.delete("/api/videos/{vid}")
def delete_video(vid: int):
    v = db.get("videos", vid) or _404()
    pipeline.cancel_video_jobs(vid)
    for c in db.rows("clips", "video_id=?", (vid,)):
        _delete_clip_files(c)
    db.delete("videos", vid)
    disk.remove_tree(config.CACHE / sources.video_key(v["platform"], v["external_id"]))
    return {"ok": True}


@app.get("/api/videos/{vid}/moments")
def list_moments(vid: int):
    out = []
    for m in db.rows("moments", "video_id=?", (vid,), order="rank"):
        clip = db.one("clips", "moment_id=?", (m["id"],))
        m["clip"] = pipeline.clip_public(clip) if clip else None
        if clip:
            job = db.one("jobs", "type='render_clip' AND status IN ('queued','running') AND payload LIKE ?", (f'%"clip_id": {clip["id"]}%',))
            m["render_job"] = {k: job[k] for k in ("id", "status", "progress", "message")} if job else None
        m["ts"] = _fmt(m["t"])
        m["thumb_url"] = f"/media/moment/{m['id']}" if m.get("thumb") and Path(m["thumb"]).exists() else None
        out.append(m)
    return out


@app.post("/api/videos/{vid}/render_next")
def render_next(vid: int):
    if not db.get("videos", vid):
        _404()
    cid = pipeline.render_next_best(vid)
    if cid is None:
        _bad("Every moment of this video already has a clip")
    return {"clip_id": cid}


@app.post("/api/clips/{cid}/cancel")
def cancel_render(cid: int):
    clip = db.get("clips", cid) or _404()
    if clip["status"] != "rendering":
        _bad("Only rendering clips can be cancelled")
    n = pipeline.cancel_clip_jobs(cid)
    if n == 0:  # no job found (e.g. lost on restart): clean up directly
        if clip.get("moment_id"):
            db.update("moments", clip["moment_id"], {"status": "found"})
        db.delete("clips", cid)
    return {"cancelled": True}


@app.get("/media/moment/{mid}")
def media_moment(mid: int):
    m = db.get("moments", mid) or _404()
    if not m.get("thumb") or not Path(m["thumb"]).exists():
        _404("No thumbnail")
    return FileResponse(m["thumb"], media_type="image/jpeg")


@app.post("/api/moments/{mid}/render")
def render_moment(mid: int):
    m = db.get("moments", mid) or _404()
    existing = db.one("clips", "moment_id=? AND status!='error'", (mid,))
    if existing:
        return {"clip_id": existing["id"], "existing": True}
    return {"clip_id": pipeline.queue_render(mid)}


@app.post("/api/videos/{vid}/render_custom")
def render_custom(vid: int, body: dict):
    """Render a clip at a time you choose (seconds into the source)."""
    v = db.get("videos", vid) or _404()
    t = float(body.get("t") or 0)
    mid = db.insert("moments", {"video_id": vid, "t": t, "score": 0, "reasons": ["Picked by you"], "chat_words": [], "rank": 99})
    return {"clip_id": pipeline.queue_render(mid)}


def _fmt(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


# ------------------------------------------------------------------ clips & review

def _clip_full(c: dict) -> dict:
    c = pipeline.clip_public(c)
    video = db.get("videos", c["video_id"])
    campaign = db.get("campaigns", c["campaign_id"])
    moment = db.get("moments", c["moment_id"]) if c.get("moment_id") else None
    c["video"] = {k: video.get(k) for k in ("id", "title", "url", "platform", "published_at", "channel")} if video else None
    c["moment"] = moment
    c["campaign_name"] = campaign["name"] if campaign else ""
    c["checklist_items"] = (campaign or {}).get("checklist") or rules.DEFAULT_CHECKLIST
    c["effective_duration"] = _effective_duration(c)
    c["posts"] = db.rows("posts", "clip_id=?", (c["id"],))
    return c


def _effective_duration(c: dict) -> float | None:
    if c.get("duration") is None:
        return None
    start = float(c.get("trim_start") or 0)
    end = float(c.get("trim_end") or c["duration"])
    return max(0.0, end - start)


def _revalidate(clip_id: int) -> dict:
    clip = db.get("clips", clip_id)
    campaign = db.get("campaigns", clip["campaign_id"])
    video = db.get("videos", clip["video_id"])
    check = dict(clip)
    check["duration"] = _effective_duration(clip) if clip.get("final_path") is None else clip["duration"]
    res = rules.validate_clip(check, campaign, video)
    db.update("clips", clip_id, {"rule_results": res})
    return db.get("clips", clip_id)


@app.get("/api/clips")
def list_clips(campaign_id: int | None = None, status: str | None = None, video_id: int | None = None):
    where, params = ["1=1"], []
    if campaign_id:
        where.append("campaign_id=?"); params.append(campaign_id)
    if status:
        where.append("status IN (" + ",".join("?" * len(status.split(","))) + ")"); params += status.split(",")
    if video_id:
        where.append("video_id=?"); params.append(video_id)
    return [_clip_full(c) for c in db.rows("clips", " AND ".join(where), tuple(params), order="id DESC")]


@app.get("/api/clips/{cid}")
def get_clip(cid: int):
    c = db.get("clips", cid) or _404()
    return _clip_full(c)


@app.put("/api/clips/{cid}")
def update_clip(cid: int, body: dict):
    clip = db.get("clips", cid) or _404()
    if clip["status"] == "posted":
        _bad("This clip has been posted; edits are locked.")
    data: dict[str, Any] = {}
    for k in ("hook", "title", "post_text", "hashtags", "checklist", "trim_start", "trim_end"):
        if k in body:
            data[k] = body[k]
    if "trim_start" in data or "trim_end" in data:
        ts = float(data.get("trim_start", clip.get("trim_start") or 0))
        te = float(data.get("trim_end", clip.get("trim_end") or clip.get("duration") or 0))
        if clip.get("duration"):
            ts = max(0.0, min(ts, clip["duration"]))
            te = max(ts + 1, min(te, clip["duration"]))
        data["trim_start"], data["trim_end"] = ts, te
        data["final_path"] = None
    if "hook" in data and "post_text" not in data:
        campaign = db.get("campaigns", clip["campaign_id"])
        moment = db.get("moments", clip["moment_id"]) if clip.get("moment_id") else None
        data["post_text"] = captions.build_all(campaign, data["hook"], (moment or {}).get("chat_words") or [])
        data["title"] = data["post_text"].get("youtube", {}).get("title", data["hook"])
    if clip["status"] == "approved":
        data["status"] = "pending"
    db.update("clips", cid, data)
    return _clip_full(_revalidate(cid))


@app.get("/api/clips/{cid}/hooks")
def clip_hooks(cid: int):
    clip = db.get("clips", cid) or _404()
    return captions.hook_candidates(clip.get("transcript") or [], float(clip.get("trim_start") or 0))[:8]


@app.post("/api/clips/{cid}/approve")
def approve_clip(cid: int, body: dict | None = None):
    clip = db.get("clips", cid) or _404()
    if clip["status"] not in ("pending", "rejected", "approved"):
        _bad(f"Clip is {clip['status']}")
    campaign = db.get("campaigns", clip["campaign_id"])
    if body and "checklist" in body:
        db.update("clips", cid, {"checklist": body["checklist"]})
    clip = _revalidate(cid)
    failed = [r for r in clip["rule_results"] if not r["ok"]]
    if failed:
        raise HTTPException(409, {"message": "Rule checks fail; fix them before approving.", "failed": failed})
    if not rules.checklist_complete(campaign, clip.get("checklist") or {}):
        raise HTTPException(409, {"message": "Tick every item on the review checklist first.", "failed": []})
    db.update("clips", cid, {"status": "approved"})
    jobs.enqueue("finalize_clip", {"clip_id": cid})
    return _clip_full(db.get("clips", cid))


@app.post("/api/clips/{cid}/reject")
def reject_clip(cid: int):
    clip = db.get("clips", cid) or _404()
    if clip["status"] == "posted":
        _bad("Posted clips cannot be rejected")
    db.update("clips", cid, {"status": "rejected"})
    return _clip_full(db.get("clips", cid))


@app.post("/api/clips/{cid}/rerender")
def rerender_clip(cid: int, body: dict | None = None):
    clip = db.get("clips", cid) or _404()
    if clip["status"] == "posted":
        _bad("Posted clips cannot be re-rendered")
    data = {"status": "rendering", "final_path": None, "error": None}
    if body and body.get("shift") is not None:
        data["source_start"] = max(0.0, float(clip["source_start"]) + float(body["shift"]))
    db.update("clips", cid, data)
    return {"job_id": jobs.enqueue("render_clip", {"clip_id": cid})}


def _delete_clip_files(c: dict) -> None:
    for k in ("path", "final_path", "thumb"):
        if c.get(k):
            Path(c[k]).unlink(missing_ok=True)


@app.delete("/api/clips/{cid}")
def delete_clip(cid: int, force: bool = False):
    clip = db.get("clips", cid) or _404()
    if clip["status"] == "posted" and not force:
        raise HTTPException(409, {"message": "This clip has been posted and may be submitted to the campaign. Deleting the file here does not delete the post. Confirm to continue."})
    _delete_clip_files(clip)
    db.delete("clips", cid)
    return {"ok": True}


@app.post("/api/clips/{cid}/reveal")
def reveal_clip(cid: int):
    clip = db.get("clips", cid) or _404()
    p = clip.get("final_path") or clip.get("path")
    if not p or not Path(p).exists():
        _bad("No file yet")
    _reveal(Path(p))
    return {"ok": True, "path": p}


def _reveal(p: Path) -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", str(p)])
        elif platform.system() == "Windows":
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ accounts

ACCOUNT_FIELDS = {"platform", "label", "route", "campaign_ids", "handle"}


@app.get("/api/accounts")
def list_accounts():
    out = []
    for a in db.rows("accounts"):
        try:
            pub = get_publisher(a)
            a["options_schema"] = pub.options_schema()
            a["route_notes"] = pub.notes
            a["upload_page"] = pub.upload_page()
            a["native_schedule"] = pub.can_schedule_natively
        except ValueError:
            a["options_schema"], a["route_notes"] = [], ""
        out.append(a)
    return out


@app.get("/api/routes")
def routes():
    return route_info()


@app.post("/api/accounts")
def create_account(body: dict):
    data = {k: v for k, v in body.items() if k in ACCOUNT_FIELDS}
    if data.get("platform") not in ("youtube", "tiktok", "instagram", "x"):
        _bad("platform must be youtube, tiktok, instagram or x")
    data.setdefault("route", "assisted")
    data.setdefault("label", f"{data['platform']} ({data['route']})")
    if data["route"] == "assisted":
        data["status"] = "ready"
        data["status_detail"] = "Assisted posting needs no connection"
    try:
        get_publisher(data)
    except ValueError as e:
        _bad(str(e))
    aid = db.insert("accounts", data)
    return db.get("accounts", aid)


@app.put("/api/accounts/{aid}")
def update_account(aid: int, body: dict):
    if not db.get("accounts", aid):
        _404()
    db.update("accounts", aid, {k: v for k, v in body.items() if k in ACCOUNT_FIELDS})
    return db.get("accounts", aid)


@app.delete("/api/accounts/{aid}")
def delete_account(aid: int):
    for key in (f"youtube_token_{aid}", f"tiktok_token_{aid}", f"instagram_token_{aid}"):
        secrets_store.delete(key)
    db.delete("accounts", aid)
    return {"ok": True}


@app.post("/api/accounts/{aid}/connect")
def connect_account(aid: int):
    a = db.get("accounts", aid) or _404()
    pub = get_publisher(a)
    if a["platform"] == "youtube" and a["route"] == "direct":
        # Google's loopback flow blocks until the browser returns; run it in a worker thread.
        import threading

        def go():
            try:
                res = pub.connect()
                db.update("accounts", aid, {"status": "connected" if res.get("ok") else "error", "status_detail": res.get("detail", "")})
            except Exception as e:  # noqa: BLE001
                db.update("accounts", aid, {"status": "error", "status_detail": str(e)[:300]})
        db.update("accounts", aid, {"status": "connecting", "status_detail": "A Google sign-in tab opened; approve access there."})
        threading.Thread(target=go, daemon=True).start()
        return {"started": True}
    try:
        res = pub.connect()
    except Exception as e:  # noqa: BLE001
        db.update("accounts", aid, {"status": "error", "status_detail": str(e)[:300]})
        _bad(str(e))
    if res.get("auth_url"):
        db.update("accounts", aid, {"status": "connecting", "status_detail": res.get("detail") or "Approve access in the tab that opened."})
        return res
    db.update("accounts", aid, {"status": "connected" if res.get("ok", True) else "error", "status_detail": res.get("detail", "")})
    return res


@app.post("/api/accounts/{aid}/test")
def test_account(aid: int):
    a = db.get("accounts", aid) or _404()
    pub = get_publisher(a)
    ok, detail = pub.verify()
    db.update("accounts", aid, {"status": "connected" if ok else "error", "status_detail": detail})
    return {"ok": ok, "detail": detail, "account": db.get("accounts", aid)}


@app.get("/oauth/callback/{platform}/", response_class=HTMLResponse)
@app.get("/oauth/callback/{platform}", response_class=HTMLResponse)
def oauth_callback(platform: str, request: Request):
    q = dict(request.query_params)
    pending = oauth_state.pop_state(q.get("state", ""))
    page = "<html><body style='font-family:sans-serif;background:#1a0b3a;color:#eee;padding:40px'><h2>{}</h2><p>{}</p><p>You can close this tab and go back to Clip Dashboard.</p></body></html>"
    if not pending:
        return page.format("Connection expired", "Start the connection again from the Accounts page.")
    aid = pending["account_id"]
    if q.get("error"):
        db.update("accounts", aid, {"status": "error", "status_detail": f"{q.get('error')}: {q.get('error_description', '')}"})
        return page.format("Connection refused", q.get("error_description") or q.get("error"))
    a = db.get("accounts", aid)
    try:
        res = get_publisher(a).finish_connect(q.get("code", ""), pending)
        db.update("accounts", aid, {"status": "connected" if res.get("ok") else "error", "status_detail": res.get("detail", "")})
        return page.format("Connected", res.get("detail", ""))
    except Exception as e:  # noqa: BLE001
        db.update("accounts", aid, {"status": "error", "status_detail": str(e)[:300]})
        return page.format("Connection failed", str(e)[:500])


# ------------------------------------------------------------------ posts, scheduling, submissions

def _post_full(p: dict) -> dict:
    p = dict(p)
    clip = db.get("clips", p["clip_id"])
    acct = db.get("accounts", p["account_id"])
    camp = db.get("campaigns", p["campaign_id"])
    p["clip"] = pipeline.clip_public(clip) if clip else None
    p["account"] = {k: acct.get(k) for k in ("id", "label", "platform", "route", "handle", "upload_page")} if acct else None
    if acct:
        try:
            p["account"]["upload_page"] = get_publisher(acct).upload_page()
        except ValueError:
            pass
    p["campaign"] = {k: camp.get(k) for k in ("id", "name", "submission_url", "submit_minutes", "pay_model", "rate_per_1k", "per_clip_cap", "pool_budget", "min_views")} if camp else None
    p["earnings_estimate"] = runner.estimate_earnings(camp, int(p.get("views") or 0)) if camp else 0
    return p


@app.get("/api/posts")
def list_posts(campaign_id: int | None = None, status: str | None = None):
    where, params = ["1=1"], []
    if campaign_id:
        where.append("campaign_id=?"); params.append(campaign_id)
    if status:
        where.append("status IN (" + ",".join("?" * len(status.split(","))) + ")"); params += status.split(",")
    return [_post_full(p) for p in db.rows("posts", " AND ".join(where), tuple(params), order="COALESCE(scheduled_at, created_at) DESC")]


@app.post("/api/posts")
def create_posts(body: dict):
    """Post now or schedule one approved clip to one or more accounts. Each (clip, account) pair is one post."""
    clip = db.get("clips", int(body.get("clip_id") or 0)) or _404("Clip not found")
    if clip["status"] not in ("approved", "posted"):
        _bad("Approve the clip first")
    campaign = db.get("campaigns", clip["campaign_id"])
    failed = [r for r in (clip.get("rule_results") or []) if not r["ok"]]
    if failed:
        _bad("Rule checks fail: " + "; ".join(r["detail"] for r in failed))
    mode = body.get("mode", "now")
    scheduled_at, tz = None, body.get("tz")
    if mode == "schedule":
        scheduled_at = _to_utc_iso(body.get("scheduled_at") or "", tz)
        if not scheduled_at:
            _bad("Pick a date and time")
    account_ids = [int(a) for a in body.get("account_ids") or []]
    if not account_ids:
        _bad("Pick at least one account")
    created = []
    seen_platforms = set()
    for aid in account_ids:
        acct = db.get("accounts", aid) or _404(f"Account {aid} not found")
        if not rules.platform_allowed(campaign, acct["platform"]):
            _bad(f"The campaign does not allow {acct['platform']}")
        if acct["platform"] in seen_platforms:
            _bad(f"Only one {acct['platform']} account per clip: posting the same clip to several accounts on one platform counts as reposting.")
        seen_platforms.add(acct["platform"])
        dup = db.one("posts", "clip_id=? AND platform=? AND status NOT IN ('failed','cancelled')", (clip["id"], acct["platform"]))
        if dup:
            _bad(f"This clip already has a {acct['platform']} post (#{dup['id']}).")
        pt = (clip.get("post_text") or {}).get(acct["platform"]) or {}
        text = (body.get("post_text") or {}).get(str(aid)) or pt.get("text") or ""
        title = (body.get("titles") or {}).get(str(aid)) or pt.get("title") or clip.get("title") or ""
        full = f"{title}\n{text}"
        for n in campaign.get("required_names") or [campaign["creator"]]:
            if n and not rules._contains(full, n):
                _bad(f"{acct['platform']} post text must mention {n}")
        for t in campaign.get("required_hashtags") or []:
            if t and not rules._contains(full, t):
                _bad(f"{acct['platform']} post text must include {t}")
        options = (body.get("options") or {}).get(str(aid)) or {}
        try:
            pub = get_publisher(acct)
        except ValueError as e:
            _bad(str(e))
        for opt in pub.options_schema():
            if opt.get("type") == "select" and opt.get("default") is None and not options.get(opt["key"]) and not opt.get("required_for"):
                _bad(f"{acct['label']}: choose {opt['label']}")
            if opt.get("required_for") and all(options.get(k, "direct") == v for k, v in opt["required_for"].items()) and not options.get(opt["key"]):
                _bad(f"{acct['label']}: choose {opt['label']}")
        status = "scheduled" if scheduled_at else "publishing"
        pid = db.insert("posts", {"clip_id": clip["id"], "account_id": aid, "campaign_id": campaign["id"], "platform": acct["platform"],
                                  "route": acct["route"], "status": status, "scheduled_at": scheduled_at, "tz": tz,
                                  "post_text": text, "title": title, "options": options,
                                  "state_detail": "Scheduled (fires while Clip Dashboard is running)" if scheduled_at and not pub.can_schedule_natively
                                  else ("Scheduled natively" if scheduled_at else "Queued to post")})
        if not scheduled_at or pub.can_schedule_natively:
            jobs.enqueue("publish_post", {"post_id": pid})
        created.append(_post_full(db.get("posts", pid)))
    return created


def _to_utc_iso(local: str, tz: str | None) -> str | None:
    if not local:
        return None
    try:
        d = datetime.fromisoformat(local)
    except ValueError:
        return None
    if d.tzinfo is None:
        from zoneinfo import ZoneInfo
        try:
            d = d.replace(tzinfo=ZoneInfo(tz) if tz else timezone.utc)
        except Exception:  # noqa: BLE001
            d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@app.post("/api/posts/{pid}/retry")
def retry_post(pid: int):
    p = db.get("posts", pid) or _404()
    if p["status"] not in ("failed", "manual", "private", "draft"):
        _bad("Only failed posts can be retried")
    db.update("posts", pid, {"status": "publishing", "error": None, "state_detail": "Retrying"})
    return {"job_id": jobs.enqueue("publish_post", {"post_id": pid})}


@app.post("/api/posts/{pid}/cancel")
def cancel_post(pid: int, force: bool = False):
    p = db.get("posts", pid) or _404()
    if p.get("submitted_at") and not force:
        raise HTTPException(409, {"message": "This post was submitted to the campaign. Editing or deleting a submitted post gets it rejected. Cancel anyway?"})
    if p["status"] == "live":
        _bad("The post is already live; delete it on the platform only if you accept losing the submission.")
    db.update("posts", pid, {"status": "cancelled", "state_detail": "Cancelled"})
    return _post_full(db.get("posts", pid))


@app.post("/api/posts/{pid}/mark_live")
def mark_live(pid: int, body: dict):
    p = db.get("posts", pid) or _404()
    url = (body.get("url") or "").strip()
    if not url.startswith("http"):
        _bad("Paste the full link to the post")
    return _post_full(runner.mark_live(pid, url))


@app.post("/api/posts/{pid}/submitted")
def mark_submitted(pid: int):
    p = db.get("posts", pid) or _404()
    db.update("posts", pid, {"submitted_at": db.now()})
    return _post_full(db.get("posts", pid))


@app.post("/api/posts/{pid}/check")
def check_post_now(pid: int):
    return {"job_id": jobs.enqueue("check_post", {"post_id": pid})}


@app.delete("/api/posts/{pid}")
def delete_post(pid: int, force: bool = False):
    p = db.get("posts", pid) or _404()
    if (p.get("submitted_at") or p["status"] == "live") and not force:
        raise HTTPException(409, {"message": "This post is live or submitted. Removing it here does not remove it on the platform; a deleted platform post gets the submission rejected. Remove the record anyway?"})
    db.delete("posts", pid)
    return {"ok": True}


# ------------------------------------------------------------------ stats

@app.get("/api/stats")
def stats(campaign_id: int | None = None):
    where, params = ("campaign_id=?", (campaign_id,)) if campaign_id else ("1=1", ())
    posts = [p for p in db.rows("posts", where + " AND status='live'", params)]
    by_campaign: dict[int, dict] = {}
    for p in posts:
        c = by_campaign.setdefault(p["campaign_id"], {"views": 0, "posts": 0, "earnings": 0.0})
        camp = db.get("campaigns", p["campaign_id"]) or {}
        c["views"] += int(p.get("views") or 0)
        c["posts"] += 1
        c["earnings"] += runner.estimate_earnings(camp, int(p.get("views") or 0))
        c["campaign"] = {k: camp.get(k) for k in ("id", "name", "pay_model", "rate_per_1k", "per_clip_cap", "pool_budget", "min_views")}
    return {"campaigns": list(by_campaign.values()), "posts": [_post_full(p) for p in posts]}


@app.post("/api/stats/refresh")
def refresh_stats(campaign_id: int | None = None):
    return {"job_id": jobs.enqueue("refresh_stats", {"campaign_id": campaign_id})}


# ------------------------------------------------------------------ media & UI

@app.get("/media/clip/{cid}")
def media_clip(cid: int, final: bool = False):
    c = db.get("clips", cid) or _404()
    p = c.get("final_path") if final and c.get("final_path") else c.get("path")
    if not p or not Path(p).exists():
        _404("No file")
    return FileResponse(p, media_type="video/mp4")


@app.get("/media/thumb/{cid}")
def media_thumb(cid: int):
    c = db.get("clips", cid) or _404()
    if not c.get("thumb") or not Path(c["thumb"]).exists():
        _404("No thumbnail")
    return FileResponse(c["thumb"], media_type="image/jpeg")


@app.get("/media/frame/{sid}")
def media_frame(sid: int):
    p = config.FRAMES / f"source_{sid}.jpg"
    if not p.exists():
        _404("No frame yet")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/", response_class=HTMLResponse)
def index():
    return (config.WEB / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(config.WEB)), name="static")


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)
