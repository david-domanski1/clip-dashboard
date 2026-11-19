"""Narrated hook intro: 3–6 s of the clip's first frame (slow push-in, darkened ~40%) with the hook as large centred
text while a synthetic voice reads it, concatenated in front of the real clip. The original clip is kept, so
narration can be switched off and the clip re-exported without it."""
from __future__ import annotations

from pathlib import Path

from . import cut, tools

VOICE_START_S = 0.3
MIN_S, MAX_S = 3.0, 6.0


def intro_duration(voice_s: float) -> float:
    """Voice starts within the first second; the real action starts about when the voice ends (3–6 s total)."""
    return max(MIN_S, min(MAX_S, VOICE_START_S + voice_s + 0.6))


def write_intro_ass(hook: str, out: Path, w: int = 1080, h: int = 1920) -> Path:
    text = hook.replace("{", "(").replace("}", ")").replace("\\", "/")
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,Arial,88,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,5,90,90,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.00,Hook,,0,0,0,,{{\\fad(150,250)}}{text}
"""
    out.write_text(ass, encoding="utf-8")
    return out


def render_intro(clip_path: Path, frame_at: float, hook: str, voice_wav: Path, voice_s: float, out: Path,
                 work_dir: Path) -> float:
    """Build the intro segment with the same video/audio parameters as the clip so it can be concatenated by copy."""
    work_dir.mkdir(parents=True, exist_ok=True)
    frame = work_dir / f"{out.stem}_frame.png"
    cut.extract_frame(clip_path, frame_at, frame)
    ass = write_intro_ass(hook, work_dir / f"{out.stem}.ass")
    dur = intro_duration(voice_s)
    frames = int(dur * 60) + 1
    delay_ms = int(VOICE_START_S * 1000)
    vf = (f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          f"zoompan=z='min(1+0.00025*on,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=60,"
          f"drawbox=c=black@0.4:t=fill,format=yuv420p,ass='{cut._ass_filter_path(ass)}'[v];"
          f"[1:a]adelay={delay_ms}|{delay_ms},apad[a]")
    tools.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "60", "-i", str(frame), "-i", str(voice_wav),
               "-filter_complex", vf, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", *cut.video_encoder(), "-r", "60",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)], timeout=1800)
    frame.unlink(missing_ok=True)
    return tools.media_duration(out)


def _quote(p: Path) -> str:
    return "'" + str(p).replace("'", "'\\''") + "'"


def concat(intro: Path, clip: Path, out: Path) -> Path:
    """Intro + clip. Stream copy when the parameters match (they do when both came from the same encoder),
    otherwise a re-encode through the concat filter."""
    listing = out.with_suffix(".concat.txt")
    listing.write_text(f"file {_quote(intro)}\nfile {_quote(clip)}\n", encoding="utf-8")
    try:
        tools.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                   "-movflags", "+faststart", str(out)], timeout=600)
        if abs(tools.media_duration(out) - (tools.media_duration(intro) + tools.media_duration(clip))) > 1.0:
            raise tools.ToolError("concat by copy produced a wrong duration")
    except tools.ToolError:
        tools.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(intro), "-i", str(clip), "-filter_complex",
                   "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", *cut.video_encoder(), "-r", "60",
                   "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(out)], timeout=3600)
    finally:
        listing.unlink(missing_ok=True)
    return out
