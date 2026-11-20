"""HTTP API and static UI. Everything the browser does goes through here."""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, captions, config, db, disk, errors, hooks, jobs, pipeline, rules, secrets_store, sources, tools, tts
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
        "watch_interval_min": db.get_setting("watch_interval_min", 10),
    }


@app.get("/api/today")
def today(campaign_id: int | None = None):
    """The Today checklist: what is waiting for the person, in the order of the day's work."""
    campaign = db.get("campaigns", campaign_id) if campaign_id else None
    cw, params = ("campaign_id=?", (campaign_id,)) if campaign_id else ("1=1", ())
    pending = db.rows("clips", f"{cw} AND status='pending'", params, order="id DESC")
    best = None
    if pending:
        def _score(c):
            m = db.get("moments", c["moment_id"]) if c.get("moment_id") else None
            return float((m or {}).get("score") or 0)
        b = max(pending, key=_score)
        best = {"id": b["id"], "hook": b.get("hook") or b.get("title") or "", "source_start": b.get("source_start"),
                "thumb_url": f"/media/thumb/{b['id']}" if b.get("thumb") and Path(b["thumb"]).exists() else None}
    approved = db.execute(f"SELECT COUNT(*) c FROM clips WHERE {cw} AND status='approved'", params).fetchone()["c"]
    due = db.rows("posts", f"{cw} AND status='live' AND submitted_at IS NULL AND submission_deadline IS NOT NULL", params,
                  order="submission_deadline")
    new_videos = []
    for v in db.rows("videos", f"{cw} AND status='new'", params, order="id DESC")[:20]:
        src = db.get("sources", v["source_id"]) if v.get("source_id") else None
        new_videos.append({"id": v["id"], "title": v.get("title") or v["url"], "source": (src or {}).get("name"), "live": bool(v.get("live")),
                           "duration": v.get("duration"), "published_at": v.get("published_at"), "platform": v["platform"]})
    srcs = []
    for s in db.rows("sources", f"{cw} AND watch=1", params, order="id"):
        live = db.execute("SELECT COUNT(*) c FROM videos WHERE source_id=? AND live=1 AND status NOT IN ('done','error','skipped')",
                          (s["id"],)).fetchone()["c"]
        srcs.append({"id": s["id"], "name": s.get("name") or s["url"], "kind": s["kind"], "live": bool(live),
                     "last_checked": s.get("last_checked")})
    attention = []
    active_job = lambda t, key, val: db.one("jobs", f"type='{t}' AND status IN ('queued','running') AND payload LIKE ?", (f'%"{key}": {val}%',))
    for v in db.rows("videos", f"{cw} AND status IN ('error','scanning','queued')", params, order="id DESC"):
        if v["status"] == "error":
            attention.append({"kind": "video", "id": v["id"], "title": v.get("title") or v["url"], "reason": _reason(v.get("error")),
                              "retry": f"/api/videos/{v['id']}/rescan"})
        elif not active_job("scan_video", "video_id", v["id"]):
            attention.append({"kind": "video", "id": v["id"], "title": v.get("title") or v["url"], "reason": "Interrupted — retry",
                              "retry": f"/api/videos/{v['id']}/rescan"})
    for c in db.rows("clips", f"{cw} AND status IN ('error','rendering')", params, order="id DESC"):
        video = db.get("videos", c["video_id"]) or {}
        title = c.get("hook") or c.get("title") or f"{video.get('title') or 'clip'} at {_fmt(c.get('source_start') or 0)}"
        if c["status"] == "error":
            attention.append({"kind": "clip", "id": c["id"], "title": title, "reason": _reason(c.get("error")), "retry": f"/api/clips/{c['id']}/rerender"})
        elif not active_job("render_clip", "clip_id", c["id"]):
            attention.append({"kind": "clip", "id": c["id"], "title": title, "reason": "Interrupted — retry", "retry": f"/api/clips/{c['id']}/rerender"})
    for p in db.rows("posts", f"{cw} AND status='failed'", params, order="id DESC"):
        clip = db.get("clips", p["clip_id"]) or {}
        attention.append({"kind": "post", "id": p["id"], "title": f"{p['platform']}: {clip.get('hook') or p.get('title') or ''}",
                          "reason": _reason(p.get("error") or p.get("state_detail")), "retry": f"/api/posts/{p['id']}/retry"})
    for j in db.rows("jobs", "status='error' AND type NOT IN ('scan_video','render_clip','publish_post','finalize_clip')", (), order="id DESC")[:5]:
        attention.append({"kind": "job", "id": j["id"], "title": j["type"].replace("_", " "), "reason": _reason(j.get("error")),
                          "retry": f"/api/jobs/{j['id']}/retry"})
    return {
        "review": {"count": len(pending), "best": best}, "publish": {"count": approved},
        "submissions": {"count": len(due), "soonest": due[0]["submission_deadline"] if due else None, "soonest_post_id": due[0]["id"] if due else None},
        "sources": srcs, "attention": attention, "new_videos": new_videos,
        "workspace": bool(campaign and campaign.get("kind") == "streamer"),
    }


def _reason(err) -> str:
    """One plain sentence for the UI; the raw text stays on the row."""
    return errors.friendly(err)


@app.get("/api/setup/checks")
def setup_checks():
    llm_ok, llm_why = hooks.available()
    return {"checks": tools.check_dependencies(), "secrets": {k: secrets_store.masked(k) for k in (
        "youtube_client_secret", "tiktok_client_key", "tiktok_client_secret", "facebook_app_id", "facebook_app_secret", "service_api_key",
        "anthropic_api_key", "openai_api_key", "elevenlabs_api_key")},
        "tts": tts.engines_status(), "llm": {"ok": llm_ok, "detail": llm_why, "provider": hooks.provider()},
        "settings": {k: db.get_setting(k) for k in ("llm_provider", "llm_model", "tts_engine", "piper_voice", "openai_voice", "openai_tts_model", "elevenlabs_voice_id")},
        "voices": tts.voices(),
        "callback_urls": {"tiktok": f"http://127.0.0.1:{config.PORT}/oauth/callback/tiktok/",
                          "instagram": f"http://localhost:{config.PORT}/oauth/callback/instagram/"}}


@app.post("/api/setup/action")
def setup_action(body: dict):
    action = body.get("action")
    if action not in ("install_tdcli", "download_whisper", "download_piper_voice"):
        _bad("Unknown action")
    payload = {"voice": body["voice"]} if body.get("voice") else {}
    return {"job_id": jobs.enqueue(action, payload)}


@app.get("/api/tts/voices")
def tts_voices():
    return tts.voices()


@app.get("/api/tts/sample")
def tts_sample(engine: str, voice: str | None = None):
    """A two-second sample of one voice, cached on disk after the first play."""
    try:
        p = tts.sample(engine, voice or None)
    except tools.ToolError as e:
        raise HTTPException(400, str(e))
    return FileResponse(p, media_type="audio/wav")


@app.post("/api/setup/secrets")
def setup_secrets(body: dict):
    allowed = {"youtube_client_secret", "tiktok_client_key", "tiktok_client_secret", "facebook_app_id", "facebook_app_secret", "service_api_key",
               "anthropic_api_key", "openai_api_key", "elevenlabs_api_key"}
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
        if k in config.DEFAULT_SETTINGS or k in ("dry_run", "auto_render_top", "service_base_url", "tiktok_pkce_hex", "setup_done",
                                                  "elevenlabs_voice_id", "elevenlabs_model", "system_voice", "openai_voice", "openai_tts_model"):
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
    out = db.rows("jobs", "1=1", order="id DESC")[:limit]
    for j in out:
        j["error_text"] = errors.friendly(j["error"]) if j["status"] == "error" else None
    return out


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
                   "per_clip_cap", "pool_budget", "min_views", "checklist", "notes", "rules_text", "active", "ai_narration", "bans_ai"}


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
        c["checklist"] = db.WORKSPACE_CHECKLIST if db.is_workspace(c) else rules.DEFAULT_CHECKLIST
    c["workspace"] = db.is_workspace(c)
    ok, why = rules.narration_allowed(c)
    c["narration"] = {"allowed": ok, "reason": why}
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
    if data.get("bans_ai"):
        data["ai_narration"] = 0
    cid = db.insert("campaigns", data)
    return _campaign_summary(db.get("campaigns", cid))


@app.put("/api/campaigns/{cid}")
def update_campaign(cid: int, body: dict):
    c = db.get("campaigns", cid) or _404()
    allowed = {"platforms", "checklist", "notes"} if db.is_workspace(c) else CAMPAIGN_FIELDS
    data = {k: v for k, v in body.items() if k in allowed}
    if data.get("bans_ai", c.get("bans_ai")):
        data["ai_narration"] = 0
    db.update("campaigns", cid, data)
    return _campaign_summary(db.get("campaigns", cid))


@app.delete("/api/campaigns/{cid}")
def delete_campaign(cid: int):
    c = db.get("campaigns", cid) or _404()
    if db.is_workspace(c):
        _bad("The Streamer clips workspace cannot be deleted; remove its sources instead.")
    db.delete("campaigns", cid)
    return {"ok": True}


def _creator_campaign_conflict(cid: int, info: dict, p: dict) -> dict | None:
    """A creator with an active campaign must be clipped inside that campaign (its rules and submissions apply)."""
    needles = {str(x).lower().lstrip("@") for x in (info.get("external_id"), info.get("handle"), info.get("name"), p.get("id")) if x}
    for c in db.rows("campaigns", "active=1 AND kind='campaign' AND id!=?", (cid,)):
        if c.get("creator") and c["creator"].lower() in needles:
            return c
        for src in db.rows("sources", "campaign_id=?", (c["id"],)):
            ids = {str(x).lower().lstrip("@") for x in (src.get("external_id"), src.get("handle"), src.get("name")) if x}
            if ids & needles:
                return c
    return None


@app.post("/api/campaigns/parse")
def parse_campaign(body: dict):
    """Pre-fill campaign fields from a campaign page URL or pasted rules text. Scripts and styles are never read."""
    from . import campaign_import
    text = body.get("text") or ""
    url = (body.get("url") or "").strip()
    if url and not text:
        try:
            import requests
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; ClipDashboard)"})
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            return {"fields": {"url": url, "submission_url": url}, "error": f"Could not fetch the page: {e}. Paste the rules text instead."}
        return {"fields": campaign_import.fields_from_page(r.text, url)}
    if "<" in text and re.search(r"<(html|body|div|script|p)\b", text, re.I):
        text = campaign_import.extract_page(text)["text"]
    parsed = rules.parse_rules_text(text)
    if url:
        parsed["url"] = url
        parsed.setdefault("submission_url", url)
    parsed["rules_text"] = text[:5000]
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
        if db.is_workspace(db.get("campaigns", cid)) and not body.get("clip_terms_ok"):
            _bad("Tick “This creator publicly allows clipping” before adding a video to the Streamer clips workspace.")
        v = pipeline.add_video(cid, p["url"], window=body.get("window"), queue=bool(body.get("scan_now")))
        return {"video": v}
    campaign = db.get("campaigns", cid)
    workspace = db.is_workspace(campaign)
    if workspace and not body.get("clip_terms_ok"):
        _bad("Tick “This creator publicly allows clipping” before adding a source to the Streamer clips workspace.")
    try:
        info = sources.resolve_channel(p["kind"], p["id"])
    except tools.ToolError as e:
        _bad(f"Could not resolve that channel: {str(e)[-300:]}")
    if workspace:
        conflict = _creator_campaign_conflict(cid, info, p)
        if conflict:
            raise HTTPException(409, {"message": f"{info['name']} has an active campaign here (“{conflict['name']}”). Add the source to that campaign so its rules and submissions apply.", "campaign_id": conflict["id"]})
    existing = db.one("sources", "campaign_id=? AND kind=? AND external_id=?", (cid, p["kind"], info["external_id"]))
    if existing:
        return {"source": existing}
    sid = db.insert("sources", {"campaign_id": cid, "kind": p["kind"], "url": info["url"], "external_id": info["external_id"],
                                "name": info["name"], "handle": info.get("handle"), "watch": 1 if body.get("watch", True) else 0,
                                "layout": "full", "clip_terms_ok": 1 if body.get("clip_terms_ok") else 0,
                                "terms_url": (body.get("terms_url") or "").strip() or None,
                                "caption_template": (body.get("caption_template") or "").strip() or None})
    if body.get("check_now", True):
        jobs.enqueue("check_sources", {"campaign_id": cid})
    return {"source": db.get("sources", sid)}


@app.put("/api/sources/{sid}")
def update_source(sid: int, body: dict):
    if not db.get("sources", sid):
        _404()
    data = {k: v for k, v in body.items() if k in ("facecam", "layout", "watch", "name", "terms_url", "caption_template", "clip_terms_ok")}
    if "facecam" in data and data["facecam"]:
        fc = data["facecam"]
        if fc.get("none"):            # the person confirmed "no facecam": remembered so Review stops asking
            data["facecam"], data["layout"] = {"none": True}, "full"
        elif not all(k in fc for k in ("x", "y", "w", "h")) or fc["w"] <= 0.02 or fc["h"] <= 0.02:
            _bad("Facecam box is too small")
        else:
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

def _video_rows(where: str, params: tuple) -> list[dict]:
    out = []
    for v in db.rows("videos", where, params, order="id DESC"):
        v["unscanned"] = pipeline.unscanned_chunks(v.get("scanned_ranges") or [], float(v["duration"])) if v.get("duration") else []
        v["clip_counts"] = {r["status"]: r["c"] for r in db.execute(
            "SELECT status, COUNT(*) c FROM clips WHERE video_id=? GROUP BY status", (v["id"],)).fetchall()}
        v["moment_count"] = db.execute("SELECT COUNT(*) c FROM moments WHERE video_id=?", (v["id"],)).fetchone()["c"]
        v["error_text"] = errors.friendly(v.get("error")) if v["status"] == "error" else None
        out.append(v)
    return out


@app.get("/api/videos")
def list_videos(campaign_id: int | None = None):
    """Pipeline entries. Videos outside the campaign's footage window are not listed here (see /api/videos/skipped)."""
    where, params = ("status!='skipped'", ())
    if campaign_id:
        where, params = ("campaign_id=? AND status!='skipped'", (campaign_id,))
    return _video_rows(where, params)


@app.get("/api/videos/skipped")
def list_skipped_videos(campaign_id: int | None = None):
    """Videos skipped at add/watch time (outside the campaign window). Revealed by the Pipeline's 'Show skipped' toggle."""
    where, params = ("status='skipped'", ())
    if campaign_id:
        where, params = ("campaign_id=? AND status='skipped'", (campaign_id,))
    return _video_rows(where, params)


@app.post("/api/videos")
def add_video(body: dict):
    """Add a video and scan it. body.window: 'auto' (default: last 2 hours for VODs over 4 hours), 'last2h', 'last6h',
    'whole', or {'start': seconds, 'end': seconds}. body.override skips the campaign date gate."""
    cid = int(body.get("campaign_id") or 0)
    campaign = db.get("campaigns", cid) or _404("Campaign not found")
    if db.is_workspace(campaign) and not body.get("source_id") and not body.get("clip_terms_ok"):
        _bad("Tick “This creator publicly allows clipping” before scanning a video in the Streamer clips workspace.")
    try:
        return pipeline.add_video(cid, body.get("url") or "", body.get("source_id"), window=body.get("window"),
                                  override=bool(body.get("override")))
    except ValueError as e:
        _bad(str(e))


@app.get("/api/videos/{vid}/section")
def video_section(vid: int, refresh: bool = False):
    """Everything the section picker needs: length, live state, scanned ranges, moments and the heat strip so far."""
    v = db.get("videos", vid) or _404()
    if refresh or not v.get("duration"):          # a live stream's VOD keeps growing; the picker asks for a refresh when it opens
        try:
            v = pipeline.refresh_video_info(vid)
        except tools.ToolError:
            pass
    dens = pipeline.load_density(v)
    running = db.one("jobs", "type='chat_density' AND status IN ('queued','running') AND payload LIKE ?", (f'%"video_id": {vid}%',))
    duration = float(v.get("duration") or 0)
    settings = db.all_settings()
    w = v.get("scan_window") if isinstance(v.get("scan_window"), dict) else {}
    return {
        "id": vid, "title": v.get("title"), "url": v["url"], "platform": v["platform"], "duration": duration, "live": bool(v.get("live")), "status": v["status"], "stage": v.get("stage"),
        "progress": v.get("progress"), "scanned_ranges": v.get("scanned_ranges") or [],
        "moments": [{"id": m["id"], "t": m["t"], "score": m["score"], "flags": m.get("flags") or []} for m in db.rows("moments", "video_id=?", (vid,), order="t")],
        "density": {**dens, "complete": pipeline.density_complete(dens, duration) if duration else False, "loading": bool(running),
                    "loading_message": (running or {}).get("message")},
        "clip_len": float(w.get("clip_len") or settings.get("clip_len") or 90), "lead_in": float(w.get("lead_in") if w.get("lead_in") is not None else settings.get("lead_in", 25)),
        "last_section": [w.get("start"), w.get("end")] if w.get("kind") == "range" else None,
    }


@app.post("/api/videos/{vid}/density")
def load_density(vid: int):
    """Start (or continue) loading the heat strip in the background."""
    v = db.get("videos", vid) or _404()
    if pipeline.density_complete(pipeline.load_density(v), float(v.get("duration") or 0)) and v.get("duration") and not v.get("live"):
        return {"job_id": None, "complete": True}
    if db.one("jobs", "type='chat_density' AND status IN ('queued','running') AND payload LIKE ?", (f'%"video_id": {vid}%',)):
        return {"job_id": None, "running": True}
    return {"job_id": jobs.enqueue("chat_density", {"video_id": vid})}


@app.post("/api/videos/{vid}/rescan")
def rescan_video(vid: int, body: dict | None = None):
    v = db.get("videos", vid) or _404()
    body = body or {}
    try:
        w = pipeline.parse_window(body.get("window", "auto"))
        if body.get("preset"):
            s_, e_ = pipeline.section_bounds(float(v.get("duration") or 0), body["preset"])
            w = {**w, "kind": "range", "start": s_, "end": e_}
        for k in ("clip_len", "lead_in"):
            if body.get(k) not in (None, ""):
                w[k] = float(body[k])
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
def render_moment(mid: int, body: dict | None = None):
    """Render one moment. Optional clip_len / lead_in (seconds) from the section picker; else the video's last choice."""
    m = db.get("moments", mid) or _404()
    existing = db.one("clips", "moment_id=? AND status!='error'", (mid,))
    if existing:
        return {"clip_id": existing["id"], "existing": True}
    body = body or {}
    return {"clip_id": pipeline.queue_render(mid, body.get("clip_len"), body.get("lead_in"))}


@app.post("/api/videos/{vid}/render_custom")
def render_custom(vid: int, body: dict):
    """Render a clip at a time you choose (seconds into the source)."""
    v = db.get("videos", vid) or _404()
    t = float(body.get("t") or 0)
    mid = db.insert("moments", {"video_id": vid, "t": t, "score": 0, "reasons": ["Picked by you"], "chat_words": [], "rank": 99})
    return {"clip_id": pipeline.queue_render(mid, body.get("clip_len"), body.get("lead_in"))}


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
    n = c.get("narration") or {}
    c["narration_preview_url"] = f"/media/clip/{c['id']}?narrated=1" if n.get("preview_path") and Path(n["preview_path"]).exists() else None
    # What the Review player and the Publish preview play by default: the narrated file when narration is on and ready.
    c["play_url"] = c["narration_preview_url"] if n.get("enabled") and n.get("status") == "ready" and c["narration_preview_url"] else c["video_url"]
    c["intro_offset"] = float(n.get("intro_duration") or 0) if c["play_url"] == c["narration_preview_url"] else 0.0
    c["narration_allowed"], c["narration_reason"] = rules.narration_allowed(campaign) if campaign else (False, "")
    source = db.get("sources", video["source_id"]) if video and video.get("source_id") else None
    c["source"] = {k: source.get(k) for k in ("id", "name", "facecam", "layout")} if source else None
    if c["source"]:
        c["source"]["facecam_decided"] = bool(c["source"]["facecam"])     # a crop, or a confirmed "no facecam"
        if (c["source"]["facecam"] or {}).get("none"):
            c["source"]["facecam"] = None
    c["rule_count"] = len(c.get("rule_results") or [])
    c["error_text"] = errors.friendly(c.get("error")) if c["status"] == "error" else None
    n = c.get("narration") or {}
    if n.get("status") == "error":
        c["narration"] = {**n, "error_text": errors.friendly(n.get("error"))}
    return c


def _effective_duration(c: dict) -> float | None:
    if c.get("duration") is None:
        return None
    base = float(pipeline.base_duration(c) or c["duration"])
    start = float(c.get("trim_start") or 0)
    end = float(c.get("trim_end") or base)
    n = c.get("narration") or {}
    intro = float(n.get("intro_duration") or 0) if n.get("enabled") else 0.0
    return max(0.0, end - start) + intro


def _revalidate(clip_id: int) -> dict:
    clip = db.get("clips", clip_id)
    campaign = db.get("campaigns", clip["campaign_id"])
    video = db.get("videos", clip["video_id"])
    check = dict(clip)
    check["duration"] = _effective_duration(clip) if clip.get("final_path") is None else clip["duration"]
    check["narration"] = clip.get("narration") or {}
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


@app.post("/api/clips/{cid}/narration/hooks")
def narration_hooks(cid: int):
    clip = db.get("clips", cid) or _404()
    campaign = db.get("campaigns", clip["campaign_id"])
    ok, why = rules.narration_allowed(campaign)
    if not ok:
        _bad(why)
    source = None
    video = db.get("videos", clip["video_id"])
    if video and video.get("source_id"):
        source = db.get("sources", video["source_id"])
    creator = (source or {}).get("name") if db.is_workspace(campaign) else campaign["creator"]
    moment = db.get("moments", clip["moment_id"]) if clip.get("moment_id") else None
    ctx = dict(clip, chat_words=(moment or {}).get("chat_words") or [])
    try:
        generated = hooks.generate_hooks(ctx, creator or "the streamer")
    except Exception as e:  # noqa: BLE001
        _bad(str(e)[:400])
    n = dict(clip.get("narration") or {})
    n["hooks"] = generated
    n.setdefault("chosen", generated[0])
    db.update("clips", cid, {"narration": n})
    return {"hooks": generated, "narration": n}


@app.put("/api/clips/{cid}/narration")
def set_narration(cid: int, body: dict):
    """Choose/edit the hook, pick a voice engine, switch narration on (renders the intro) or off."""
    clip = db.get("clips", cid) or _404()
    if clip["status"] == "posted":
        _bad("This clip has been posted; edits are locked.")
    campaign = db.get("campaigns", clip["campaign_id"])
    n = dict(clip.get("narration") or {})
    if "hook" in body:
        ok, t = hooks.validate_hook(body["hook"] or "")
        if body["hook"] and not ok:
            _bad(t)
        n["chosen"] = t if body["hook"] else ""
    if "engine" in body:
        n["engine"] = body["engine"] or None
    if "voice" in body:
        n["voice"] = body["voice"] or None
    enabled = bool(body.get("enabled", n.get("enabled", False)))
    if enabled:
        ok, why = rules.narration_allowed(campaign)
        if not ok:
            _bad(why)
        if not n.get("chosen"):
            _bad("Pick or write a hook first")
    old = clip.get("narration") or {}
    changed_hook = n.get("chosen") != old.get("chosen") or n.get("engine") != old.get("engine") or n.get("voice") != old.get("voice")
    n["enabled"] = enabled
    data = {"narration": n, "final_path": None, "ai_text": 1 if enabled else 0}
    if clip["status"] == "approved":
        data["status"] = "pending"
    if enabled and (changed_hook or not n.get("intro_path") or not Path(n.get("intro_path", "")).exists()):
        n["status"] = "rendering"
        n["intro_path"] = None
        n["preview_path"] = None
        data["narration"] = n
        db.update("clips", cid, data)
        jobs.enqueue("render_intro", {"clip_id": cid})
    else:
        db.update("clips", cid, data)
    return _clip_full(_revalidate(cid))


@app.post("/api/clips/{cid}/approve")
def approve_clip(cid: int, body: dict | None = None):
    clip = db.get("clips", cid) or _404()
    if clip["status"] not in ("pending", "rejected", "approved"):
        _bad(f"Clip is {clip['status']}")
    campaign = db.get("campaigns", clip["campaign_id"])
    if body and "checklist" in body:
        db.update("clips", cid, {"checklist": body["checklist"]})
    clip = _revalidate(cid)
    n = clip.get("narration") or {}
    if n.get("enabled") and n.get("status") != "ready":
        raise HTTPException(409, {"message": "The narrated intro is still rendering (or failed). Wait for it or switch narration off.", "failed": []})
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
    p["link_state"] = runner.link_poll_state(p)          # 'pending' | 'gave_up' | None
    if p["status"] == "failed":
        raw = p.get("error") or p.get("state_detail")
        p["error_text"] = errors.friendly(raw)
        p["platform_message"] = errors.platform_message(raw)
        p["fix"] = errors.suggest_fix(p["platform"], p["route"], raw)
    else:
        p["error_text"] = p["platform_message"] = p["fix"] = None
    handle = (acct or {}).get("handle") or ""
    p["profile_url"] = _profile_url(p["platform"], handle)
    dl = p.get("submission_deadline")
    p["missed"] = bool(dl and not p.get("submitted_at") and datetime.fromisoformat(dl) <= datetime.now(timezone.utc))
    return p


def _profile_url(platform: str, handle: str) -> str:
    h = handle.lstrip("@")
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{h}" if h else "https://www.tiktok.com/"
    if platform == "instagram":
        return f"https://www.instagram.com/{h}/" if h else "https://www.instagram.com/"
    if platform == "youtube":
        return f"https://www.youtube.com/@{h}" if h else "https://studio.youtube.com/"
    if platform == "x":
        return f"https://x.com/{h}" if h else "https://x.com/"
    return ""


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
        options = dict((body.get("options") or {}).get(str(aid)) or {})
        if (clip.get("narration") or {}).get("enabled"):
            options["synthetic"] = True   # AI voice intro → altered/synthetic content disclosure on every route
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
def media_clip(cid: int, final: bool = False, narrated: bool = False):
    c = db.get("clips", cid) or _404()
    p = c.get("final_path") if final and c.get("final_path") else c.get("path")
    if narrated:
        p = (c.get("narration") or {}).get("preview_path")
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
