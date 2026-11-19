"""Cheap thumbnails for moments. Twitch: fetch one low-quality HLS segment (~1 MB) around the moment and grab a
frame; YouTube: seek a direct low-res stream URL. Both take well under a second and never download the VOD."""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from . import config, tools

_playlists: dict[str, tuple[str, list[tuple[float, str]]]] = {}
_formats: dict[str, dict] = {}
_yt_audio_urls: dict[str, str] = {}


def _twitch_formats(video: dict) -> dict:
    key = video["external_id"]
    if key not in _formats:
        info = tools.ytdlp_json(video["url"])
        _formats[key] = {f.get("format_id"): f.get("url") for f in info.get("formats", [])}
    return _formats[key]


def _twitch_playlist_for(video: dict, fmt_id: str) -> tuple[str, list[tuple[float, str]]]:
    key = f"{video['external_id']}:{fmt_id}"
    if key in _playlists:
        return _playlists[key]
    url = _twitch_formats(video).get(fmt_id)
    if not url:
        raise tools.ToolError(f"No {fmt_id} stream")
    text = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "replace")
    lines = text.split("\n")
    segs: list[tuple[float, str]] = []
    for i, l in enumerate(lines):
        if l.startswith("#EXTINF:") and i + 1 < len(lines):
            segs.append((float(l[8:].split(",")[0]), lines[i + 1].strip()))
    _playlists[key] = (url.rsplit("/", 1)[0], segs)
    return _playlists[key]


def _twitch_playlist(video: dict) -> tuple[str, list[tuple[float, str]]]:
    key = video["external_id"]
    if key in _playlists:
        return _playlists[key]
    fmts = _twitch_formats(video)
    url = fmts.get("360p") or fmts.get("480p") or fmts.get("160p") or next((u for k, u in fmts.items() if k and k.endswith("p")), None)
    if not url:
        raise tools.ToolError("No playable stream for thumbnails")
    text = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "replace")
    lines = text.split("\n")
    segs: list[tuple[float, str]] = []
    for i, l in enumerate(lines):
        if l.startswith("#EXTINF:") and i + 1 < len(lines):
            segs.append((float(l[8:].split(",")[0]), lines[i + 1].strip()))
    base = url.rsplit("/", 1)[0]
    _playlists[key] = (base, segs)
    return _playlists[key]


def twitch_frame(video: dict, t: float, out: Path) -> Path:
    base, segs = _twitch_playlist(video)
    cum = 0.0
    for dur, name in segs:
        if cum + dur > t:
            data = urllib.request.urlopen(f"{base}/{name}", timeout=30).read()
            ts = out.with_suffix(".ts")
            ts.write_bytes(data)
            try:
                tools.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t - cum):.2f}", "-i", str(ts),
                           "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", str(out)], timeout=60)
            finally:
                ts.unlink(missing_ok=True)
            return out
        cum += dur
    raise tools.ToolError("Moment is past the end of the VOD playlist")


_yt_urls: dict[str, str] = {}


def youtube_frame(video: dict, t: float, out: Path) -> Path:
    key = video["external_id"]
    url = _yt_urls.get(key)
    if not url:
        url = tools.run(["yt-dlp", "-g", "-f", "bv*[height<=360][ext=mp4]/b[height<=360]/worst", video["url"]], timeout=120).strip().split("\n")[0]
        _yt_urls[key] = url
    tools.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", url, "-frames:v", "1", "-vf", "scale=360:-2",
               "-q:v", "4", str(out)], timeout=120)
    return out


def moment_thumbnail(video: dict, moment_id: int, t: float) -> Path | None:
    config.FRAMES.mkdir(parents=True, exist_ok=True)
    out = config.FRAMES / f"moment_{moment_id}.jpg"
    if out.exists():
        return out
    try:
        if video["platform"] == "twitch":
            return twitch_frame(video, t, out)
        return youtube_frame(video, t, out)
    except Exception:  # noqa: BLE001  thumbnails are best-effort
        return None
