"""Highlight signals and moment picking. Pure functions over arrays so they are unit-testable.

Every signal is converted to a comparable 'intensity' (roughly: how many times its usual level),
then combined. Each chosen moment carries human-readable reasons explaining why it was picked.
"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np

CUE_WORDS = ["oh my god", "no way", "what the", "bro", "nah", "holy", "yo", "wait", "insane", "crazy",
             "chat", "clip", "let's go", "lets go", "stop", "nooo", "what", "wow", "jesus", "dude"]
LAUGH = re.compile(r"\b(ha){2,}|lol|lmao|lmfao|😂|🤣|💀", re.I)


def _rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    if len(x) == 0:
        return x
    return np.array([np.median(x[max(0, i - win):i + 1]) for i in range(len(x))])


def chat_signal(rows: list[list], hype_tokens: list[str], bin_s: int = 10, baseline_s: int = 600) -> dict:
    """rows: [[t, message], ...]. Returns intensity = rate spike × (1 + 2·hype fraction) plus explain arrays."""
    if not rows:
        return {"bins": np.array([]), "intensity": np.array([]), "rate": np.array([]), "hype_frac": np.array([])}
    hype = [h.lower() for h in hype_tokens]
    tmax = max(r[0] for r in rows)
    nb = int(tmax // bin_s) + 1
    rate = np.zeros(nb)
    hyp = np.zeros(nb)
    for t, m in rows:
        b = int(t // bin_s)
        if b < 0 or b >= nb:
            continue
        rate[b] += 1
        ml = (m or "").lower()
        if any(h in ml for h in hype):
            hyp[b] += 1
    base = _rolling_median(rate, max(1, baseline_s // bin_s))
    base[base < 1] = 1
    spike = rate / base
    hype_frac = np.where(rate > 0, hyp / np.maximum(rate, 1), 0)
    return {"bins": np.arange(nb) * bin_s, "intensity": spike * (1 + 2 * hype_frac), "rate": rate,
            "spike": spike, "hype_frac": hype_frac}


def audio_signal(pcm: np.ndarray, sample_rate: int, bin_s: int = 10, baseline_s: int = 600) -> dict:
    """pcm: mono samples. Returns dB above rolling median per bin."""
    n = sample_rate * bin_s
    nb = len(pcm) // n
    if nb == 0:
        return {"bins": np.array([]), "db": np.array([])}
    x = pcm[: nb * n].astype(np.float32).reshape(nb, n)
    rms = np.sqrt(np.mean(x ** 2, axis=1)) + 1e-6
    db = 20 * np.log10(rms)
    base = _rolling_median(db, max(1, baseline_s // bin_s))
    return {"bins": np.arange(nb) * bin_s, "db": db - base}


def heatmap_signal(heatmap: list[dict], duration: float, bin_s: int = 10) -> dict:
    """YouTube 'most replayed' data as yt-dlp exposes it: [{start_time, end_time, value}], value in 0..1.
    Intensity = value relative to the median value across the video."""
    if not heatmap or not duration:
        return {"bins": np.array([]), "intensity": np.array([])}
    nb = int(duration // bin_s) + 1
    vals = np.zeros(nb)
    for h in heatmap:
        try:
            s, e, v = float(h["start_time"]), float(h["end_time"]), float(h["value"])
        except (KeyError, TypeError, ValueError):
            continue
        for b in range(int(s // bin_s), min(nb, int(e // bin_s) + 1)):
            vals[b] = max(vals[b], v)
    med = float(np.median(vals[vals > 0])) if np.any(vals > 0) else 1.0
    return {"bins": np.arange(nb) * bin_s, "intensity": vals / max(med, 1e-6), "value": vals}


def transcript_signal(cues: list[list], bin_s: int = 10, baseline_s: int = 600) -> dict:
    """cues: [[t, text], ...] from auto subtitles. Intensity from cue-word density, laughter and speech rate."""
    if not cues:
        return {"bins": np.array([]), "intensity": np.array([])}
    tmax = max(c[0] for c in cues)
    nb = int(tmax // bin_s) + 1
    words = np.zeros(nb)
    cue_hits = np.zeros(nb)
    for t, text in cues:
        b = int(t // bin_s)
        if b < 0 or b >= nb:
            continue
        tl = (text or "").lower()
        words[b] += len(tl.split())
        cue_hits[b] += sum(tl.count(c) for c in CUE_WORDS) + (2 if LAUGH.search(tl) else 0) + tl.count("!")
    base = _rolling_median(words, max(1, baseline_s // bin_s))
    base[base < 1] = 1
    rate = words / base
    intensity = rate * (1 + cue_hits)
    return {"bins": np.arange(nb) * bin_s, "intensity": intensity, "cue_hits": cue_hits}


def _pad(a: np.ndarray, n: int) -> np.ndarray:
    if len(a) >= n:
        return a[:n]
    return np.concatenate([a, np.zeros(n - len(a))])


def combine(signals: dict, settings: dict, bin_s: int = 10) -> np.ndarray:
    """signals: {'chat': {...}, 'audio': {...}, 'heatmap': {...}, 'transcript': {...}} (any subset).
    Returns a combined score per bin, normalised so ~1.0 is 'notable' and >2 is strong."""
    parts = []
    if "chat" in signals and len(signals["chat"]["intensity"]):
        parts.append(("chat", signals["chat"]["intensity"] / max(settings.get("chat_spike_factor", 3.0), 1e-6), 1.0))
    if "audio" in signals and len(signals["audio"]["db"]):
        a = np.clip(signals["audio"]["db"], 0, None) / max(settings.get("audio_spike_db", 8.0), 1e-6)
        off = int(signals["audio"].get("offset_bins", 0))
        if off:
            a = np.concatenate([np.zeros(off), a])
        parts.append(("audio", a, 0.6))
    if "heatmap" in signals and len(signals["heatmap"]["intensity"]):
        parts.append(("heatmap", np.clip(signals["heatmap"]["intensity"] - 1.0, 0, None), 1.0))
    if "transcript" in signals and len(signals["transcript"]["intensity"]):
        parts.append(("transcript", np.clip(signals["transcript"]["intensity"] / 3.0, 0, None), 0.5))
    if not parts:
        return np.array([])
    nb = max(len(p[1]) for p in parts)
    score = np.zeros(nb)
    for _name, arr, w in parts:
        score += w * _pad(np.asarray(arr, dtype=float), nb)
    return score


def pick_moments(score: np.ndarray, signals: dict, settings: dict, bin_s: int = 10,
                 t_min: float = 0.0, t_max: float | None = None) -> list[dict]:
    """Greedy non-overlapping selection of the highest-scoring bins inside [t_min, t_max], with reasons per moment.
    The stream-start flood (skip_start) is only ignored when the window starts at the beginning."""
    if len(score) == 0:
        return []
    score = score.copy()
    if t_min > 0:
        score[: int(t_min // bin_s)] = 0
    if t_max is not None:
        score[int(t_max // bin_s) + 1:] = 0
    n_signals = sum(1 for k in ("chat", "audio", "heatmap", "transcript")
                    if k in signals and len(signals[k].get("intensity", signals[k].get("db", []))))
    cutoff = 1.5 if n_signals >= 2 else 1.0
    if t_min <= 0:
        score[: int(settings.get("skip_start", 120) // bin_s)] = 0
    min_gap = settings.get("min_gap", 90)
    max_moments = settings.get("max_moments", 12)
    chosen: list[dict] = []
    for i in np.argsort(score)[::-1]:
        if score[i] < cutoff or len(chosen) >= max_moments:
            break
        t = float(i * bin_s)
        if all(abs(t - c["t"]) >= min_gap for c in chosen):
            chosen.append({"t": t, "score": round(float(score[i]), 2), "reasons": explain(int(i), signals)})
    chosen.sort(key=lambda c: -c["score"])
    for rank, c in enumerate(chosen, 1):
        c["rank"] = rank
    return chosen


def explain(i: int, signals: dict) -> list[str]:
    out = []
    ch = signals.get("chat")
    if ch is not None and i < len(ch.get("intensity", [])):
        sp = float(ch["spike"][i])
        hf = float(ch["hype_frac"][i])
        if sp >= 1.5 or hf >= 0.2:
            s = f"Chat {sp:.1f}× its usual rate ({int(ch['rate'][i])} msgs/10s)"
            if hf >= 0.1:
                s += f", {int(hf * 100)}% hype words"
            out.append(s)
    au = signals.get("audio")
    if au is not None:
        j = i - int(au.get("offset_bins", 0))
        if 0 <= j < len(au.get("db", [])) and float(au["db"][j]) >= 4:
            out.append(f"Audio +{float(au['db'][j]):.0f} dB louder than usual")
    hm = signals.get("heatmap")
    if hm is not None and i < len(hm.get("intensity", [])) and float(hm["intensity"][i]) >= 1.5:
        out.append(f"YouTube 'most replayed' {float(hm['intensity'][i]):.1f}× the video's median")
    tr = signals.get("transcript")
    if tr is not None and i < len(tr.get("intensity", [])) and float(tr["intensity"][i]) >= 3:
        out.append(f"Excited speech: {int(tr['cue_hits'][i])} cue words/laughs in the transcript")
    return out or ["Weak signal (kept to fill the list)"]


def top_chat_words(rows: list[list], t: float, span: int = 30, n: int = 5) -> list[str]:
    c: Counter = Counter()
    for rt, m in rows:
        if t - 5 <= rt <= t + span:
            for w in (m or "").split():
                if len(w) > 2 and not w.startswith("@") and not w.startswith("http"):
                    c[w.lower()] += 1
    return [w for w, _ in c.most_common(n)]
