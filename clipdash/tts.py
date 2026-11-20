"""Text-to-speech for the narrated intro.

Engines: ElevenLabs (key), OpenAI (key; gpt-4o-mini-tts or tts-1-hd), Piper (local, free; voices download once),
the operating system's own voice (macOS `say`, Windows SAPI). "Auto" picks the best one that is set up:
ElevenLabs if its key is saved, else OpenAI if its key is saved, else Piper, else the system voice.

Every engine takes an optional voice id; the voice picker in Settings → Setup lists them and plays a short cached
sample per voice (`sample()`), so choosing one is instant after the first play."""
from __future__ import annotations

import hashlib
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config, db, secrets_store, tools

PIPER_DIR = config.MODELS / "piper"
# Best-sounding English voices first; the first one installed is used unless a voice is chosen in Settings.
PIPER_CANDIDATES = ["en_US-ryan-high", "en_US-hfc_male-medium", "en_GB-alan-medium", "en_US-lessac-medium"]
DEFAULT_PIPER_VOICE = PIPER_CANDIDATES[0]
OPENAI_VOICES = ["ash", "onyx", "nova", "alloy", "ballad", "coral", "echo", "fable", "sage", "shimmer", "verse"]
DEFAULT_OPENAI_VOICE = "ash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini-tts"
DEFAULT_ELEVEN_VOICE = "21m00Tcm4TlvDq8ikWAM"   # "Rachel"
SAMPLE_TEXT = "Wait for what happens next."
SAMPLE_DIR = config.CACHE / "tts_samples"


# ---------------------------------------------------------------- Piper

def piper_installed() -> list[str]:
    """Installed Piper voices, best candidates first, then anything else in the folder."""
    if not PIPER_DIR.exists():
        return []
    have = {p.stem for p in PIPER_DIR.glob("*.onnx")}
    return [v for v in PIPER_CANDIDATES if v in have] + sorted(v for v in have if v not in PIPER_CANDIDATES)


def piper_voice(voice: str | None = None) -> str:
    """The voice to use: the one asked for, else the one chosen in Settings, else the best installed, else the default."""
    chosen = voice or db.get_setting("piper_voice") or ""
    installed = piper_installed()
    if chosen and chosen in installed:
        return chosen
    return installed[0] if installed else (chosen or DEFAULT_PIPER_VOICE)


def piper_voice_path(voice: str | None = None) -> Path:
    return PIPER_DIR / f"{piper_voice(voice)}.onnx"


def piper_available() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return bool(piper_installed())


def download_piper_voice(progress=None, voice: str | None = None) -> Path:
    say = progress or (lambda s: None)
    voice = voice or db.get_setting("piper_voice") or DEFAULT_PIPER_VOICE
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    say(f"Downloading Piper voice {voice} (60–120 MB, once)…")
    tools.run([sys.executable, "-m", "piper.download_voices", voice, "--download-dir", str(PIPER_DIR)], timeout=1800)
    if not (PIPER_DIR / f"{voice}.onnx").exists():
        raise tools.ToolError(f"Piper voice {voice} did not download")
    say("Voice ready.")
    return PIPER_DIR / f"{voice}.onnx"


def _to_wav48(src: Path, out: Path) -> Path:
    tools.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], timeout=120)
    return out


def _piper(text: str, out: Path, voice: str | None = None) -> Path:
    model = piper_voice_path(voice)
    if not model.exists():
        download_piper_voice(voice=model.stem)
    raw = out.with_suffix(".piper.wav")
    p = subprocess.run([sys.executable, "-m", "piper", "-m", str(model), "-f", str(raw), "--length-scale", "1.05"], input=text,
                       capture_output=True, text=True, timeout=300, **tools._popen_kwargs())
    if p.returncode != 0 or not raw.exists():
        raise tools.ToolError(f"Piper failed: {(p.stderr or p.stdout)[-300:]}")
    try:
        return _to_wav48(raw, out)
    finally:
        raw.unlink(missing_ok=True)


# ---------------------------------------------------------------- system voice

def _system(text: str, out: Path, voice: str | None = None) -> Path:
    sysname = platform.system()
    if sysname == "Darwin":
        aiff = out.with_suffix(".aiff")
        voice = voice or db.get_setting("system_voice") or "Samantha"
        p = subprocess.run(["say", "-v", voice, "-o", str(aiff), text], capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            p = subprocess.run(["say", "-o", str(aiff), text], capture_output=True, text=True, timeout=120)
        if p.returncode != 0 or not aiff.exists():
            raise tools.ToolError(f"macOS say failed: {p.stderr[-200:]}")
        try:
            return _to_wav48(aiff, out)
        finally:
            aiff.unlink(missing_ok=True)
    if sysname == "Windows":
        raw = out.with_suffix(".sapi.wav")
        safe = text.replace("'", "''")
        script = (f"Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  f"$s.SetOutputToWaveFile('{raw}'); $s.Speak('{safe}'); $s.Dispose()")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=120, **tools._popen_kwargs())
        if p.returncode != 0 or not raw.exists():
            raise tools.ToolError(f"Windows speech failed: {(p.stderr or p.stdout)[-200:]}")
        try:
            return _to_wav48(raw, out)
        finally:
            raw.unlink(missing_ok=True)
    raise tools.ToolError("No system voice on this OS; install a Piper voice in Settings → Setup instead")


# ---------------------------------------------------------------- OpenAI

def _openai(text: str, out: Path, voice: str | None = None) -> Path:
    import requests
    key = secrets_store.get("openai_api_key")
    if not key:
        raise tools.ToolError("Add your OpenAI API key in Settings → Setup → Narration")
    voice = voice or db.get_setting("openai_voice") or DEFAULT_OPENAI_VOICE
    model = db.get_setting("openai_tts_model") or DEFAULT_OPENAI_MODEL
    r = requests.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": model, "voice": voice, "input": text, "response_format": "wav"}, timeout=120)
    if r.status_code >= 400:
        raise tools.ToolError(f"OpenAI speech error {r.status_code}: {r.text[:200]}")
    raw = out.with_suffix(".openai.wav")
    raw.write_bytes(r.content)
    try:
        return _to_wav48(raw, out)
    finally:
        raw.unlink(missing_ok=True)


# ---------------------------------------------------------------- ElevenLabs

_eleven_cache: dict = {"t": 0.0, "voices": []}


def elevenlabs_voices() -> list[dict]:
    """Voices on the account (cached 10 minutes); empty without a key."""
    import requests
    key = secrets_store.get("elevenlabs_api_key")
    if not key:
        return []
    if time.time() - _eleven_cache["t"] < 600 and _eleven_cache["voices"]:
        return _eleven_cache["voices"]
    try:
        r = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=30)
        voices = [{"id": v["voice_id"], "label": v.get("name") or v["voice_id"]} for v in r.json().get("voices", [])] if r.status_code == 200 else []
    except Exception:  # noqa: BLE001
        voices = []
    if voices:
        _eleven_cache.update({"t": time.time(), "voices": voices})
    return voices or [{"id": DEFAULT_ELEVEN_VOICE, "label": "Rachel (default)"}]


def _elevenlabs(text: str, out: Path, voice: str | None = None) -> Path:
    import requests
    key = secrets_store.get("elevenlabs_api_key")
    if not key:
        raise tools.ToolError("Add your ElevenLabs API key in Settings → Setup → Narration")
    voice_id = voice or db.get_setting("elevenlabs_voice_id") or DEFAULT_ELEVEN_VOICE
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}", headers={"xi-api-key": key, "Content-Type": "application/json"},
                      json={"text": text, "model_id": db.get_setting("elevenlabs_model") or "eleven_multilingual_v2"}, timeout=120)
    if r.status_code >= 400:
        raise tools.ToolError(f"ElevenLabs error {r.status_code}: {r.text[:200]}")
    mp3 = out.with_suffix(".mp3")
    mp3.write_bytes(r.content)
    try:
        return _to_wav48(mp3, out)
    finally:
        mp3.unlink(missing_ok=True)


ENGINES = {"elevenlabs": _elevenlabs, "openai": _openai, "piper": _piper, "system": _system}
LABELS = {"elevenlabs": "ElevenLabs (key)", "openai": "OpenAI (key)", "piper": "Piper (local, free)", "system": "built-in voice"}


def engine_ready(name: str) -> bool:
    if name == "elevenlabs":
        return secrets_store.has("elevenlabs_api_key")
    if name == "openai":
        return secrets_store.has("openai_api_key")
    if name == "piper":
        return piper_available()
    if name == "system":
        return platform.system() in ("Darwin", "Windows")
    return False


def default_order() -> list[str]:
    """What 'auto' tries, in order: ElevenLabs if a key is set, else OpenAI if set, else Piper, then the system voice."""
    return [e for e in ("elevenlabs", "openai", "piper", "system") if engine_ready(e)] or ["piper", "system"]


def engines_status() -> list[dict]:
    sysname = platform.system()
    inst = piper_installed()
    return [
        {"id": "elevenlabs", "label": LABELS["elevenlabs"], "ok": engine_ready("elevenlabs"), "detail": "key saved" if engine_ready("elevenlabs") else "no key"},
        {"id": "openai", "label": LABELS["openai"], "ok": engine_ready("openai"), "detail": "key saved" if engine_ready("openai") else "no key"},
        {"id": "piper", "label": LABELS["piper"], "ok": bool(inst), "detail": ("voices installed: " + ", ".join(inst)) if inst else "no voice downloaded yet"},
        {"id": "system", "label": f"{'macOS' if sysname == 'Darwin' else sysname} built-in voice", "ok": sysname in ("Darwin", "Windows"),
         "detail": "available" if sysname in ("Darwin", "Windows") else "not on this OS"},
    ]


def voices() -> dict:
    """Voice choices per engine for the picker, with the current selection and what 'auto' resolves to."""
    inst = piper_installed()
    return {
        "auto": default_order()[0],
        "piper": {"current": piper_voice(), "voices": [{"id": v, "label": v.replace("_", " "), "installed": v in inst} for v in PIPER_CANDIDATES + [x for x in inst if x not in PIPER_CANDIDATES]]},
        "openai": {"current": db.get_setting("openai_voice") or DEFAULT_OPENAI_VOICE, "model": db.get_setting("openai_tts_model") or DEFAULT_OPENAI_MODEL,
                   "voices": [{"id": v, "label": v, "installed": True} for v in OPENAI_VOICES], "ready": engine_ready("openai")},
        "elevenlabs": {"current": db.get_setting("elevenlabs_voice_id") or DEFAULT_ELEVEN_VOICE, "voices": [{**v, "installed": True} for v in elevenlabs_voices()],
                       "ready": engine_ready("elevenlabs")},
    }


def sample(engine: str, voice: str | None = None, text: str = SAMPLE_TEXT) -> Path:
    """A short sample for one voice, synthesized once and kept on disk so replaying is instant."""
    if engine not in ENGINES:
        raise tools.ToolError(f"Unknown voice engine {engine}")
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{engine}|{voice or ''}|{text}".encode()).hexdigest()[:12]
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", voice or "default")[:40]
    out = SAMPLE_DIR / f"{engine}_{safe}_{key}.wav"
    if out.exists() and out.stat().st_size > 1000:
        return out
    return ENGINES[engine](text, out, voice)


def synthesize(text: str, out: Path, engine: str | None = None, voice: str | None = None) -> tuple[Path, float, str]:
    """Returns (wav path at 48 kHz stereo, duration seconds, engine used). engine 'auto' follows default_order()."""
    engine = engine or db.get_setting("tts_engine") or "auto"
    order = [engine] if engine in ENGINES else default_order()
    errors = []
    for name in order:
        try:
            path = ENGINES[name](text, out, voice if engine in ENGINES else None)
            return path, tools.media_duration(path), name
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[-160:]}")
    raise tools.ToolError("No voice could be produced: " + " | ".join(errors))
