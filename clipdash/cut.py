"""Cutting: download only the needed segment, transcribe with Whisper, burn word-timed captions,
lay out 9:16 (facecam on top, gameplay below), trim, thumbnails and frame extraction."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Callable

import numpy as np

from . import captions as cap
from . import tools


def fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def download_segment(url: str, platform: str, start: float, length: float, out: Path,
                     on_line: Callable[[str], None] | None = None) -> Path:
    """Fetch only [start, start+length] at up to 1080p. Never the whole VOD.
    Twitch VODs go through TwitchDownloaderCLI's section downloader: yt-dlp's ffmpeg HLS seek stalls for
    offsets deep into a 20-hour-plus VOD (0 bytes after minutes), while TDCLI fetches just the needed
    segments in ~30 s at any offset. yt-dlp remains the fallback and the YouTube path."""
    if out.exists():
        if out.stat().st_size > 50_000:
            return out
        out.unlink()  # a stub left by a failed download (yt-dlp once left a 261-byte file); fetch again
    if platform == "twitch":
        try:
            return _download_twitch_section(url, start, length, out, on_line)
        except tools.ToolError as e:
            if on_line:
                on_line(f"TwitchDownloaderCLI failed ({str(e)[-120:]}); falling back to yt-dlp")
    fmt = ("1080p60/1080p/best" if platform == "twitch"
           else "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best")
    tmp = out.with_suffix(".part.mp4")
    tools.run(["yt-dlp", "-f", fmt, "--download-sections", f"*{fmt_ts(start)}-{fmt_ts(start + length)}",
               "--force-keyframes-at-cuts", "--merge-output-format", "mp4", "--newline", "--progress",
               "-o", str(tmp), url], timeout=3600, on_line=on_line)
    if not tmp.exists():
        cands = list(out.parent.glob(tmp.stem + "*"))
        if not cands:
            raise tools.ToolError("yt-dlp produced no file")
        cands[0].rename(tmp)
    tmp.rename(out)
    return out


def _download_twitch_section(url: str, start: float, length: float, out: Path,
                             on_line: Callable[[str], None] | None = None) -> Path:
    m = re.search(r"(\d{6,})", url)
    if not m:
        raise tools.ToolError(f"No Twitch VOD id in {url}")
    exe = tools.tdcli_path()
    if not exe.exists():
        tools.install_tdcli(on_line)
    tmp = out.with_suffix(".part.mp4")
    tmp.unlink(missing_ok=True)
    tmpdir = out.parent / "tdtmp"
    tmpdir.mkdir(parents=True, exist_ok=True)
    tail: list[str] = []

    def collect(line: str) -> None:
        line = line.strip()
        if line:
            tail.append(line[-160:])
            del tail[:-5]
        if on_line and "[STATUS]" in line:
            on_line(line.replace("[STATUS] - ", ""))

    tools.run([str(exe), "videodownload", "-u", m.group(1), "-o", str(tmp), "-b", f"{int(start)}s", "-e", f"{int(start + length)}s",
               "-q", "1080p60", "--ffmpeg-path", tools.ffmpeg_path(), "--temp-path", str(tmpdir),
               "--collision", "Overwrite", "--banner", "false"], timeout=1800, on_line=collect)
    if not tmp.exists() or tmp.stat().st_size < 10_000:
        raise tools.ToolError("TwitchDownloaderCLI produced no video section. Last output: " + " | ".join(tail))
    tmp.rename(out)
    return out


def decode_audio(path: Path, sample_rate: int = 16000, dtype: str = "f32le") -> np.ndarray:
    """Decode with ffmpeg to mono PCM. faster-whisper crashes with current PyAV when given a path,
    so it always receives an array."""
    with tempfile.NamedTemporaryFile(suffix=".raw", delete=False) as tmp:
        raw = tmp.name
    try:
        tools.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
                   "-ar", str(sample_rate), "-f", dtype, raw], timeout=3600)
        return np.fromfile(raw, dtype=np.float32 if dtype == "f32le" else np.int16)
    finally:
        try:
            os.unlink(raw)
        except OSError:
            pass


_model_cache: dict = {}


def transcribe(path: Path, model_name: str = "small") -> list[list]:
    """Word-timed transcript: [[start, end, word], ...]."""
    from faster_whisper import WhisperModel
    audio = decode_audio(path)
    model = _model_cache.get(model_name)
    if model is None:
        model = WhisperModel(model_name, device="auto", compute_type="int8")
        _model_cache[model_name] = model
    segs, _info = model.transcribe(audio, language="en", vad_filter=True, word_timestamps=True)
    words: list[list] = []
    for s in segs:
        for w in (s.words or []):
            if w.word.strip():
                words.append([round(float(w.start), 2), round(float(w.end), 2), w.word.strip()])
    return words


def group_captions(words: list, max_words: int = 4, max_dur: float = 2.0) -> list[tuple]:
    lines, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or (cur[-1][1] - cur[0][0]) >= max_dur:
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return [(l[0][0], l[-1][1], " ".join(w[2] for w in l)) for l in lines]


def ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def write_ass(lines: list[tuple], creator: str, out: Path, w: int = 1080, h: int = 1920,
              censor: bool = False, offset: float = 0.0, name_badge: bool = True) -> Path:
    """Big outlined captions in the lower-middle of the frame plus the creator's name near the top.
    The name badge is plain text (no logo) so it does not count as a watermark."""
    def esc(s: str) -> str:
        s = cap.censor(s) if censor else s
        return s.replace("{", "(").replace("}", ")").replace("\\", "/").upper()
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,78,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Name,Arial,60,&H0066E0FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,5,2,8,60,60,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if name_badge and creator:
        ev.append(f"Dialogue: 0,0:00:00.00,9:59:59.00,Name,,0,0,0,,{esc(creator)}")
    for s, e, text in lines:
        if e - offset <= 0:
            continue
        ev.append(f"Dialogue: 0,{ass_time(s - offset)},{ass_time(e - offset)},Cap,,0,0,0,,{esc(text)}")
    out.write_text(hdr + "\n".join(ev) + "\n", encoding="utf-8")
    return out


def _ass_filter_path(p: Path) -> str:
    """Escape a path for the ass= filter option. Windows drive colons and backslashes must be escaped."""
    s = str(p).replace("\\", "/")
    s = s.replace(":", "\\:").replace("'", "\\'")
    return s


def build_filter(facecam: dict | None, layout: str, ass_path: Path | None, W: int = 1080, H: int = 1920,
                 cam_fraction: float = 0.32) -> str:
    """facecam: {x, y, w, h} as fractions of the source frame. layout: 'stacked' or 'full'."""
    if layout == "stacked" and facecam:
        cam_h = int(H * cam_fraction)
        game_h = H - cam_h
        fx, fy, fw, fh = (max(0.0, min(1.0, float(facecam[k]))) for k in ("x", "y", "w", "h"))
        f = (
            f"[0:v]split=2[cam][game];"
            f"[cam]crop=iw*{fw:.4f}:ih*{fh:.4f}:iw*{fx:.4f}:ih*{fy:.4f},"
            f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[camv];"
            f"[game]scale=-2:{game_h},crop={W}:{game_h}[gamev];"
            f"[camv][gamev]vstack=inputs=2[v0];[v0]format=yuv420p"
        )
    else:
        # Gameplay fills the width; a blurred copy fills the rest of the 9:16 frame. The blur is computed on a
        # 1/8-size copy and scaled back up: a full-frame boxblur at 1080×1920×60fps was the slowest step of a render.
        f = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,crop={W // 8}:{H // 8},boxblur=4:2,"
            f"scale={W}:{H}:flags=bilinear,eq=brightness=-0.08[bgv];"
            f"[fg]scale={W}:-2[fgv];"
            f"[bgv][fgv]overlay=(W-w)/2:(H-h)/2[v0];[v0]format=yuv420p"
        )
    if ass_path:
        f += f",ass='{_ass_filter_path(ass_path)}'"
    return f + "[vout]"


_encoder_cache: dict = {}


def video_encoder() -> list[str]:
    """Hardware H.264 when the bundled ffmpeg has one that actually works here (VideoToolbox on Apple Silicon,
    NVENC/QSV/AMF on Windows), else libx264. A 90-second 1080×1920 clip encodes in ~30 s on hardware versus
    several minutes in software. The choice is verified once with a tiny test encode and cached."""
    if "args" in _encoder_cache:
        return _encoder_cache["args"]
    import subprocess
    ff = tools.ffmpeg_path()
    listed = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True, **tools._popen_kwargs()).stdout
    candidates = [("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "7M", "-maxrate", "9M", "-bufsize", "14M", "-allow_sw", "1"]),
                  ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "7M", "-maxrate", "9M"]),
                  ("h264_qsv", ["-c:v", "h264_qsv", "-b:v", "7M", "-maxrate", "9M"]),
                  ("h264_amf", ["-c:v", "h264_amf", "-b:v", "7M", "-maxrate", "9M"])]
    chosen = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    for name, args in candidates:
        if f" {name} " not in listed:
            continue
        test = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:r=30", "-t", "0.5",
                               *args, "-pix_fmt", "yuv420p", "-f", "null", "-"], capture_output=True, text=True, timeout=60, **tools._popen_kwargs())
        if test.returncode == 0:
            chosen = args
            break
    _encoder_cache["args"] = chosen
    return chosen


def render(src: Path, out: Path, facecam: dict | None, layout: str, ass_path: Path | None,
           cam_fraction: float = 0.32, on_line: Callable[[str], None] | None = None) -> Path:
    enc = video_encoder()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stats", "-i", str(src),
           "-filter_complex", build_filter(facecam, layout, ass_path, cam_fraction=cam_fraction),
           "-map", "[vout]", "-map", "0:a?", *enc, "-r", "60", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)]
    try:
        tools.run(cmd, timeout=7200, on_line=on_line)
    except tools.ToolError:
        if enc[1] == "libx264":
            raise
        _encoder_cache["args"] = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
        tools.run(cmd[: cmd.index(enc[0])] + _encoder_cache["args"] + cmd[cmd.index(enc[0]) + len(enc):], timeout=7200, on_line=on_line)
    return out


def trim(src: Path, out: Path, start: float, end: float) -> Path:
    """Re-encode [start, end] of a rendered clip (captions already burned in) for a frame-accurate cut."""
    tools.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
               *video_encoder(), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", str(out)], timeout=3600)
    return out


def extract_frame(src: Path, t: float, out: Path, width: int | None = None) -> Path:
    vf = f"scale={width}:-2" if width else "scale=iw:ih"
    tools.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
               "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out)], timeout=600)
    return out


def ass_text_sanity(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
