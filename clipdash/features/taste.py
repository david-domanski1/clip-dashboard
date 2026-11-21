"""Learns your taste: moment ranking leans toward what this person actually approves and what earns views.

Every clip that came from a moment is a labelled example: approved or posted → +1, rejected → −1 (pending clips are
skipped). Posted clips weigh more the more views they earned (1 + log(1 + views)). A moment becomes a small numeric
feature vector read off its reasons and fields (featurize), and a tiny ridge-regularised logistic regression in numpy is
fitted per creator (video.channel), with a global model as the fallback. A scope only gets a model once it has at least
MIN_EXAMPLES labelled clips; before that nothing changes.

On the moments.scored event the scope's model (retrained lazily when older than an hour) turns each moment's probability
of approval p into a multiplier clamp(0.6, 1.5, 1 + (p − 0.5)) applied to m["score"], and appends a plain-language reason
with the honest counts of similar past decisions. This only re-orders suggestions: a person still approves every clip."""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter

from .. import config, db, events

log = logging.getLogger("clipdash.features.taste")

MIN_EXAMPLES = 8
MODEL_MAX_AGE = timedelta(hours=1)
RIDGE = 1.0
GLOBAL = "global"

FEATURES = ["chat_spike", "hype_frac", "audio_db", "heatmap_x", "cue_hits", "speech_frac", "chat_spam", "low_speech",
            "hour_of_stream", "label_bucket"]
# What each feature means in words, for the Settings sentence ("you approve …, you reject …")
FEATURE_WORDS = {
    "chat_spike": "chat explosions", "hype_frac": "hype-word chat", "audio_db": "loud reactions",
    "heatmap_x": "most-replayed moments", "cue_hits": "excited speech", "speech_frac": "the streamer talking",
    "chat_spam": "chat spam", "low_speech": "quiet moments", "hour_of_stream": "moments late in the stream",
    "label_bucket": "big reactions overall",
}
LABELS = ["Quiet", "Some reaction", "Big reaction", "Chat exploded"]

db.register_table("""CREATE TABLE IF NOT EXISTS taste_models (
  scope TEXT PRIMARY KEY, weights TEXT NOT NULL DEFAULT '{}', n INTEGER NOT NULL DEFAULT 0, trained_at TEXT NOT NULL,
  feature_means TEXT NOT NULL DEFAULT '{}', feature_stds TEXT NOT NULL DEFAULT '{}'
)""")
config.register_settings({"taste_enabled": True})

router = APIRouter()
_lock = threading.Lock()


# ---- features ----

def _num(txt: str, pattern: str) -> float:
    m = re.search(pattern, txt)
    try:
        return float(m.group(1)) if m else 0.0
    except ValueError:
        return 0.0


def strength_bucket(moment: dict) -> int:
    """0 Quiet · 1 Some reaction · 2 Big reaction · 3 Chat exploded (the same thresholds as the UI's momentStrength)."""
    f = featurize(moment, _bucket=False)
    spike, hype, heat, loud, cues = f["chat_spike"], f["hype_frac"] * 100, f["heatmap_x"], f["audio_db"], f["cue_hits"]
    if spike >= 8 or hype >= 40 or heat >= 4:
        return 3
    if spike >= 3 or heat >= 2 or loud >= 8 or cues >= 6:
        return 2
    if spike >= 1.5 or hype >= 20 or heat >= 1.5 or loud >= 4 or cues >= 3:
        return 1
    return 0


def featurize(moment: dict, _bucket: bool = True) -> dict:
    """Pure: a moment dict (or moments row) → numeric features read off its reason strings and fields."""
    txt = " ".join(str(r) for r in (moment.get("reasons") or []))
    flags = moment.get("flags") or []
    speech = moment.get("speech")
    if speech is None:
        pct = _num(txt, r"Speech in (\d+)%") or _num(txt, r"only (\d+)% of the next")
        speech = pct / 100.0
    f = {
        "chat_spike": _num(txt, r"Chat ([\d.]+)×"),
        "hype_frac": _num(txt, r"(\d+)% hype") / 100.0,
        "audio_db": _num(txt, r"Audio \+(\d+) dB"),
        "heatmap_x": _num(txt, r"most replayed' ([\d.]+)×"),
        "cue_hits": _num(txt, r"(\d+) cue words"),
        "speech_frac": float(speech or 0.0),
        "chat_spam": 1.0 if "chat_spam" in flags else 0.0,
        "low_speech": 1.0 if "low_speech" in flags else 0.0,
        "hour_of_stream": float(int(float(moment.get("t") or 0) // 3600)),
    }
    if _bucket:
        f["label_bucket"] = float(strength_bucket(moment))
    return f


def _vector(f: dict) -> np.ndarray:
    return np.array([float(f.get(k, 0.0)) for k in FEATURES], dtype=float)


# ---- training data ----

def scope_of(channel: str | None) -> str:
    s = (channel or "").strip().lower()
    return s or GLOBAL


def labelled_examples(scope: str) -> list[dict]:
    """Every clip with a moment and a decision: approved/posted → +1, rejected → −1, weighted by views when posted."""
    sql = """SELECT c.id clip_id, c.status, v.channel, m.t, m.reasons, m.flags, m.speech,
                    (SELECT SUM(COALESCE(p.views, 0)) FROM posts p WHERE p.clip_id=c.id AND p.views IS NOT NULL) views
             FROM clips c JOIN moments m ON m.id=c.moment_id JOIN videos v ON v.id=c.video_id
             WHERE c.status IN ('approved', 'posted', 'rejected')"""
    out = []
    for r in db.execute(sql).fetchall():
        r = dict(r)
        if scope != GLOBAL and scope_of(r["channel"]) != scope:
            continue
        moment = {"t": r["t"], "reasons": _loads(r["reasons"], []), "flags": _loads(r["flags"], []), "speech": r["speech"]}
        label = -1 if r["status"] == "rejected" else 1
        weight = 1.0
        if r["status"] == "posted" and r["views"] is not None:
            weight = 1.0 + math.log1p(max(0, int(r["views"])))
        out.append({"clip_id": r["clip_id"], "label": label, "weight": weight, "features": featurize(moment),
                    "channel": r["channel"]})
    return out


def _loads(v, default):
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v) if v else default
    except ValueError:
        return default


# ---- model ----

def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def fit_logistic(X: np.ndarray, y: np.ndarray, sample_weight: np.ndarray | None = None, l2: float = RIDGE,
                 iters: int = 50) -> tuple[np.ndarray, float]:
    """Ridge-regularised logistic regression by Newton's method. X standardised (n × d), y in {−1, +1}.
    Returns (weights, bias); the bias is not regularised. The ridge keeps separable data from running off to infinity."""
    n, d = X.shape
    sw = np.ones(n) if sample_weight is None else np.asarray(sample_weight, dtype=float)
    sw = sw * (n / max(sw.sum(), 1e-9))
    y01 = (np.asarray(y, dtype=float) + 1) / 2
    Xb = np.hstack([X, np.ones((n, 1))])
    reg = np.full(d + 1, float(l2))
    reg[-1] = 0.0
    beta = np.zeros(d + 1)
    for _ in range(iters):
        p = sigmoid(Xb @ beta)
        grad = Xb.T @ (sw * (p - y01)) + reg * beta
        w = sw * p * (1 - p) + 1e-9
        hess = (Xb * w[:, None]).T @ Xb + np.diag(reg) + 1e-6 * np.eye(d + 1)
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.max(np.abs(step)) < 1e-7:
            break
    return beta[:-1], float(beta[-1])


def train(scope: str) -> dict | None:
    """Fit and store the model for a scope; returns the stored row, or None (and forgets any old model) when the scope
    has fewer than MIN_EXAMPLES labelled clips or only one kind of decision."""
    ex = labelled_examples(scope)
    labels = {e["label"] for e in ex}
    if len(ex) < MIN_EXAMPLES or len(labels) < 2:
        db.execute("DELETE FROM taste_models WHERE scope=?", (scope,))
        return None
    X = np.vstack([_vector(e["features"]) for e in ex])
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    stds[stds < 1e-9] = 1.0
    Z = (X - means) / stds
    w, b = fit_logistic(Z, np.array([e["label"] for e in ex]), np.array([e["weight"] for e in ex]))
    weights = {k: round(float(v), 6) for k, v in zip(FEATURES, w)}
    weights["bias"] = round(b, 6)
    row = {"scope": scope, "weights": json.dumps(weights), "n": len(ex), "trained_at": db.now(),
           "feature_means": json.dumps(dict(zip(FEATURES, map(float, means)))),
           "feature_stds": json.dumps(dict(zip(FEATURES, map(float, stds))))}
    db.execute("INSERT INTO taste_models(scope, weights, n, trained_at, feature_means, feature_stds) VALUES (?,?,?,?,?,?)"
               " ON CONFLICT(scope) DO UPDATE SET weights=excluded.weights, n=excluded.n, trained_at=excluded.trained_at,"
               " feature_means=excluded.feature_means, feature_stds=excluded.feature_stds",
               (row["scope"], row["weights"], row["n"], row["trained_at"], row["feature_means"], row["feature_stds"]))
    return load_model(scope)


def train_all() -> list[dict]:
    scopes = {GLOBAL} | {scope_of(e["channel"]) for e in labelled_examples(GLOBAL)}
    return [m for m in (train(s) for s in sorted(scopes)) if m]


def load_model(scope: str) -> dict | None:
    r = db.execute("SELECT * FROM taste_models WHERE scope=?", (scope,)).fetchone()
    if r is None:
        return None
    r = dict(r)
    for k in ("weights", "feature_means", "feature_stds"):
        r[k] = _loads(r[k], {})
    return r


def _stale(model: dict | None) -> bool:
    if not model:
        return True
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(model["trained_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return age > MODEL_MAX_AGE


def model_for(scope: str) -> dict | None:
    """The creator's model, trained lazily when missing or older than an hour; the global model when the creator has
    too few decisions; None when there is nothing to learn from yet."""
    with _lock:
        for s in ((scope, GLOBAL) if scope != GLOBAL else (GLOBAL,)):
            m = load_model(s)
            if _stale(m):
                m = train(s)
            if m:
                return m
    return None


def probability(model: dict, features: dict) -> float:
    z = 0.0
    for k in FEATURES:
        std = float(model["feature_stds"].get(k, 1.0)) or 1.0
        z += float(model["weights"].get(k, 0.0)) * (float(features.get(k, 0.0)) - float(model["feature_means"].get(k, 0.0))) / std
    return float(sigmoid(z + float(model["weights"].get("bias", 0.0))))


def multiplier(p: float) -> float:
    """clamp(0.6, 1.5, 1 + 0.5 × (p − 0.5) × 2): a 50/50 moment is untouched, a sure yes ×1.5, a sure no ×0.6."""
    return round(min(1.5, max(0.6, 1 + 0.5 * (p - 0.5) * 2)), 4)


def similar_counts(examples: list[dict], bucket: int) -> tuple[int, int]:
    """(approved, total) among past decisions in the nearest strength bucket, so the reason stays honest."""
    if not examples:
        return 0, 0
    best = min(abs(int(e["features"].get("label_bucket", 0)) - bucket) for e in examples)
    near = [e for e in examples if abs(int(e["features"].get("label_bucket", 0)) - bucket) == best]
    return sum(1 for e in near if e["label"] > 0), len(near)


def reason_text(approved: int, total: int, scope: str, mult: float) -> str:
    pct = int(round((mult - 1) * 100))
    change = "no change" if pct == 0 else (f"+{pct}%" if pct > 0 else f"−{abs(pct)}%")
    who = "across your creators" if scope == GLOBAL else f"for {scope}"
    return f"Your taste: moments like this were approved {approved} of {total} times {who} ({change})"


# ---- event handler ----

def on_moments_scored(video: dict, moments: list, settings: dict | None = None, **_) -> None:
    """Adjust m["score"] in place from the learned model and append a reason. Never raises; no data → no change."""
    try:
        settings = settings if settings is not None else db.all_settings()
        if not settings.get("taste_enabled", True) or not moments:
            return
        scope = scope_of((video or {}).get("channel"))
        model = model_for(scope)
        if not model:
            return
        examples = labelled_examples(model["scope"])
        for m in moments:
            f = featurize(m)
            mult = multiplier(probability(model, f))
            m["score"] = round(float(m.get("score") or 0.0) * mult, 2)
            m["taste"] = mult
            approved, total = similar_counts(examples, int(f["label_bucket"]))
            m.setdefault("reasons", []).append(reason_text(approved, total, model["scope"], mult))
    except Exception:  # noqa: BLE001
        log.exception("taste adjustment skipped")


_registered = False


def register() -> None:
    global _registered
    if not _registered:
        events.on("moments.scored", on_moments_scored)
        _registered = True


# ---- status / API ----

def describe(model: dict | None, top: int = 2, min_weight: float = 0.05) -> tuple[list[str], list[str]]:
    """Top positive and negative features in words. Weights act on standardised features so they are comparable."""
    if not model:
        return [], []
    ws = sorted(((float(model["weights"].get(k, 0.0)), k) for k in FEATURES), key=lambda x: -abs(x[0]))
    likes = [FEATURE_WORDS[k] for w, k in ws if w > min_weight][:top]
    dislikes = [FEATURE_WORDS[k] for w, k in ws if w < -min_weight][:top]
    return likes, dislikes


def _sentence(name: str, n: int, trained: bool, likes: list[str], dislikes: list[str]) -> str:
    if not trained:
        if n == 0:
            return f"No decisions on {name} yet. Approve or reject a few clips and it starts learning at {MIN_EXAMPLES}."
        return f"{n} of your decisions on {name} so far; it starts learning at {MIN_EXAMPLES}."
    parts = []
    if likes:
        parts.append("you approve " + " and ".join(likes))
    if dislikes:
        parts.append("you reject " + " and ".join(dislikes))
    return f"Learned from {n} of your decisions on {name}: " + (", ".join(parts) if parts else "no strong pattern yet") + "."


def status() -> dict:
    ex = labelled_examples(GLOBAL)
    by_scope: dict[str, list[dict]] = {}
    for e in ex:
        by_scope.setdefault(scope_of(e["channel"]), []).append(e)
    scopes = []
    for scope, rows in sorted(by_scope.items(), key=lambda kv: (-len(kv[1]), kv[0])) + [(GLOBAL, ex)]:
        model = load_model(scope)
        likes, dislikes = describe(model)
        name = "all creators" if scope == GLOBAL else scope
        scopes.append({"scope": scope, "name": name, "n": len(rows), "approved": sum(1 for e in rows if e["label"] > 0),
                       "rejected": sum(1 for e in rows if e["label"] < 0), "trained": bool(model),
                       "trained_at": model["trained_at"] if model else None, "likes": likes, "dislikes": dislikes,
                       "sentence": _sentence(name, len(rows), bool(model), likes, dislikes)})
    return {"enabled": bool(db.get_setting("taste_enabled", True)), "min_examples": MIN_EXAMPLES, "scopes": scopes}


@router.get("/api/taste/status")
def api_status():
    return status()


@router.post("/api/taste/retrain")
def api_retrain():
    with _lock:
        trained = train_all()
    return {"trained": [m["scope"] for m in trained], **status()}


@router.post("/api/taste/reset")
def api_reset():
    """Forget the learned weights. Your decisions stay in the clips table; the model is relearned at the next scan."""
    with _lock:
        db.execute("DELETE FROM taste_models")
    return status()
