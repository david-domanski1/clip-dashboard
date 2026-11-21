"""Learns your taste: feature parsing, the numpy logistic model, the score multiplier and reason, the ≥ 8 guard,
the moments.scored handler and the /api/taste endpoints."""
import numpy as np
import pytest

from clipdash.features import taste

LOUD = ["Chat 7.5× its usual rate (31 msgs/10s), 48% hype words", "Audio +9 dB louder than usual",
        "YouTube 'most replayed' 2.1× the video's median", "Excited speech: 5 cue words/laughs in the transcript",
        "Speech in 25% of the next 40 s"]
QUIET = ["Weak signal (kept to fill the list)", "Low speech: only 4% of the next 40 s has talking (scored down)"]


def test_featurize_reads_the_numbers_out_of_reasons():
    f = taste.featurize({"t": 7300, "reasons": LOUD, "flags": [], "speech": None})
    assert f["chat_spike"] == 7.5 and f["hype_frac"] == pytest.approx(0.48) and f["audio_db"] == 9
    assert f["heatmap_x"] == 2.1 and f["cue_hits"] == 5 and f["speech_frac"] == pytest.approx(0.25)
    assert f["chat_spam"] == 0 and f["low_speech"] == 0 and f["hour_of_stream"] == 2 and f["label_bucket"] == 3
    q = taste.featurize({"t": 100, "reasons": QUIET, "flags": ["low_speech", "chat_spam"], "speech": 0.04})
    assert q["chat_spike"] == 0 and q["speech_frac"] == pytest.approx(0.04) and q["low_speech"] == 1 and q["chat_spam"] == 1
    assert q["hour_of_stream"] == 0 and q["label_bucket"] == 0
    assert taste.featurize({"reasons": ["Chat 4.0× its usual rate (12 msgs/10s), 20% hype words"]})["label_bucket"] == 2
    assert taste.featurize({"reasons": ["Audio +5 dB louder than usual"]})["label_bucket"] == 1
    assert set(taste.featurize({})) == set(taste.FEATURES)


def test_fit_logistic_learns_the_right_sign_on_a_separable_set():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 3))
    y = np.where(X[:, 0] > 0, 1, -1)          # only the first feature matters
    w, b = taste.fit_logistic(X, y)
    assert w[0] > 1.0 and abs(w[1]) < w[0] / 3 and abs(w[2]) < w[0] / 3
    assert np.isfinite(w).all() and np.isfinite(b)
    # sample weights pull the fit toward the heavier examples
    w2, _ = taste.fit_logistic(X, y, sample_weight=np.where(y > 0, 5.0, 1.0))
    assert w2[0] > 0


def test_multiplier_clamp_and_reason_text():
    assert taste.multiplier(0.5) == 1.0
    assert taste.multiplier(1.0) == 1.5 and taste.multiplier(0.99) < 1.5
    assert taste.multiplier(0.0) == 0.6 and taste.multiplier(0.05) == 0.6
    assert taste.multiplier(0.68) == pytest.approx(1.18)
    assert taste.reason_text(7, 9, "lacy", 1.18) == "Your taste: moments like this were approved 7 of 9 times for lacy (+18%)"
    assert taste.reason_text(1, 6, "lacy", 0.78) == "Your taste: moments like this were approved 1 of 6 times for lacy (−22%)"
    assert taste.reason_text(3, 6, taste.GLOBAL, 1.0).endswith("across your creators (no change)")


# ---- helpers building labelled history ----

def _video(db, campaign, channel, n=[0]):
    n[0] += 1
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": f"v{n[0]}", "url": "u",
                               "channel": channel, "status": "scanned"})
    return db.get("videos", vid)


def _decide(db, campaign, video, reasons, status, t=1000, flags=None, speech=None, views=None):
    mid = db.insert("moments", {"video_id": video["id"], "t": t, "score": 2.0, "reasons": reasons, "flags": flags or [],
                                "speech": speech, "status": "rendered"})
    cid = db.insert("clips", {"video_id": video["id"], "campaign_id": campaign["id"], "moment_id": mid, "status": status,
                              "source_start": t, "source_length": 90})
    if views is not None:
        aid = db.insert("accounts", {"platform": "youtube", "label": "yt", "route": "assisted"})
        db.insert("posts", {"clip_id": cid, "account_id": aid, "campaign_id": campaign["id"], "platform": "youtube",
                            "route": "assisted", "status": "live", "views": views})
    return cid


def _history(db, campaign, channel="lacy", loud_approved=6, quiet_rejected=6):
    v = _video(db, campaign, channel)
    for i in range(loud_approved):
        _decide(db, campaign, v, LOUD, "posted" if i % 2 else "approved", t=1000 + i, speech=0.4, views=5000 if i % 2 else None)
    for i in range(quiet_rejected):
        _decide(db, campaign, v, QUIET, "rejected", t=5000 + i, flags=["low_speech"], speech=0.04)
    return v


def test_labelled_examples_labels_and_view_weights(fresh_db, campaign):
    v = _history(fresh_db, campaign, loud_approved=2, quiet_rejected=1)
    _decide(fresh_db, campaign, v, LOUD, "pending")                      # undecided: skipped
    ex = taste.labelled_examples("lacy")
    assert sorted(e["label"] for e in ex) == [-1, 1, 1]
    weights = sorted(e["weight"] for e in ex)
    assert weights[0] == 1.0 and weights[1] == 1.0 and weights[2] == pytest.approx(1 + np.log1p(5000))
    assert taste.labelled_examples("someone_else") == [] and len(taste.labelled_examples(taste.GLOBAL)) == 3


def test_train_needs_eight_examples(fresh_db, campaign):
    _history(fresh_db, campaign, loud_approved=4, quiet_rejected=3)
    assert taste.train("lacy") is None and taste.model_for("lacy") is None
    assert fresh_db.execute("SELECT COUNT(*) c FROM taste_models").fetchone()["c"] == 0
    v = fresh_db.one("videos", "channel='lacy'")
    _decide(fresh_db, campaign, v, QUIET, "rejected", flags=["low_speech"], speech=0.02)
    m = taste.train("lacy")
    assert m and m["n"] == 8 and m["scope"] == "lacy"
    assert m["weights"]["chat_spike"] > 0 and m["weights"]["low_speech"] < 0 and m["weights"]["speech_frac"] > 0
    likes, dislikes = taste.describe(m)
    assert likes and dislikes and "quiet moments" in dislikes


def test_handler_is_a_no_op_without_data(fresh_db):
    moments = [{"t": 10, "score": 2.5, "reasons": list(LOUD), "flags": [], "speech": 0.3}]
    taste.on_moments_scored(video={"channel": "lacy"}, moments=moments, settings={"taste_enabled": True})
    assert moments[0]["score"] == 2.5 and moments[0]["reasons"] == LOUD
    taste.on_moments_scored(video=None, moments=[], settings={})          # nothing to do, nothing raised
    taste.on_moments_scored(video={"channel": "lacy"}, moments=[{"t": 0}], settings={"taste_enabled": True})


def test_handler_adjusts_in_place_with_reasons(fresh_db, campaign):
    _history(fresh_db, campaign)
    loud = {"t": 20, "score": 2.0, "reasons": list(LOUD), "flags": [], "speech": 0.4}
    quiet = {"t": 30, "score": 2.0, "reasons": list(QUIET), "flags": ["low_speech"], "speech": 0.03}
    moments = [quiet, loud]
    taste.on_moments_scored(video={"channel": "LACY"}, moments=moments, settings={"taste_enabled": True})
    assert moments[0] is quiet and moments[1] is loud                      # same list, same dicts
    assert loud["score"] > 2.0 and quiet["score"] < 2.0
    assert 0.6 <= quiet["taste"] < 1.0 < loud["taste"] <= 1.5
    assert loud["reasons"][-1].startswith("Your taste: moments like this were approved 6 of 6 times for lacy (+")
    assert quiet["reasons"][-1].startswith("Your taste: moments like this were approved 0 of 6 times for lacy (−")
    assert fresh_db.execute("SELECT scope FROM taste_models").fetchone()["scope"] == "lacy"   # trained lazily


def test_handler_falls_back_to_global_and_respects_the_setting(fresh_db, campaign):
    _history(fresh_db, campaign, channel="caseoh")
    m = {"t": 20, "score": 2.0, "reasons": list(LOUD), "flags": [], "speech": 0.4}
    taste.on_moments_scored(video={"channel": "lacy"}, moments=[m], settings={"taste_enabled": False})
    assert m["score"] == 2.0 and len(m["reasons"]) == len(LOUD)
    taste.on_moments_scored(video={"channel": "lacy"}, moments=[m], settings={"taste_enabled": True})
    assert m["score"] > 2.0 and "across your creators" in m["reasons"][-1]


def test_stale_model_is_retrained(fresh_db, campaign, monkeypatch):
    _history(fresh_db, campaign)
    assert taste.model_for("lacy")["trained_at"]
    fresh_db.execute("UPDATE taste_models SET trained_at='2020-01-01T00:00:00+00:00', n=99")
    calls = []
    real = taste.train
    monkeypatch.setattr(taste, "train", lambda scope: calls.append(scope) or real(scope))
    assert taste.model_for("lacy")["n"] == 12 and calls == ["lacy"]
    assert taste.model_for("lacy")["n"] == 12 and calls == ["lacy"]      # fresh now: no second fit


def test_register_subscribes_once(monkeypatch):
    from clipdash import events
    seen = []
    monkeypatch.setattr(events, "on", lambda name, fn: seen.append((name, fn)))
    monkeypatch.setattr(taste, "_registered", False)
    taste.register()
    taste.register()
    assert seen == [("moments.scored", taste.on_moments_scored)]


def test_endpoints(fresh_db, campaign):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    _history(fresh_db, campaign)
    _history(fresh_db, campaign, channel="caseoh", loud_approved=2, quiet_rejected=1)
    with TestClient(app) as c:
        st = c.get("/api/taste/status").json()
        assert st["enabled"] is True and st["min_examples"] == 8
        by = {s["scope"]: s for s in st["scopes"]}
        assert set(by) == {"lacy", "caseoh", "global"} and st["scopes"][-1]["scope"] == "global"
        assert by["lacy"]["n"] == 12 and by["lacy"]["approved"] == 6 and by["lacy"]["rejected"] == 6 and not by["lacy"]["trained"]
        assert "8" in by["caseoh"]["sentence"] and by["caseoh"]["n"] == 3
        r = c.post("/api/taste/retrain").json()
        assert sorted(r["trained"]) == ["global", "lacy"]
        lacy = {s["scope"]: s for s in r["scopes"]}["lacy"]
        assert lacy["trained"] and lacy["trained_at"] and lacy["sentence"].startswith("Learned from 12 of your decisions on lacy: you approve")
        assert "you reject" in lacy["sentence"] and lacy["likes"] and lacy["dislikes"]
        assert c.put("/api/settings", json={"taste_enabled": False}).json()["taste_enabled"] is False
        r = c.post("/api/taste/reset").json()
        assert r["enabled"] is False and not any(s["trained"] for s in r["scopes"])
        assert fresh_db.execute("SELECT COUNT(*) c FROM taste_models").fetchone()["c"] == 0
        assert "/static/features/taste.js" in c.get("/api/features").json()
