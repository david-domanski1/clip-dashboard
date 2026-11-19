"""Pipeline steps run by background jobs: scan a video (or a time window of it) for moments, render a clip,
finalize (trim), watch sources for new videos. Each step reports progress into the videos/clips/jobs tables."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import captions as cap
from . import chat, config, cut, db, disk, narration, rules, signals, sources, speech, thumbs, tools, tts

CHUNK_S = 2 * 3600          # "Scan the rest" works in 2-hour chunks
LONG_VOD_S = 4 * 3600       # VODs longer than this default to the last 2 hours
LAST_2H = {"kind": "last", "hours": 2}


def _video_dir(video: dict) -> Path:
    d = config.CACHE / sources.video_key(video["platform"], video["external_id"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _set_video(video_id: int, **fields) -> None:
    db.update("videos", video_id, fields)


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


# ---------------------------------------------------------------- windows

def parse_window(w) -> dict:
    """Normalise a window spec from the UI: 'auto' | 'last2h' | 'last6h' | 'whole' | {'start': s, 'end': e}."""
    if w in (None, "", "auto"):
        return {"kind": "auto"}
    if isinstance(w, str):
        if w == "whole":
            return {"kind": "whole"}
        if w.startswith("last") and w.endswith("h"):
            return {"kind": "last", "hours": float(w[4:-1])}
        raise ValueError(f"Unknown scan window {w!r}")
    if isinstance(w, dict):
        if w.get("kind") in ("auto", "whole", "last"):
            return w
        return {"kind": "range", "start": float(w.get("start") or 0), "end": float(w["end"]) if w.get("end") not in (None, "") else None}
    raise ValueError("Bad scan window")


def resolve_window(w: dict, duration: float) -> tuple[float, float]:
    duration = max(0.0, float(duration or 0))
    kind = w.get("kind", "auto")
    if kind == "auto":
        kind, w = ("last", LAST_2H) if duration > LONG_VOD_S else ("whole", {})
    if kind == "whole":
        return 0.0, duration
    if kind == "last":
        return max(0.0, duration - float(w.get("hours", 2)) * 3600), duration
    start = max(0.0, float(w.get("start") or 0))
    end = duration if w.get("end") is None else min(duration, float(w["end"]))
    if end <= start:
        raise ValueError("The scan window end must be after its start")
    return start, end


def merge_ranges(ranges: list, new: tuple[float, float]) -> list:
    rs = sorted([list(map(float, r)) for r in ranges] + [list(new)])
    out: list = []
    for s, e in rs:
        if out and s <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def unscanned_chunks(ranges: list, duration: float, chunk: float = CHUNK_S) -> list[tuple[float, float]]:
    """Gaps not yet scanned, split into chunks, most recent first."""
    gaps, cursor = [], 0.0
    for s, e in sorted(ranges):
        if s > cursor + 1:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if duration > cursor + 1:
        gaps.append((cursor, duration))
    out = []
    for s, e in gaps:
        x = e
        while x > s + 1:
            out.append((max(s, x - chunk), x))
            x -= chunk
    out.sort(key=lambda r: -r[1])
    return out


def outside_campaign_window(campaign: dict, published_at: str | None) -> str | None:
    """Reason text if the video's publish date falls outside the campaign's footage window, else None."""
    pub = rules._parse_dt(published_at)
    start, end = rules._parse_dt(campaign.get("source_start")), rules._parse_dt(campaign.get("source_end"))
    if pub is None or (start is None and end is None):
        return None
    if start and pub < start:
        return f"Outside campaign window: published {pub.date()}, campaign footage starts {start.date()}"
    if end and pub > end:
        return f"Outside campaign window: published {pub.date()}, campaign footage ends {end.date()}"
    return None


# ---------------------------------------------------------------- scan

def _auto_subs_cues(url: str, d: Path) -> list[list]:
    """YouTube auto-generated English captions as [[t, text], ...] (json3 format). Cached per video."""
    cache = d / "cues.json"
    if cache.exists():
        return json.loads(cache.read_text())
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
        text = "".join(s.get("utf8", "") for s in (ev.get("segs") or [])).strip()
        if t is not None and text and text != "\n":
            cues.append([round(t / 1000.0, 2), text])
    for f in files:
        f.unlink(missing_ok=True)
    cache.write_text(json.dumps(cues))
    return cues


def _in_window(rows: list[list], start: float, end: float) -> list[list]:
    return [r for r in rows if start <= r[0] <= end]


def scan_video(video_id: int, progress, window=None) -> None:
    video = db.get("videos", video_id)
    if not video:
        return
    campaign = db.get("campaigns", video["campaign_id"])
    settings = db.all_settings()
    d = _video_dir(video)
    w = parse_window(window if window is not None else video.get("scan_window") or "auto")
    _set_video(video_id, status="scanning", stage="Reading video info", progress=0.05, error=None)
    progress(0.05, "Reading video info")
    info = sources.video_info(video["url"])
    live = bool(info.get("live_now"))
    _set_video(video_id, title=info["title"] or video["title"], channel=info["channel"] or video["channel"],
               published_at=info["published_at"] or video["published_at"], duration=info["duration"] or video.get("duration"),
               is_stream=max(int(video.get("is_stream") or 0), info["is_stream"]), live=int(live))
    video = db.get("videos", video_id)

    # Campaign footage window applies to the whole video, before any download.
    reason = outside_campaign_window(campaign, video.get("published_at"))
    if reason and not video.get("override"):
        _set_video(video_id, status="skipped", stage=reason, progress=1.0)
        progress(1.0, reason)
        return

    duration = float(video.get("duration") or 0)
    if live and duration <= 0:
        raise tools.ToolError("This stream just started and the VOD has no playable segments yet. Try again in a few minutes.")
    if duration <= 0:
        raise tools.ToolError("Could not read the video's duration; the VOD may be unavailable or sub-only.")
    start, end = resolve_window(w, duration)
    label = f"{fmt_ts(start)}–{fmt_ts(end)}" + (" (stream in progress)" if live else "")
    _set_video(video_id, scan_start=start, scan_end=end)
    sig: dict = {}
    summary: dict = dict(video.get("signals") or {})
    summary.update({"audio": False, "heatmap": False, "transcript_cues": 0, "last_window": [start, end]})

    # 1. chat replay (strongest signal). Twitch: windowed download (seconds). YouTube: whole replay, cached, then filtered.
    rows: list[list] = []
    try:
        if video["platform"] == "twitch":
            whole = d / "chat.json"
            win_path = d / f"chat_{int(start)}_{int(end)}.json"
            if whole.exists() and not live:
                rows = _in_window(chat.load(whole), start, end)
            else:
                _set_video(video_id, stage=f"Downloading Twitch chat {label}", progress=0.1)
                progress(0.1, f"Downloading Twitch chat {label}")
                chat.download_twitch_chat(video["external_id"], win_path, start=start, end=end,
                                          on_line=lambda l: progress(None, l[:120]))
                rows = chat.load(win_path)
        elif info.get("has_live_chat") or video.get("is_stream"):
            chat_path = d / "chat.json"
            if not chat_path.exists():
                _set_video(video_id, stage="Downloading YouTube chat replay (whole stream, cached for later windows)", progress=0.1)
                progress(0.1, "Downloading YouTube chat replay")
                chat.download_youtube_chat(video["url"], chat_path)
            rows = _in_window(chat.load(chat_path), start, end)
    except tools.ToolError as e:
        progress(None, f"Chat unavailable: {str(e)[:200]}")
        summary["chat_error"] = str(e)[:300]
    if not db.get("videos", video_id):
        return
    if rows:
        sig["chat"] = signals.chat_signal(rows, settings["hype_tokens"], settings["bin_seconds"])
        summary["chat_messages"] = len(rows)

    # 2. YouTube "most replayed" heatmap (regular uploads)
    if info.get("heatmap"):
        sig["heatmap"] = signals.heatmap_signal(info["heatmap"], duration, settings["bin_seconds"])
        summary["heatmap"] = True

    # 3. transcript cues from auto captions (regular uploads without chat)
    if video["platform"] == "youtube" and not rows and info.get("has_auto_subs") and not live:
        _set_video(video_id, stage="Reading auto captions", progress=0.3)
        progress(0.3, "Reading auto captions")
        cues = _in_window(_auto_subs_cues(video["url"], d), start, end)
        if cues:
            sig["transcript"] = signals.transcript_signal(cues, settings["bin_seconds"])
            summary["transcript_cues"] = len(cues)

    # 4. audio loudness only when nothing better exists and the window is not huge (audio for a window is ~60 MB/hour)
    if not sig and (end - start) <= LONG_VOD_S:
        _set_video(video_id, stage=f"Downloading audio {label}", progress=0.4)
        progress(0.4, "Downloading audio track")
        audio_path = d / f"audio_{int(start)}_{int(end)}.m4a"
        try:
            if not audio_path.exists():
                fmt = "Audio_Only/bestaudio/worst" if video["platform"] == "twitch" else "bestaudio[ext=m4a]/bestaudio/worst"
                cmd = ["yt-dlp", "-f", fmt, "-o", str(audio_path)]
                if start > 0 or end < duration - 1:
                    cmd += ["--download-sections", f"*{fmt_ts(start)}-{fmt_ts(end)}"]
                tools.run(cmd + [video["url"]], timeout=7200)
                if not audio_path.exists():
                    cands = [p for p in d.glob(audio_path.stem + "*") if p.suffix != ".json"]
                    if cands:
                        cands[0].rename(audio_path)
            _set_video(video_id, stage="Analysing loudness", progress=0.6)
            progress(0.6, "Analysing loudness")
            pcm = cut.decode_audio(audio_path, 8000, "s16le")
            a = signals.audio_signal(pcm, 8000, settings["bin_seconds"])
            if start > 0:  # the section starts at t=start; shift bins onto the video's time axis
                a["bins"] = a["bins"] + start
                a["offset_bins"] = int(start // settings["bin_seconds"])
            sig["audio"] = a
            summary["audio"] = True
        except tools.ToolError as e:
            progress(None, f"Audio skipped: {str(e)[:200]}")
        finally:
            audio_path.unlink(missing_ok=True)

    _set_video(video_id, stage="Picking moments", progress=0.85)
    progress(0.85, "Picking moments")
    score = signals.combine(sig, settings, settings["bin_seconds"])
    moments = signals.pick_moments(score, sig, settings, settings["bin_seconds"], t_min=start, t_max=end)
    video = db.get("videos", video_id)
    if not video:
        return
    # AFK / spam filter: a chat spike made of one repeated message ("WAKE UP") is not a highlight
    spam_cut = float(settings.get("spam_fraction", 0.5))
    for m in moments:
        m["chat_words"] = signals.top_chat_words(rows, m["t"]) if rows else []
        m["flags"] = []
        m["raw_score"] = m["score"]
        if rows:
            frac, what = signals.chat_spam(rows, m["t"])
            if frac >= spam_cut:
                m["flags"].append("chat_spam")
                m["score"] = round(m["score"] * 0.2, 2)
                m["reasons"].append(f"Chat spam: {int(frac * 100)}% of messages repeat “{what}” (scored down)")
    # Speech check: ~40 s of audio around each candidate through Silero VAD. A streamer asleep or AFK while
    # chat plays word games shows up as almost no speech.
    low_cut = float(settings.get("min_speech_fraction", 0.12))
    _set_video(video_id, stage="Checking for speech at each moment", progress=0.88)
    progress(0.88, "Checking for speech at each moment")
    for m in moments:
        frac = speech.moment_speech(video, m["t"])
        m["speech"] = frac
        if frac is not None and frac < low_cut:
            m["flags"].append("low_speech")
            m["score"] = round(m["score"] * 0.3, 2)
            m["reasons"].append(f"Low speech: only {int(frac * 100)}% of the next 40 s has talking (scored down)")
        elif frac is not None:
            m["reasons"].append(f"Speech in {int(frac * 100)}% of the next 40 s")
    moments.sort(key=lambda m: -m["score"])
    # merge: drop un-rendered moments inside this window, insert the new ones, re-rank everything
    db.execute("DELETE FROM moments WHERE video_id=? AND status='found' AND t>=? AND t<=?", (video_id, start, end))
    new_ids = []
    for m in moments:
        new_ids.append(db.insert("moments", {"video_id": video_id, "t": m["t"], "score": m["score"], "raw_score": m["raw_score"],
                                             "reasons": m["reasons"], "chat_words": m["chat_words"], "flags": m["flags"],
                                             "speech": m.get("speech"), "rank": 0}))
    for rank, m in enumerate(db.rows("moments", "video_id=?", (video_id,), order="score DESC, t"), 1):
        db.update("moments", m["id"], {"rank": rank})
    _set_video(video_id, stage="Grabbing moment thumbnails", progress=0.92)
    progress(0.92, "Grabbing moment thumbnails")
    for mid in new_ids:
        m = db.get("moments", mid)
        if m:
            th = thumbs.moment_thumbnail(video, mid, m["t"])
            if th:
                db.update("moments", mid, {"thumb": str(th)})
    ranges = merge_ranges(video.get("scanned_ranges") or [], (start, end))
    total = db.execute("SELECT COUNT(*) c FROM moments WHERE video_id=?", (video_id,)).fetchone()["c"]
    summary.update({"moments": total, "signals_used": list(sig)})
    covered = sum(e - s for s, e in ranges) >= duration - 2
    stage = f"Scanned {label}: {len(moments)} moments" + ("" if covered else " · more of the VOD can be scanned")
    _set_video(video_id, status="scanned", stage=stage, progress=1.0, signals=summary, scanned_ranges=ranges, live=int(live))
    if not moments:
        _set_video(video_id, status="done", stage=f"Scanned {label}: no strong moments" + ("" if covered else " · more of the VOD can be scanned"))
        return
    top = int(settings.get("auto_render_top", 1))
    rendered = 0
    for m in db.rows("moments", "id IN (" + ",".join("?" * len(new_ids)) + ")", tuple(new_ids), order="score DESC"):
        if rendered >= top:
            break
        if m.get("flags"):
            continue
        queue_render(m["id"])
        rendered += 1
    if rendered == 0:
        _set_video(video_id, status="done", stage=stage + " · nothing auto-rendered (all flagged); pick one to render")


def render_next_best(video_id: int) -> int | None:
    """Render the highest-scoring moment that has no clip yet, preferring unflagged ones."""
    for m in db.rows("moments", "video_id=? AND status='found'", (video_id,), order="score DESC, t"):
        if not m.get("flags"):
            return queue_render(m["id"])
    for m in db.rows("moments", "video_id=? AND status='found'", (video_id,), order="score DESC, t"):
        return queue_render(m["id"])
    return None


def scan_rest(video_id: int) -> int:
    """Queue background scans for every unscanned 2-hour chunk, most recent first. Returns how many were queued."""
    from . import jobs
    video = db.get("videos", video_id)
    if not video or not video.get("duration"):
        return 0
    n = 0
    for s, e in unscanned_chunks(video.get("scanned_ranges") or [], float(video["duration"])):
        jobs.enqueue("scan_video", {"video_id": video_id, "window": {"kind": "range", "start": s, "end": e}})
        n += 1
    if n:
        _set_video(video_id, stage=f"Scanning the rest in {n} chunks", status="queued")
    return n


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
    if not video or not campaign:
        return
    source = db.get("sources", video["source_id"]) if video.get("source_id") else None
    moment = db.get("moments", clip["moment_id"]) if clip.get("moment_id") else None
    settings = db.all_settings()
    d = _video_dir(video)
    key = sources.video_key(video["platform"], video["external_id"])
    seg = d / f"seg_{clip_id}.mp4"
    db.update("clips", clip_id, {"status": "rendering", "error": None})

    import time as _time
    timings: dict = {}
    t_mark = _time.time()
    progress(0.05, "Downloading segment")
    cut.download_segment(video["url"], video["platform"], clip["source_start"], clip["source_length"], seg,
                         on_line=lambda l: progress(None, l[:100]) if "[download]" in l else None)
    config.FRAMES.mkdir(parents=True, exist_ok=True)
    if source and not (config.FRAMES / f"source_{source['id']}.jpg").exists():
        try:
            cut.extract_frame(seg, 2.0, config.FRAMES / f"source_{source['id']}.jpg")
        except tools.ToolError:
            pass

    timings["download_s"] = round(_time.time() - t_mark, 1); t_mark = _time.time()
    progress(0.35, "Transcribing with Whisper")
    from . import jobs as _jobs
    _jobs.check_cancelled()
    words = cut.transcribe(seg, settings["whisper_model"])
    _jobs.check_cancelled()
    timings["transcribe_s"] = round(_time.time() - t_mark, 1); t_mark = _time.time()
    seg_len = max(1.0, tools.media_duration(seg))
    wpm = len(words) / seg_len * 60
    flags = []
    if wpm < float(settings.get("min_words_per_min", 20)):
        flags.append("low_speech")
    ass = d / f"seg_{clip_id}.ass"
    cut.write_ass(cut.group_captions(words), campaign["creator"], ass, censor=bool(settings.get("censor_captions")))

    progress(0.6, "Rendering 9:16")
    facecam = (source or {}).get("facecam")
    layout = (source or {}).get("layout") or ("stacked" if facecam else "full")
    out = config.CLIPS / f"{key}_{clip_id}.mp4"
    cut.render(seg, out, facecam, layout, ass, cam_fraction=float(settings.get("facecam_fraction", 0.32)))
    timings["render_s"] = round(_time.time() - t_mark, 1)
    duration = tools.media_duration(out)
    w, h = tools.media_size(out)
    thumb = config.FRAMES / f"clip_{clip_id}.jpg"
    try:
        cut.extract_frame(out, 1.0, thumb, width=540)
    except tools.ToolError:
        thumb = None

    if not db.get("clips", clip_id):
        out.unlink(missing_ok=True)
        seg.unlink(missing_ok=True)
        return  # deleted while rendering
    chat_words = (moment or {}).get("chat_words") or []
    creator_name = (source or {}).get("name") if campaign.get("kind") == "streamer" else campaign["creator"]
    hook = clip.get("hook") or cap.default_hook(creator_name or campaign["creator"], words, chat_words)
    post_text = cap.build_all(campaign, hook, chat_words, source=source)
    trim_end = min(duration, float(campaign.get("max_len") or duration))
    fields = {
        "path": str(out), "thumb": str(thumb) if thumb else None, "duration": duration, "width": w, "height": h,
        "transcript": words, "hook": hook, "title": post_text.get("youtube", {}).get("title", hook),
        "hashtags": cap.build_hashtags(cap.workspace_campaign(campaign, source) if campaign.get("kind") == "streamer" else campaign, chat_words, "tiktok"),
        "post_text": post_text,
        "trim_start": 0.0, "trim_end": trim_end, "status": "pending", "watermark": 0, "ai_text": 0,
        "speech_wpm": round(wpm, 1), "flags": flags, "timings": timings,
    }
    db.update("clips", clip_id, fields)
    clip = db.get("clips", clip_id)
    clip["duration"] = trim_end - 0.0
    db.update("clips", clip_id, {"rule_results": rules.validate_clip(clip, campaign, video)})
    if moment:
        mflags = sorted(set((moment.get("flags") or []) + flags))
        reasons = list(moment.get("reasons") or [])
        if "low_speech" in flags and not any(r.startswith("Low speech") for r in reasons):
            reasons.append(f"Low speech: {wpm:.0f} words/min in the transcript (likely AFK or quiet)")
        db.update("moments", moment["id"], {"status": "rendered", "flags": mflags, "reasons": reasons})
    if not settings.get("keep_segments"):
        seg.unlink(missing_ok=True)
        ass.unlink(missing_ok=True)
    disk.prune_cache(int(settings.get("cache_cap_mb", 3000)) * 1024 * 1024)
    remaining = db.execute("SELECT COUNT(*) c FROM clips WHERE video_id=? AND status='rendering'", (video["id"],)).fetchone()["c"]
    pending_scans = db.execute("SELECT COUNT(*) c FROM jobs WHERE type='scan_video' AND status IN ('queued','running') AND payload LIKE ?",
                               (f'%"video_id": {video["id"]}%',)).fetchone()["c"]
    if remaining == 0 and pending_scans == 0:
        _set_video(video["id"], status="done", stage="Clips ready for review", progress=1.0)
    progress(1.0, "Done")


def base_duration(clip: dict) -> float | None:
    """Length of the rendered clip itself (before trim/intro)."""
    n = clip.get("narration") or {}
    return n.get("base_duration") or clip.get("duration")


def finalize_clip(clip_id: int, progress) -> None:
    """Apply the trim chosen in review and, when narration is enabled, prepend the intro. The posted file is
    <clip>_final.mp4; the original render is never modified."""
    clip = db.get("clips", clip_id)
    if not clip or not clip.get("path"):
        return
    src = Path(clip["path"])
    base = float(base_duration(clip) or tools.media_duration(src))
    start = float(clip.get("trim_start") or 0.0)
    end = float(clip.get("trim_end") or base)
    final = src.with_name(src.stem + "_final.mp4")
    trimmed = src.with_name(src.stem + "_trimmed.mp4")
    progress(0.2, "Trimming")
    if start <= 0.05 and end >= base - 0.05:
        shutil.copyfile(src, trimmed)
    else:
        cut.trim(src, trimmed, start, end)
    narr = clip.get("narration") or {}
    if narr.get("enabled") and narr.get("intro_path") and Path(narr["intro_path"]).exists():
        progress(0.7, "Adding narrated intro")
        narration.concat(Path(narr["intro_path"]), trimmed, final)
        trimmed.unlink(missing_ok=True)
    else:
        trimmed.replace(final)
    dur = tools.media_duration(final)
    narr["base_duration"] = base
    db.update("clips", clip_id, {"final_path": str(final), "duration": dur, "narration": narr})
    progress(1.0, "Final file ready")


def render_intro(clip_id: int, progress) -> None:
    """Synthesize the chosen hook and render the intro segment; also a preview (intro + untrimmed clip)."""
    clip = db.get("clips", clip_id)
    if not clip or not clip.get("path"):
        return
    narr = dict(clip.get("narration") or {})
    hook = (narr.get("chosen") or "").strip()
    if not hook:
        raise tools.ToolError("No hook chosen")
    campaign = db.get("campaigns", clip["campaign_id"])
    ok, why = rules.narration_allowed(campaign)
    if not ok:
        raise tools.ToolError(why)
    src = Path(clip["path"])
    work = _video_dir(db.get("videos", clip["video_id"])) / "narration"
    work.mkdir(parents=True, exist_ok=True)
    progress(0.1, "Synthesizing voice")
    wav, voice_s, engine = tts.synthesize(hook, work / f"voice_{clip_id}.wav", narr.get("engine") or None)
    progress(0.4, f"Rendering intro ({engine} voice, {voice_s:.1f}s)")
    intro = config.CLIPS / f"{src.stem}_intro.mp4"
    intro_s = narration.render_intro(src, float(clip.get("trim_start") or 0.0), hook, wav, voice_s, intro, work)
    progress(0.8, "Building preview")
    preview = config.CLIPS / f"{src.stem}_narrated_preview.mp4"
    narration.concat(intro, src, preview)
    narr.update({"intro_path": str(intro), "intro_duration": round(intro_s, 2), "audio_path": str(wav), "engine": engine,
                 "preview_path": str(preview), "status": "ready", "error": None, "base_duration": float(base_duration(clip) or tools.media_duration(src))})
    db.update("clips", clip_id, {"narration": narr, "final_path": None, "ai_text": 1})
    progress(1.0, "Intro ready")


def grab_frame(source_id: int, progress) -> None:
    """Download a few seconds of the source's latest video and save a frame for the facecam picker."""
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


# ---------------------------------------------------------------- adding & watching

def add_video(campaign_id: int, url: str, source_id: int | None = None, queue: bool = True, window=None,
              override: bool = False) -> dict:
    from . import jobs
    p = sources.parse(url)
    if not p["kind"].endswith("_video"):
        raise ValueError("That is a channel URL. Add it as a source instead.")
    w = parse_window(window)
    platform = p["kind"].split("_")[0]
    existing = db.one("videos", "campaign_id=? AND platform=? AND external_id=?", (campaign_id, platform, p["id"]))
    if existing:
        if queue and existing["status"] in ("skipped", "error", "done", "scanned"):
            db.update("videos", existing["id"], {"status": "queued", "stage": "Waiting to scan", "error": None,
                                                 "scan_window": w, "override": int(override or existing.get("override") or 0)})
            jobs.enqueue("scan_video", {"video_id": existing["id"], "window": w})
        return db.get("videos", existing["id"])
    vid = db.insert("videos", {"campaign_id": campaign_id, "source_id": source_id, "platform": platform,
                               "external_id": p["id"], "url": p["url"], "title": p["url"], "status": "queued",
                               "stage": "Waiting to scan", "scan_window": w, "override": int(override)})
    if queue:
        jobs.enqueue("scan_video", {"video_id": vid, "window": w})
    return db.get("videos", vid)


def check_sources(progress, campaign_id: int | None = None) -> None:
    """Queue new videos from watched channels. Videos published before the campaign's footage window are recorded
    as 'skipped' (with an override in the UI) instead of being scanned."""
    from . import jobs
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
        recent_floor = datetime.now(timezone.utc) - timedelta(days=3)
        added = 0
        for v in vids:
            if settings.get("gta_only") and not sources.looks_like_gta(v["title"]):
                continue
            if db.one("videos", "campaign_id=? AND platform=? AND external_id=?", (campaign["id"], v["platform"], v["external_id"])):
                continue
            row = {"campaign_id": campaign["id"], "source_id": s["id"], "platform": v["platform"], "external_id": v["external_id"],
                   "url": v["url"], "title": v["title"], "channel": v["channel"], "published_at": v["published_at"],
                   "duration": v["duration"], "is_stream": v["is_stream"], "live": int(bool(v.get("live_now")))}
            reason = outside_campaign_window(campaign, v.get("published_at"))
            pub = rules._parse_dt(v.get("published_at"))
            if reason or (pub and not campaign.get("source_start") and pub < recent_floor):
                row.update({"status": "skipped", "stage": reason or "Older than 3 days (no campaign start date set)"})
                db.insert("videos", row)
                continue
            if added >= 3:
                break
            row.update({"status": "queued", "stage": "Waiting to scan", "scan_window": {"kind": "auto"}})
            vid = db.insert("videos", row)
            jobs.enqueue("scan_video", {"video_id": vid, "window": {"kind": "auto"}})
            added += 1
        db.update("sources", s["id"], {"last_checked": db.now()})


def cancel_video_jobs(video_id: int) -> None:
    from . import jobs
    for j in db.rows("jobs", "status IN ('queued','running') AND type='scan_video' AND payload LIKE ?", (f'%"video_id": {video_id}%',)):
        jobs.cancel(j["id"])
    for c in db.rows("clips", "video_id=?", (video_id,)):
        cancel_clip_jobs(c["id"])


def cancel_clip_jobs(clip_id: int) -> int:
    from . import jobs
    n = 0
    for j in db.rows("jobs", "status IN ('queued','running') AND type IN ('render_clip','finalize_clip') AND payload LIKE ?",
                     (f'%"clip_id": {clip_id}%',)):
        if jobs.cancel(j["id"]):
            n += 1
    return n


def clip_public(clip: dict) -> dict:
    """Clip row with media URLs for the UI."""
    c = dict(clip)
    c["video_url"] = f"/media/clip/{clip['id']}" if clip.get("path") else None
    c["thumb_url"] = f"/media/thumb/{clip['id']}" if clip.get("thumb") else None
    return c
