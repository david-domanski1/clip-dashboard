"""Text-to-speech for the narrated intro. Local first so it runs for free: Piper (pip, ~60 MB voice downloaded once)
then the operating system's own voice (macOS `say`, Windows SAPI). ElevenLabs is optional behind a key."""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from . import config, db, secrets_store, tools

PIPER_DIR = config.MODELS / "piper"
DEFAULT_PIPER_VOICE = "en_US-lessac-medium"


def piper_voice_path(voice: str | None = None) -> Path:
    return PIPER_DIR / f"{(voice or db.get_setting('piper_voice') or DEFAULT_PIPER_VOICE)}.onnx"


def piper_available() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return piper_voice_path().exists()


def download_piper_voice(progress=None) -> Path:
    say = progress or (lambda s: None)
    voice = db.get_setting("piper_voice") or DEFAULT_PIPER_VOICE
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    say(f"Downloading Piper voice {voice} (~60 MB, once)…")
    tools.run([sys.executable, "-m", "piper.download_voices", voice, "--download-dir", str(PIPER_DIR)], timeout=900)
    say("Voice ready.")
    return piper_voice_path(voice)


def _to_wav48(src: Path, out: Path) -> Path:
    tools.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out)], timeout=120)
    return out


def _piper(text: str, out: Path) -> Path:
    model = piper_voice_path()
    if not model.exists():
        download_piper_voice()
    raw = out.with_suffix(".piper.wav")
    p = subprocess.run([sys.executable, "-m", "piper", "-m", str(model), "-f", str(raw), "--length-scale", "1.05"], input=text,
                       capture_output=True, text=True, timeout=300, **tools._popen_kwargs())
    if p.returncode != 0 or not raw.exists():
        raise tools.ToolError(f"Piper failed: {(p.stderr or p.stdout)[-300:]}")
    try:
        return _to_wav48(raw, out)
    finally:
        raw.unlink(missing_ok=True)


def _system(text: str, out: Path) -> Path:
    sysname = platform.system()
    if sysname == "Darwin":
        aiff = out.with_suffix(".aiff")
        voice = db.get_setting("system_voice") or "Samantha"
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
    raise tools.ToolError("No system voice on this OS; install the Piper voice in Setup instead")


def _elevenlabs(text: str, out: Path) -> Path:
    import requests
    key = secrets_store.get("elevenlabs_api_key")
    if not key:
        raise tools.ToolError("Add your ElevenLabs API key in Setup → Narration")
    voice_id = db.get_setting("elevenlabs_voice_id") or "21m00Tcm4TlvDq8ikWAM"
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


ENGINES = {"piper": _piper, "system": _system, "elevenlabs": _elevenlabs}


def engines_status() -> list[dict]:
    out = [{"id": "piper", "label": "Piper (local, free)", "ok": piper_available(),
            "detail": "voice installed" if piper_available() else "voice not downloaded yet"}]
    sysname = platform.system()
    out.append({"id": "system", "label": f"{'macOS' if sysname == 'Darwin' else sysname} built-in voice", "ok": sysname in ("Darwin", "Windows"),
                "detail": "available" if sysname in ("Darwin", "Windows") else "not on this OS"})
    out.append({"id": "elevenlabs", "label": "ElevenLabs (key)", "ok": secrets_store.has("elevenlabs_api_key"),
                "detail": "key saved" if secrets_store.has("elevenlabs_api_key") else "no key"})
    return out


def synthesize(text: str, out: Path, engine: str | None = None) -> tuple[Path, float, str]:
    """Returns (wav path at 48 kHz stereo, duration seconds, engine used). engine 'auto' tries piper, then system."""
    engine = engine or db.get_setting("tts_engine") or "auto"
    order = [engine] if engine in ENGINES else ["piper", "system"]
    errors = []
    for name in order:
        try:
            path = ENGINES[name](text, out)
            return path, tools.media_duration(path), name
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[-160:]}")
    raise tools.ToolError("No voice could be produced: " + " | ".join(errors))
