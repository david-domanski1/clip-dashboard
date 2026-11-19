"""Pipeline steps run by background jobs: scan a video for moments, render a clip, finalize (trim),
watch sources for new videos. Each step reports progress into the videos/clips/jobs tables."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import captions as cap
from . import chat, config, cut, db, disk, rules, signals, sources, tools


def _video_dir(video: dict) -> Path:
    d = config.CACHE / sources.video_key(video["platform"], video["external_id"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_video(video_id: int, **fields) -> None:
    db.update("videos", video_id, fields)


# ---------------------------------------------------------------- scan

def _auto_subs_cues(url: str, d: Path) -> list[list]:
    """YouTube auto-generated English captions as [[t, text], ...] (json3 format)."""
    for f in d.glob("subs*.json3"):
        f.unlink()
    try:
        tools.run(["yt-dlp", "--skip-download", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "json3",
                   "-o", str(d / "subs"), url], timeout=1800)
    except tools.ToolError:
        return []
    files = list(d.glob("subs*.json3"))
    if not files:
        return []
    try:
        data = json.loads(files[0].read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return []
    cues = []
    for ev in data.get("events", []):
        t = ev.get("tStartMs")
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if t is not None and text and text != "\n":
            cues.append([round(t / 1000.0, 2), text])
    for f in files:
        f.unlink(missing_ok=True)
    return cues


def scan_video(video_id: int, progress) -> None:
    video = db.get("videos", video_id)
    if not video:
        return
    settings = db.all_settings()
    d = _video_dir(video)
    _set_video(video_id, status="scanning", stage="Reading video info", progress=0.05, error=None)
    progress(0.05, "Reading video info")
    info = sources.video_info(video["url"])
    _set_video(video_id, title=info["title"] or video["title"], channel=info["channel"] or video["channel"],
               published_at=info["published_at"] or video["published_at"], duration=info["duration"],
               is_stream=max(int(video.get("is_stream") or 0), info["is_stream"]))
    if info.get("live_now"):
        raise tools.ToolError("This stream is still live. Add it again once the VOD is finished.")
    duration = float(info["duration"] or 0)
    sig: dict = {}
    summary: dict = {"chat_messages": 0, "audio": False, "heatmap": False, "transcript_cues": 0}

    # 1. chat replay (strongest signal)
    rows: list[list] = []
    chat_path = d / "chat.json"
    try:
        if chat_path.exists():
            rows = chat.load(chat_path)
        elif video["platform"] == "twitch":
            _set_video(video_id, stage="Downloading Twitch chat replay", progress=0.1)
            progress(0.1, "Downloading Twitch chat replay")
            chat.download_twitch_chat(video["external_id"], chat_path, on_line=lambda l: progress(None, l[:120]))
            rows = chat.load(chat_path)
        elif info.get("has_live_chat") or video.get("is_stream"):
            _set_video(video_id, stage="Downloading YouTube chat replay", progress=0.1)
            progress(0.1, "Downloading YouTube chat replay")
            if chat.download_youtube_chat(video["url"], chat_path):
                rows = chat.load(chat_path)
    except tools.ToolError as e:
        progress(None, f"Chat unavailable: {str(e)[:200]}")
    if rows:
        sig["chat"] = signals.chat_signal(rows, settings["hype_tokens"], settings["bin_seconds"])
        summary["chat_messages"] = len(rows)

    # 2. YouTube "most replayed" heatmap (regular uploads)
    if info.get("heatmap"):
        sig["heatmap"] = signals.heatmap_signal(info["heatmap"], duration, settings["bin_seconds"])
        summary["heatmap"] = True

    # 3. transcript cues from auto captions (regular uploads without chat)
    if video["platform"] == "youtube" and not rows and info.get("has_auto_subs"):
        _set_video(video_id, stage="Reading auto captions", progress=0.3)
        progress(0.3, "Reading auto captions")
        cues = _auto_subs_cues(video["url"], d)
        if cues:
            sig["transcript"] = signals.transcript_signal(cues, settings["bin_seconds"])
            summary["transcript_cues"] = len(cues)

    # 4. audio loudness: skipped on very long VODs that already have chat (the audio track alone is ~1 GB)
    want_audio = duration <= 4 * 3600 or not sig
    if want_audio:
        _set_video(video_id, stage="Downloading audio track", progress=0.4)
        progress(0.4, "Downloading audio track")
        audio_path = d / "audio.m4a"
        try:
            if not audio_path.exists():
                fmt = "Audio_Only/bestaudio/worst" if video["platform"] == "twitch" else "bestaudio[ext=m4a]/bestaudio/worst"
                tools.run(["yt-dlp", "-f", fmt, "-o", str(audio_path), video["url"]], timeout=7200)
                if not audio_path.exists():
                    cands = [p for p in d.glob("audio.*") if p.suffix != ".json"]
                    if cands:
                        cands[0].rename(audio_path)
            _set_video(video_id, stage="Analysing loudness", progress=0.6)
            progress(0.6, "Analysing loudness")
            pcm = cut.decode_audio(audio_path, 8000, "s16le")
            sig["audio"] = signals.audio_signal(pcm, 8000, settings["bin_seconds"])
            summary["audio"] = True
        except tools.ToolError as e:
            progress(None, f"Audio skipped: {str(e)[:200]}")
        finally:
            audio_path.unlink(missing_ok=True)

    _set_video(video_id, stage="Picking moments", progress=0.85)
    progress(0.85, "Picking moments")
    score = signals.combine(sig, settings, settings["bin_seconds"])
    moments = signals.pick_moments(score, sig, settings, settings["bin_seconds"])
    db.execute("DELETE FROM moments WHERE video_id=? AND status='found'", (video_id,))
    for m in moments:
        m["chat_words"] = signals.top_chat_words(rows, m["t"]) if rows else []
        db.insert("moments", {"video_id": video_id, "t": m["t"], "score": m["score"], "reasons": m["reasons"],
                              "chat_words": m["chat_words"], "rank": m["rank"]})
    summary["moments"] = len(moments)
    summary["signals_used"] = list(sig)
    _set_video(video_id, status="scanned", stage=f"{len(moments)} moments found", progress=1.0, signals=summary)
    if not moments:
        _set_video(video_id, status="done", stage="No strong moments found")
        return
    top = int(settings.get("auto_render_top", 4))
    for m in db.rows("moments", "video_id=? AND rank<=?", (video_id, top), order="rank"):
        queue_render(m["id"])


# ---------------------------------------------------------------- render

def queue_render(moment_id: int) -> int:
    from . import jobs
    m = db.get("moments", moment_id)
    video = db.get("videos", m["video_id"])
    settings = db.all_settings()
    lead_in = float(settings["lead_in"])
    length = float(settings["download_length"])
    start = max(0.0, m["t"] - lead_in)
    if video.get("duration"):
        start = min(start, max(0.0, float(video["duration"]) - length))
    clip_id = db.insert("clips", {"video_id": video["id"], "campaign_id": video["campaign_id"], "moment_id": moment_id,
                                  "source_start": start, "source_length": length, "status": "rendering"})
    db.update("moments", moment_id, {"status": "queued"})
    _set_video(video["id"], status="rendering", stage="Rendering clips")
    jobs.enqueue("render_clip", {"clip_id": clip_id})
    return clip_id


def render_clip(clip_id: int, progress) -> None:
    clip = db.get("clips", clip_id)
    if not clip:
        return
    video = db.get("videos", clip["video_id"])
    campaign = db.get("campaigns", clip["campaign_id"])
    source = db.get("sources", video["source_id"]) if video.get("source_id") else None
    moment = db.get("moments", clip["moment_id"]) if clip.get("moment_id") else None
    settings = db.all_settings()
    d = _video_dir(video)
    key = sources.video_key(video["platform"], video["external_id"])
    seg = d / f"seg_{clip_id}.mp4"
    db.update("clips", clip_id, {"status": "rendering", "error": None})

    progress(0.05, "Downloading segment")
    cut.download_segment(video["url"], video["platform"], clip["source_start"], clip["source_length"], seg,
                         on_line=lambda l: progress(None, l[:100]) if "[download]" in l else None)
    config.FRAMES.mkdir(parents=True, exist_ok=True)
    if source and not (config.FRAMES / f"source_{source['id']}.jpg").exists():
        try:
            cut.extract_frame(seg, 2.0, config.FRAMES / f"source_{source['id']}.jpg")
        except tools.ToolError:
            pass

    progress(0.35, "Transcribing with Whisper")
    words = cut.transcribe(seg, settings["whisper_model"])
    ass = d / f"seg_{clip_id}.ass"
    cut.write_ass(cut.group_captions(words), campaign["creator"], ass, censor=bool(settings.get("censor_captions")))

    progress(0.6, "Rendering 9:16")
    facecam = (source or {}).get("facecam")
    layout = (source or {}).get("layout") or ("stacked" if facecam else "full")
    out = config.CLIPS / f"{key}_{clip_id}.mp4"
    cut.render(seg, out, facecam, layout, ass, cam_fraction=float(settings.get("facecam_fraction", 0.32)))
    duration = tools.media_duration(out)
    w, h = tools.media_size(out)
    thumb = config.FRAMES / f"clip_{clip_id}.jpg"
    try:
        cut.extract_frame(out, 1.0, thumb, width=540)
    except tools.ToolError:
        thumb = None

    chat_words = (moment or {}).get("chat_words") or []
    hook = clip.get("hook") or cap.default_hook(campaign["creator"], words, chat_words)
    post_text = cap.build_all(campaign, hook, chat_words)
    trim_end = min(duration, float(campaign.get("max_len") or duration))
    fields = {
        "path": str(out), "thumb": str(thumb) if thumb else None, "duration": duration, "width": w, "height": h,
        "transcript": words, "hook": hook, "title": post_text.get("youtube", {}).get("title", hook),
        "hashtags": cap.build_hashtags(campaign, chat_words, "tiktok"), "post_text": post_text,
        "trim_start": 0.0, "trim_end": trim_end, "status": "pending", "watermark": 0, "ai_text": 0,
    }
    db.update("clips", clip_id, fields)
    clip = db.get("clips", clip_id)
    clip["duration"] = trim_end - 0.0
    db.update("clips", clip_id, {"rule_results": rules.validate_clip(clip, campaign, video)})
    if moment:
        db.update("moments", moment["id"], {"status": "rendered"})
    if not settings.get("keep_segments"):
        seg.unlink(missing_ok=True)
        ass.unlink(missing_ok=True)
    disk.prune_cache(int(settings.get("cache_cap_mb", 3000)) * 1024 * 1024)
    remaining = db.execute("SELECT COUNT(*) c FROM clips WHERE video_id=? AND status='rendering'", (video["id"],)).fetchone()["c"]
    if remaining == 0:
        _set_video(video["id"], status="done", stage="Clips ready for review", progress=1.0)
    progress(1.0, "Done")


def finalize_clip(clip_id: int, progress) -> None:
    """Apply the trim chosen in review and produce the file that gets posted."""
    clip = db.get("clips", clip_id)
    if not clip or not clip.get("path"):
        return
    src = Path(clip["path"])
    start = float(clip.get("trim_start") or 0.0)
    end = float(clip.get("trim_end") or clip["duration"])
    final = src.with_name(src.stem + "_final.mp4")
    progress(0.2, "Trimming")
    if start <= 0.05 and end >= float(clip["duration"]) - 0.05:
        shutil.copyfile(src, final)
    else:
        cut.trim(src, final, start, end)
    dur = tools.media_duration(final)
    db.update("clips", clip_id, {"final_path": str(final), "duration": dur})
    progress(1.0, "Final file ready")


def grab_frame(source_id: int, progress) -> None:
    """Download two seconds of the source's latest video and save a frame for the facecam picker."""
    source = db.get("sources", source_id)
    vids = sources.list_recent(source, limit=3)
    if not vids:
        raise tools.ToolError("No videos found for this source yet")
    v = vids[0]
    d = config.CACHE / "frames"
    d.mkdir(parents=True, exist_ok=True)
    seg = d / f"frame_src{source_id}.mp4"
    seg.unlink(missing_ok=True)
    t = 600 if (v.get("duration") or 0) > 700 else 10
    progress(0.3, "Downloading a short sample")
    cut.download_segment(v["url"], v["platform"], t, 3, seg)
    cut.extract_frame(seg, 1.0, config.FRAMES / f"source_{source_id}.jpg")
    seg.unlink(missing_ok=True)
    progress(1.0, "Frame ready")


# ---------------------------------------------------------------- watching sources

def add_video(campaign_id: int, url: str, source_id: int | None = None, queue: bool = True) -> dict:
    from . import jobs
    p = sources.parse(url)
    if not p["kind"].endswith("_video"):
        raise ValueError("That is a channel URL. Add it as a source instead.")
    platform = p["kind"].split("_")[0]
    existing = db.one("videos", "campaign_id=? AND platform=? AND external_id=?", (campaign_id, platform, p["id"]))
    if existing:
        return existing
    vid = db.insert("videos", {"campaign_id": campaign_id, "source_id": source_id, "platform": platform,
                               "external_id": p["id"], "url": p["url"], "title": p["url"], "status": "queued",
                               "stage": "Waiting to scan"})
    if queue:
        jobs.enqueue("scan_video", {"video_id": vid})
    return db.get("videos", vid)


def check_sources(progress, campaign_id: int | None = None) -> None:
    where = "watch=1" + (" AND campaign_id=?" if campaign_id else "")
    params = (campaign_id,) if campaign_id else ()
    settings = db.all_settings()
    for s in db.rows("sources", where, params):
        if not s["kind"].endswith("_channel"):
            continue
        campaign = db.get("campaigns", s["campaign_id"])
        if not campaign or not campaign.get("active"):
            continue
        progress(None, f"Checking {s.get('name') or s['url']}")
        try:
            vids = sources.list_recent(s, limit=10)
        except tools.ToolError as e:
            progress(None, f"{s.get('name')}: {str(e)[:120]}")
            continue
        since = rules._parse_dt(campaign.get("source_start")) or (datetime.now(timezone.utc) - timedelta(days=3))
        until = rules._parse_dt(campaign.get("source_end"))
        added = 0
        for v in vids:
            if v.get("live_now"):
                continue
            if settings.get("gta_only") and not sources.looks_like_gta(v["title"]):
                continue
            pub = rules._parse_dt(v.get("published_at"))
            if pub and (pub < since or (until and pub > until)):
                continue
            if db.one("videos", "campaign_id=? AND platform=? AND external_id=?", (campaign["id"], v["platform"], v["external_id"])):
                continue
            vid = db.insert("videos", {"campaign_id": campaign["id"], "source_id": s["id"], "platform": v["platform"],
                                       "external_id": v["external_id"], "url": v["url"], "title": v["title"],
                                       "channel": v["channel"], "published_at": v["published_at"], "duration": v["duration"],
                                       "is_stream": v["is_stream"], "status": "queued", "stage": "Waiting to scan"})
            from . import jobs
            jobs.enqueue("scan_video", {"video_id": vid})
            added += 1
            if added >= 3:
                break
        db.update("sources", s["id"], {"last_checked": db.now()})


def clip_public(clip: dict) -> dict:
    """Clip row with media URLs for the UI."""
    c = dict(clip)
    c["video_url"] = f"/media/clip/{clip['id']}" if clip.get("path") else None
    c["thumb_url"] = f"/media/thumb/{clip['id']}" if clip.get("thumb") else None
    return c
