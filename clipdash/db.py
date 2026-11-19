"""SQLite state. One connection per thread, dict rows, JSON columns decoded on read."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS campaigns (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, creator TEXT NOT NULL, url TEXT, submission_url TEXT,
  required_names TEXT NOT NULL DEFAULT '[]', required_hashtags TEXT NOT NULL DEFAULT '[]',
  min_len INTEGER NOT NULL DEFAULT 61, max_len INTEGER NOT NULL DEFAULT 180,
  platforms TEXT NOT NULL DEFAULT '["youtube","tiktok","instagram"]',
  source_start TEXT, source_end TEXT, watermark_allowed INTEGER NOT NULL DEFAULT 0,
  submit_minutes INTEGER NOT NULL DEFAULT 30, pay_model TEXT NOT NULL DEFAULT 'cpm',
  rate_per_1k REAL NOT NULL DEFAULT 0, per_clip_cap REAL NOT NULL DEFAULT 0,
  pool_budget REAL NOT NULL DEFAULT 0, min_views INTEGER NOT NULL DEFAULT 0,
  checklist TEXT NOT NULL DEFAULT '[]', notes TEXT NOT NULL DEFAULT '', rules_text TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  kind TEXT NOT NULL, url TEXT NOT NULL, external_id TEXT, name TEXT, handle TEXT,
  facecam TEXT, layout TEXT NOT NULL DEFAULT 'stacked', watch INTEGER NOT NULL DEFAULT 1,
  last_checked TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  platform TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT NOT NULL, title TEXT, channel TEXT,
  published_at TEXT, duration REAL, is_stream INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT '',
  signals TEXT NOT NULL DEFAULT '{}', error TEXT, created_at TEXT NOT NULL,
  UNIQUE(campaign_id, platform, external_id)
);

CREATE TABLE IF NOT EXISTS moments (
  id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  t REAL NOT NULL, score REAL NOT NULL, reasons TEXT NOT NULL DEFAULT '[]', chat_words TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'found', rank INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  moment_id INTEGER REFERENCES moments(id) ON DELETE SET NULL,
  path TEXT, final_path TEXT, thumb TEXT, source_start REAL NOT NULL, source_length REAL NOT NULL,
  trim_start REAL NOT NULL DEFAULT 0, trim_end REAL, duration REAL, width INTEGER, height INTEGER,
  transcript TEXT NOT NULL DEFAULT '[]', hook TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
  hashtags TEXT NOT NULL DEFAULT '[]', post_text TEXT NOT NULL DEFAULT '{}',
  checklist TEXT NOT NULL DEFAULT '{}', rule_results TEXT NOT NULL DEFAULT '[]',
  watermark INTEGER NOT NULL DEFAULT 0, ai_text INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'rendering', error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, platform TEXT NOT NULL, label TEXT NOT NULL, route TEXT NOT NULL DEFAULT 'assisted',
  handle TEXT, external_id TEXT, status TEXT NOT NULL DEFAULT 'unverified', status_detail TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '{}', campaign_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY, clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
  platform TEXT NOT NULL, route TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled',
  scheduled_at TEXT, tz TEXT, posted_at TEXT, url TEXT, external_id TEXT, state_detail TEXT NOT NULL DEFAULT '',
  error TEXT, attempts INTEGER NOT NULL DEFAULT 0, submitted_at TEXT, submission_deadline TEXT,
  post_text TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', options TEXT NOT NULL DEFAULT '{}',
  views INTEGER, views_checked_at TEXT, log TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, type TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued', progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
  error TEXT, attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
"""

JSON_COLS = {
    "campaigns": {"required_names", "required_hashtags", "platforms", "checklist"},
    "sources": {"facecam"},
    "videos": {"signals", "scanned_ranges", "scan_window"},
    "moments": {"reasons", "chat_words", "flags"},
    "clips": {"transcript", "hashtags", "post_text", "checklist", "rule_results", "flags", "timings"},
    "accounts": {"meta", "campaign_ids"},
    "posts": {"options", "log"},
    "jobs": {"payload"},
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        config.ensure_dirs()
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return c


MIGRATIONS = {
    "moments": [("thumb", "TEXT"), ("flags", "TEXT NOT NULL DEFAULT '[]'"), ("raw_score", "REAL"), ("speech", "REAL")],
    "clips": [("speech_wpm", "REAL"), ("flags", "TEXT NOT NULL DEFAULT '[]'"), ("timings", "TEXT NOT NULL DEFAULT '{}'")],
    "videos": [("scan_start", "REAL"), ("scan_end", "REAL"), ("scanned_ranges", "TEXT NOT NULL DEFAULT '[]'"),
               ("override", "INTEGER NOT NULL DEFAULT 0"), ("live", "INTEGER NOT NULL DEFAULT 0"),
               ("scan_window", "TEXT NOT NULL DEFAULT '{}'")],
}


def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _decode(table: str, row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLS.get(table, ()):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    return d


def _encode(table: str, data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k in JSON_COLS.get(table, ()) or isinstance(v, (dict, list)):
            v = json.dumps(v)
        elif isinstance(v, bool):
            v = int(v)
        out[k] = v
    return out


def rows(table: str, where: str = "1=1", params: tuple = (), order: str = "id") -> list[dict]:
    cur = conn().execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {order}", params)
    return [_decode(table, r) for r in cur.fetchall()]


def get(table: str, id_: int) -> dict | None:
    return _decode(table, conn().execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone())


def one(table: str, where: str, params: tuple = ()) -> dict | None:
    return _decode(table, conn().execute(f"SELECT * FROM {table} WHERE {where}", params).fetchone())


def insert(table: str, data: dict) -> int:
    data = dict(data)
    if "created_at" not in data and table not in ("settings", "moments"):
        data["created_at"] = now()
    if table in ("clips", "posts") and "updated_at" not in data:
        data["updated_at"] = data["created_at"]
    enc = _encode(table, data)
    cols = ", ".join(enc)
    marks = ", ".join("?" for _ in enc)
    cur = conn().execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(enc.values()))
    return int(cur.lastrowid)


def update(table: str, id_: int, data: dict) -> None:
    if not data:
        return
    data = dict(data)
    if table in ("clips", "posts"):
        data["updated_at"] = now()
    enc = _encode(table, data)
    sets = ", ".join(f"{k}=?" for k in enc)
    conn().execute(f"UPDATE {table} SET {sets} WHERE id=?", (*enc.values(), id_))


def delete(table: str, id_: int) -> None:
    conn().execute(f"DELETE FROM {table} WHERE id=?", (id_,))


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    return conn().execute(sql, params)


# ---- settings ----

def get_setting(key: str, default: Any = None) -> Any:
    r = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if r is None:
        return config.DEFAULT_SETTINGS.get(key, default)
    return json.loads(r["value"])


def set_setting(key: str, value: Any) -> None:
    conn().execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))


def all_settings() -> dict:
    out = dict(config.DEFAULT_SETTINGS)
    for r in conn().execute("SELECT key, value FROM settings"):
        out[r["key"]] = json.loads(r["value"])
    return out
