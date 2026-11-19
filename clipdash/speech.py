"""Scan-time speech check for candidate moments: fetch ~40 s of audio around the moment (one or two low-bitrate
HLS segments on Twitch, a seek into the audio stream on YouTube), run the Silero VAD bundled with faster-whisper,
and report what fraction of the window contains speech. Catches the streamer being asleep/AFK while chat is busy."""
from __future__ import annotations

import os
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

from . import thumbs, tools

WINDOW_S = 40.0


def _decode_16k(path: Path, start: float = 0.0, length: float = WINDOW_S) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw = tmp.name
    try:
        tools.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, start):.2f}", "-t", f"{length:.1f}", "-i", str(path),
                   "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", raw], timeout=120)
        return np.fromfile(raw, dtype=np.float32)
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass


def speech_fraction(audio16k: np.ndarray) -> float:
    """Fraction of the audio that Silero VAD marks as speech (0..1)."""
    if len(audio16k) < 16000:
        return 0.0
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    opts = VadOptions(threshold=0.5, min_speech_duration_ms=200, min_silence_duration_ms=500, speech_pad_ms=100)
    segs = get_speech_timestamps(audio16k, opts, sampling_rate=16000)
    total = sum(s["end"] - s["start"] for s in segs) / 16000.0
    return round(min(1.0, total / (len(audio16k) / 16000.0)), 3)


def _twitch_audio_window(video: dict, t: float, length: float) -> np.ndarray:
    base, segs = thumbs._twitch_playlist_for(video, "Audio_Only")
    cum = 0.0
    parts: list[bytes] = []
    first_offset = None
    for dur, name in segs:
        if cum + dur > t and cum < t + length:
            if first_offset is None:
                first_offset = t - cum
            parts.append(urllib.request.urlopen(f"{base}/{name}", timeout=30).read())
        cum += dur
        if cum >= t + length:
            break
    if not parts:
        raise tools.ToolError("No audio segments for that time")
    with tempfile.NamedTemporaryFile(suffix=".ts", delete=False) as tmp:
        tmp.write(b"".join(parts))
        path = Path(tmp.name)
    try:
        return _decode_16k(path, first_offset or 0.0, length)
    finally:
        path.unlink(missing_ok=True)


def _youtube_audio_window(video: dict, t: float, length: float) -> np.ndarray:
    key = video["external_id"]
    url = thumbs._yt_audio_urls.get(key)
    if not url:
        url = tools.run(["yt-dlp", "-g", "-f", "ba[ext=m4a]/ba/worst", video["url"]], timeout=120).strip().split("\n")[0]
        thumbs._yt_audio_urls[key] = url
    return _decode_16k(Path(url), t, length)


def moment_speech(video: dict, t: float, length: float = WINDOW_S) -> float | None:
    """Speech fraction in [t, t+length], or None if the audio could not be fetched (best effort)."""
    try:
        audio = _twitch_audio_window(video, t, length) if video["platform"] == "twitch" else _youtube_audio_window(video, t, length)
        return speech_fraction(audio)
    except Exception:  # noqa: BLE001
        return None
