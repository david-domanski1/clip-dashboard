"""Paths and tunable defaults. Everything the app writes lives under DATA (gitignored)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CLIPDASH_DATA", ROOT / "data")).expanduser()
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
    "auto_render_top": 1,
    "spam_fraction": 0.5,
    "min_words_per_min": 20,
    "min_speech_fraction": 0.12,
    "llm_provider": "anthropic",
    "llm_model": "",
    "tts_engine": "auto",
    "piper_voice": "en_US-lessac-medium",
    "skip_start": 120,
    "min_gap": 90,
    "bin_seconds": 10,
    "lead_in": 25,
    "download_length": 90,     # seconds fetched per moment; the clip is trimmed inside this window
    "whisper_model": "small",
    "cache_cap_mb": 3000,
    "keep_segments": False,
    "censor_captions": False,
    "suggested_hooks": False,  # model-written hooks are off; only transcript sentences are used
    "watch_interval_min": 30,
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
