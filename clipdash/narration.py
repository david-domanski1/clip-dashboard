"""Narrated hook intro: 3–6 s of the clip's first frame (slow push-in, darkened ~40%) while a synthetic voice reads the
hook. The hook's words appear one by one in sync with the voice, in the same caption style as the clip, and the intro is
concatenated in front of the real clip. The original clip is kept, so narration can be switched off and the clip
re-exported without it.

Word timing: the voice file is run through the same Whisper model as the clip captions (word timestamps); the hook's own
words are then laid on those timings (one-to-one when the counts match, otherwise spread by letter count between the
first and last spoken word). If Whisper is unavailable the words are spread evenly across the voice's length."""
from __future__ import annotations

import re
from pathlib import Path

from . import cut, tools

VOICE_START_S = 0.3
MIN_S, MAX_S = 3.0, 6.0


def intro_duration(voice_s: float) -> float:
    """Voice starts within the first second; the real action starts about when the voice ends (3–6 s total)."""
    return max(MIN_S, min(MAX_S, VOICE_START_S + voice_s + 0.6))


def _tokens(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def spread_words(words: list[str], start: float, end: float) -> list[tuple[float, float, str]]:
    """Lay words between start and end in proportion to their length (longer words take longer to say)."""
    if not words:
        return []
    end = max(end, start + 0.1 * len(words))
    weights = [max(1, len(re.sub(r"[^\w]", "", w))) + 1 for w in words]
    total = float(sum(weights))
    out, t = [], start
    for w, wt in zip(words, weights):
        d = (end - start) * wt / total
        out.append((round(t, 2), round(t + d, 2), w))
        t += d
    return out


def align_words(hook: str, spoken: list[list], voice_s: float) -> list[tuple[float, float, str]]:
    """Timings for the hook's words from a word-timed transcript of the voice file ([[start, end, word], ...])."""
    words = _tokens(hook)
    if not words:
        return []
    spoken = [s for s in spoken if s[1] > s[0] >= 0]
    if not spoken:
        return spread_words(words, 0.0, voice_s)
    if len(spoken) == len(words):
        return [(round(float(s[0]), 2), round(float(s[1]), 2), w) for s, w in zip(spoken, words)]
    return spread_words(words, float(spoken[0][0]), float(spoken[-1][1]))


def word_timings(hook: str, voice_wav: Path, voice_s: float, model_name: str = "small") -> list[tuple[float, float, str]]:
    """Word-by-word timings of the hook as spoken in voice_wav; falls back to an even spread if Whisper cannot run."""
    try:
        spoken = cut.transcribe(voice_wav, model_name)
    except Exception:  # noqa: BLE001
        spoken = []
    return align_words(hook, spoken, voice_s)


def _esc(s: str) -> str:
    return s.replace("{", "(").replace("}", ")").replace("\\", "/").upper()


def write_intro_ass(hook: str, out: Path, words: list[tuple[float, float, str]] | None = None, voice_start: float = VOICE_START_S,
                    w: int = 1080, h: int = 1920) -> Path:
    """The hook in the clip's caption style (same font, size, outline and position as cut.write_ass), revealed word by word.
    Uses ASS karaoke timing with an invisible 'not yet sung' colour, so each word pops in when the voice reaches it."""
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,78,&H00FFFFFF,&HFF000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    if not words:
        text = "{\\fad(150,250)}" + _esc(hook)
    else:
        # Each word starts fully transparent (fill, outline and shadow) and switches to visible at its spoken time
        # (\t times are milliseconds from the line start). Invisible words still take up space, so the layout never jumps.
        parts = []
        for start, end, word in words:
            at = int(round((voice_start + float(start)) * 1000))
            parts.append(f"{{\\alpha&HFF&\\t({at},{at + 40},\\alpha&H00&)}}{_esc(word)} ")
        text = "".join(parts).rstrip()
    out.write_text(hdr + f"Dialogue: 0,0:00:00.00,9:59:59.00,Cap,,0,0,0,,{text}\n", encoding="utf-8")
    return out


def render_intro(clip_path: Path, frame_at: float, hook: str, voice_wav: Path, voice_s: float, out: Path,
                 work_dir: Path, words: list[tuple[float, float, str]] | None = None) -> float:
    """Build the intro segment with the same video/audio parameters as the clip so it can be concatenated by copy."""
    work_dir.mkdir(parents=True, exist_ok=True)
    frame = work_dir / f"{out.stem}_frame.png"
    cut.extract_frame(clip_path, frame_at, frame)
    ass = write_intro_ass(hook, work_dir / f"{out.stem}.ass", words)
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
