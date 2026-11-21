"""Paths and tunable defaults. Everything the app writes lives under DATA (gitignored)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    """data/ next to the source checkout; the per-user app-data folder when running as a packaged (PyInstaller) app,
    where the install folder is read-only or replaced on update. CLIPDASH_DATA overrides both."""
    if not getattr(sys, "frozen", False):
        return ROOT / "data"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "Clip Dashboard"


DATA = Path(os.environ.get("CLIPDASH_DATA") or default_data_dir()).expanduser()
CACHE = DATA / "cache"      # downloaded segments, audio, chat replays (prunable)
CLIPS = DATA / "clips"      # rendered clips (kept)
FRAMES = DATA / "frames"    # stills for the facecam picker and thumbnails
TOOLS = DATA / "tools"      # TwitchDownloaderCLI etc.
MODELS = DATA / "models"    # whisper models
DB_PATH = DATA / "clipdash.db"
SECRETS_PATH = DATA / "secrets.json"
WEB = ROOT / "clipdash" / "web"

HOST = os.environ.get("CLIPDASH_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLIPDASH_PORT", "8765"))

# Signal tuning (also editable from Settings in the UI; stored in the settings table)
DEFAULT_SETTINGS = {
    "hype_tokens": ["KEKW", "LUL", "LULW", "OMEGALUL", "PogChamp", "POGGERS", "Pog", "WTF", "LMAO",
                    "LMFAO", "HAHA", "NOOO", "CLIP", "clip it", "NAH", "BRUH", "💀", "😭", "🤣"],
    "chat_spike_factor": 3.0,
    "audio_spike_db": 8.0,
    "max_moments": 12,
    "auto_render_top": 0,      # nothing renders until a person clicks Render (set 1 to auto-render the best moment)
    "spam_fraction": 0.5,
    "min_words_per_min": 20,
    "min_speech_fraction": 0.12,
    "llm_provider": "anthropic",
    "llm_model": "",
    "tts_engine": "auto",      # auto = ElevenLabs if a key is set, else OpenAI if set, else Piper, else the system voice
    "openai_voice": "ash",
    "openai_tts_model": "gpt-4o-mini-tts",
    "piper_voice": "en_US-ryan-high",
    "skip_start": 120,
    "min_gap": 90,
    "bin_seconds": 10,
    "lead_in": 25,             # seconds before the moment the clip starts
    "clip_len": 90,            # default clip length (the section picker offers 61 / 90 / 120 / custom)
    "download_length": 90,     # seconds fetched per moment; the clip is trimmed inside this window
    "whisper_model": "small",
    "cache_cap_mb": 3000,
    "keep_segments": False,
    "censor_captions": False,
    "suggested_hooks": False,  # model-written hooks are off; only transcript sentences are used
    "watch_interval_min": 10,
    "gta_only": True,
    "facecam_fraction": 0.32,
}

GTA_PATTERN = r"(gta\s*(6|vi)\b|grand theft auto|vice city|\bgta\b)"

SUGGESTED_CREATORS = [
    {"name": "IShowSpeed", "youtube": "@IShowSpeed", "twitch": None},
    {"name": "CaseOh", "youtube": "@CaseOh_", "twitch": "caseoh_"},
    {"name": "LACY", "youtube": "@Lacy", "twitch": "lacy"},
    {"name": "Kai Cenat", "youtube": "@KaiCenatLive", "twitch": "kaicenat"},
]


def ensure_dirs() -> None:
    for d in (DATA, CACHE, CLIPS, FRAMES, TOOLS, MODELS):
        d.mkdir(parents=True, exist_ok=True)


EXTRA_SETTINGS: dict = {}


def register_settings(defaults: dict) -> None:
    """Feature modules call this at import time; the keys become editable through PUT /api/settings and get defaults."""
    EXTRA_SETTINGS.update(defaults)
    DEFAULT_SETTINGS.update({k: v for k, v in defaults.items() if k not in DEFAULT_SETTINGS})
