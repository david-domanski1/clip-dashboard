"""Identify and resolve sources: single videos and channels on YouTube and Twitch, and list their recent videos."""
import re
from datetime import datetime, timezone

from . import config, tools

YT_VIDEO = re.compile(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/|youtube\.com/embed/)([A-Za-z0-9_-]{11})")
YT_CHANNEL = re.compile(r"youtube\.com/(@[A-Za-z0-9_.-]+|channel/UC[A-Za-z0-9_-]+|c/[A-Za-z0-9_.-]+|user/[A-Za-z0-9_.-]+)")
TW_VIDEO = re.compile(r"twitch\.tv/videos/(\d+)")
TW_CHANNEL = re.compile(r"twitch\.tv/([A-Za-z0-9_]{3,25})(?:/|$|\?)")


def parse(text: str) -> dict:
    """Return {'kind': youtube_video|youtube_channel|twitch_video|twitch_channel, 'id': ..., 'url': ...}."""
    s = text.strip()
    if m := YT_VIDEO.search(s):
        return {"kind": "youtube_video", "id": m.group(1), "url": f"https://www.youtube.com/watch?v={m.group(1)}"}
    if m := TW_VIDEO.search(s):
        return {"kind": "twitch_video", "id": m.group(1), "url": f"https://www.twitch.tv/videos/{m.group(1)}"}
    if m := YT_CHANNEL.search(s):
        return {"kind": "youtube_channel", "id": m.group(1), "url": f"https://www.youtube.com/{m.group(1)}"}
    if m := TW_CHANNEL.search(s):
        name = m.group(1).lower()
        if name not in ("videos", "directory", "downloads", "p", "settings"):
            return {"kind": "twitch_channel", "id": name, "url": f"https://www.twitch.tv/{name}"}
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
        return {"kind": "youtube_video", "id": s, "url": f"https://www.youtube.com/watch?v={s}"}
    if re.fullmatch(r"@[A-Za-z0-9_.-]+", s):
        return {"kind": "youtube_channel", "id": s, "url": f"https://www.youtube.com/{s}"}
    if re.fullmatch(r"\d{6,}", s):
        return {"kind": "twitch_video", "id": s, "url": f"https://www.twitch.tv/videos/{s}"}
    raise ValueError("Paste a YouTube video/channel URL (youtube.com/@name) or a Twitch video/channel URL (twitch.tv/name).")


def video_key(platform: str, external_id: str) -> str:
    return f"{'yt' if platform == 'youtube' else 'tw'}_{external_id}"


def _entry_to_video(platform: str, e: dict) -> dict | None:
    vid = e.get("id")
    if not vid:
        return None
    url = e.get("url") or e.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if platform == "youtube"
                                                   else f"https://www.twitch.tv/videos/{vid}")
    if platform == "youtube" and not re.search(r"[A-Za-z0-9_-]{11}", vid):
        return None
    if platform == "twitch":
        m = re.search(r"videos/(\d+)", url) or re.search(r"(\d{6,})", vid)
        if not m:
            return None
        vid = m.group(1)
        url = f"https://www.twitch.tv/videos/{vid}"
    published = None
    if e.get("timestamp"):
        published = datetime.fromtimestamp(e["timestamp"], tz=timezone.utc).isoformat()
    elif e.get("upload_date"):
        d = e["upload_date"]
        published = f"{d[:4]}-{d[4:6]}-{d[6:8]}T00:00:00+00:00"
    elif e.get("release_timestamp"):
        published = datetime.fromtimestamp(e["release_timestamp"], tz=timezone.utc).isoformat()
    live_status = e.get("live_status") or ""
    return {
        "platform": platform, "external_id": vid, "url": url, "title": e.get("title") or "",
        "channel": e.get("channel") or e.get("uploader") or e.get("channel_id") or "",
        "published_at": published, "duration": e.get("duration"),
        "is_stream": int(live_status in ("was_live", "post_live", "is_live") or platform == "twitch"),
        "live_now": live_status == "is_live",
    }


def video_info(url: str) -> dict:
    """Full info for one video (title, duration, publish date, heatmap, live status)."""
    p = parse(url)
    platform = "youtube" if p["kind"].startswith("youtube") else "twitch"
    info = tools.ytdlp_json(p["url"])
    v = _entry_to_video(platform, info)
    if v is None:
        raise ValueError(f"Could not read video info for {url}")
    v["heatmap"] = info.get("heatmap") or []
    v["chapters"] = info.get("chapters") or []
    v["has_auto_subs"] = bool((info.get("automatic_captions") or {}).get("en"))
    v["has_live_chat"] = bool((info.get("subtitles") or {}).get("live_chat"))
    return v


def resolve_channel(kind: str, ident: str) -> dict:
    """Resolve a channel handle/URL to {name, handle, external_id, url, kind} via yt-dlp (no hardcoded IDs)."""
    if kind == "youtube_channel":
        url = f"https://www.youtube.com/{ident}" if not ident.startswith("http") else ident
        info = tools.ytdlp_json(url + ("/videos" if not url.rstrip("/").endswith("/videos") else ""),
                                ["--flat-playlist", "--playlist-items", "1"])
        cid = info.get("channel_id") or info.get("uploader_id") or info.get("id")
        handle = info.get("uploader_id") if str(info.get("uploader_id", "")).startswith("@") else ident
        return {"kind": kind, "name": info.get("channel") or info.get("uploader") or info.get("title") or ident,
                "handle": handle, "external_id": cid, "url": f"https://www.youtube.com/channel/{cid}" if cid else url}
    if kind == "twitch_channel":
        name = ident.lower().lstrip("@")
        info = tools.ytdlp_json(f"https://www.twitch.tv/{name}/videos?filter=archives&sort=time",
                                ["--flat-playlist", "--playlist-items", "1"])
        first = (info.get("entries") or [{}])[0] or {}
        title = (info.get("title") or "").split(" - ")[0].strip()
        return {"kind": kind, "name": first.get("uploader") or info.get("uploader") or title or name, "handle": name,
                "external_id": name, "url": f"https://www.twitch.tv/{name}"}
    raise ValueError("Not a channel")


def list_recent(source: dict, limit: int = 12) -> list[dict]:
    """Recent videos for a channel. YouTube: both the Videos and the Live (streams) tabs, since
    many streamers keep edited highlights and full streams on the same channel under different tabs."""
    kind = source["kind"]
    out: list[dict] = []
    seen: set[str] = set()
    if kind == "youtube_channel":
        base = source["url"].rstrip("/")
        for tab in ("videos", "streams"):
            try:
                info = tools.ytdlp_json(f"{base}/{tab}", ["--flat-playlist", "--playlist-items", f"1:{limit}"])
            except tools.ToolError:
                continue
            for e in info.get("entries") or []:
                v = _entry_to_video("youtube", e)
                if v and v["external_id"] not in seen:
                    if tab == "streams":
                        v["is_stream"] = 1
                    seen.add(v["external_id"])
                    out.append(v)
    elif kind == "twitch_channel":
        info = tools.ytdlp_json(f"https://www.twitch.tv/{source['external_id']}/videos?filter=archives&sort=time",
                                ["--flat-playlist", "--playlist-items", f"1:{limit}"])
        for e in info.get("entries") or []:
            v = _entry_to_video("twitch", e)
            if v and v["external_id"] not in seen:
                seen.add(v["external_id"])
                out.append(v)
    elif kind in ("youtube_video", "twitch_video"):
        out.append(video_info(source["url"]))
    return out


def looks_like_gta(title: str) -> bool:
    return bool(re.search(config.GTA_PATTERN, title or "", re.I))
