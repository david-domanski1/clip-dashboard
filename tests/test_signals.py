import numpy as np

from clipdash import signals


def chat(n_quiet=600, spike_at=300, spike_n=120, hype=False):
    rows = [[float(t), "hello"] for t in np.linspace(0, n_quiet, n_quiet)]
    rows += [[spike_at + i * 0.05, "KEKW CLIP IT" if hype else "wow"] for i in range(spike_n)]
    return sorted(rows)


def test_chat_spike_is_found_and_explained():
    sig = {"chat": signals.chat_signal(chat(hype=True), ["KEKW", "CLIP"])}
    score = signals.combine(sig, {"chat_spike_factor": 3.0})
    moments = signals.pick_moments(score, sig, {"skip_start": 120, "min_gap": 90, "max_moments": 5})
    assert moments and abs(moments[0]["t"] - 300) <= 10
    assert any("Chat" in r and "hype" in r for r in moments[0]["reasons"])


def test_hype_words_outrank_plain_spike():
    plain = signals.chat_signal(chat(spike_at=300), ["KEKW"])
    hyped = signals.chat_signal(chat(spike_at=300, hype=True), ["KEKW"])
    assert hyped["intensity"][30] > plain["intensity"][30]


def test_stream_start_is_ignored():
    rows = [[t * 0.02, "KEKW"] for t in range(3000)] + [[float(t), "hi"] for t in range(60, 900)]
    sig = {"chat": signals.chat_signal(rows, ["KEKW"])}
    score = signals.combine(sig, {})
    moments = signals.pick_moments(score, sig, {"skip_start": 120, "min_gap": 90, "max_moments": 5})
    assert all(m["t"] >= 120 for m in moments)


def test_min_gap_between_moments():
    rows = []
    for t0 in (300, 330, 360, 700):
        rows += [[t0 + i * 0.05, "LMAO"] for i in range(100)]
    rows += [[float(t), "x"] for t in range(0, 1000, 2)]
    sig = {"chat": signals.chat_signal(sorted(rows), ["LMAO"])}
    score = signals.combine(sig, {})
    moments = signals.pick_moments(score, sig, {"skip_start": 0, "min_gap": 90, "max_moments": 10})
    ts = sorted(m["t"] for m in moments)
    assert all(b - a >= 90 for a, b in zip(ts, ts[1:]))


def test_audio_signal_db_above_baseline():
    sr = 8000
    pcm = np.concatenate([np.random.randn(sr * 10 * 20) * 100, np.random.randn(sr * 10) * 2000, np.random.randn(sr * 10 * 5) * 100])
    sig = signals.audio_signal(pcm.astype(np.float32), sr)
    assert sig["db"][20] > 15


def test_heatmap_signal_scales_by_median():
    hm = [{"start_time": i * 30, "end_time": (i + 1) * 30, "value": 0.1} for i in range(20)]
    hm[5]["value"] = 0.9
    sig = signals.heatmap_signal(hm, 600)
    assert sig["intensity"][15] > 5 and abs(sig["intensity"][0] - 1) < 1e-6


def test_combine_handles_mismatched_lengths():
    sig = {"chat": signals.chat_signal([[1, "a"], [500, "b"]], []), "audio": signals.audio_signal(np.zeros(8000 * 10 * 3, dtype=np.float32), 8000)}
    score = signals.combine(sig, {})
    assert len(score) == max(len(sig["chat"]["intensity"]), len(sig["audio"]["db"]))


def test_no_signals_gives_no_moments():
    assert signals.pick_moments(signals.combine({}, {}), {}, {}) == []
