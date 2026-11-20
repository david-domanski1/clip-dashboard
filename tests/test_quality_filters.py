from clipdash import signals


def test_chat_spam_detects_repeated_message():
    rows = [[i, m] for i, m in enumerate(["wake up", "WAKE UP", "wake up lacy", "wake", "lol", "wake up", "wakey wakey", "wake up", "WAKE UPPP", "wake up"])]
    frac, what = signals.chat_spam(rows, 5)
    assert frac >= 0.5 and what.startswith("wake")


def test_chat_spam_ignores_varied_chat():
    rows = [[i, ["no way", "he got him", "LMAO", "what was that", "bro", "cops behind", "he died", "run", "W", "L"][i % 10] + str(i)] for i in range(30)]
    frac, _ = signals.chat_spam(rows, 15)
    assert frac < 0.3


def test_spam_moments_are_scored_down_and_flagged(fresh_db, campaign, monkeypatch):
    from clipdash import db, pipeline, sources, thumbs
    monkeypatch.setattr(sources, "video_info", lambda url: {"title": "v", "channel": "lacy", "published_at": "2026-11-19T00:00:00+00:00",
                                                             "duration": 2000.0, "is_stream": 1, "live_now": False, "heatmap": [], "has_auto_subs": False, "has_live_chat": False})
    monkeypatch.setattr(thumbs, "moment_thumbnail", lambda *a, **k: None)
    rows = [[float(t), "hi"] for t in range(0, 2000, 4)]
    rows += [[600 + i * 0.1, "WAKE UP"] for i in range(80)]            # spam spike
    rows += [[1500 + i * 0.1, ["LMAO", "no way", "he died", "CLIP IT", "what"][i % 5]] for i in range(80)]  # real spike
    import json
    monkeypatch.setattr("clipdash.chat.download_twitch_chat", lambda *a, **k: (a[1].write_text(json.dumps(sorted(rows))), True)[1])
    db.set_setting("auto_render_top", 1)
    v = pipeline.add_video(campaign["id"], "https://www.twitch.tv/videos/123456789", queue=False)
    pipeline.scan_video(v["id"], lambda f, m=None: None, window="whole")
    ms = db.rows("moments", "video_id=?", (v["id"],), order="rank")
    spam = [m for m in ms if "chat_spam" in m["flags"]]
    assert spam and all(abs(m["t"] - 600) < 60 for m in spam)
    assert ms[0]["t"] >= 1400 and not ms[0]["flags"]
    assert any(r.startswith("Chat spam") for r in spam[0]["reasons"])
    clips = db.rows("clips", "video_id=?", (v["id"],))
    assert len(clips) == 1 and clips[0]["moment_id"] == ms[0]["id"]


def test_render_next_best_prefers_unflagged(fresh_db, campaign, monkeypatch):
    from clipdash import db, pipeline
    monkeypatch.setattr("clipdash.jobs.enqueue", lambda *a, **k: 1)
    vid = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "1", "url": "u", "duration": 5000, "status": "done"})
    a = db.insert("moments", {"video_id": vid, "t": 100, "score": 9, "flags": ["chat_spam"], "rank": 1})
    b = db.insert("moments", {"video_id": vid, "t": 900, "score": 3, "flags": [], "rank": 2})
    cid = pipeline.render_next_best(vid)
    assert db.get("clips", cid)["moment_id"] == b
    cid2 = pipeline.render_next_best(vid)
    assert db.get("clips", cid2)["moment_id"] == a
    assert pipeline.render_next_best(vid) is None


def test_speech_fraction_silence_is_zero():
    import numpy as np
    from clipdash import speech
    assert speech.speech_fraction(np.zeros(16000 * 20, dtype=np.float32)) == 0.0


def test_low_speech_moments_are_flagged_and_not_auto_rendered(fresh_db, campaign, monkeypatch):
    import json
    from clipdash import db, pipeline, sources, speech, thumbs
    monkeypatch.setattr(sources, "video_info", lambda url: {"title": "v", "channel": "lacy", "published_at": "2026-11-19T00:00:00+00:00",
                                                             "duration": 2000.0, "is_stream": 1, "live_now": False, "heatmap": [], "has_auto_subs": False, "has_live_chat": False})
    monkeypatch.setattr(thumbs, "moment_thumbnail", lambda *a, **k: None)
    monkeypatch.setattr(speech, "moment_speech", lambda video, t, length=40.0: 0.02 if t < 1000 else 0.6)
    rows = [[float(t), "hi"] for t in range(0, 2000, 4)]
    rows += [[600 + i * 0.1, ["yay", "prance", "prayer", "finally", "lacy"][i % 5]] for i in range(80)]   # busy chat, streamer asleep
    rows += [[1500 + i * 0.1, ["LMAO", "no way", "he died", "CLIP IT", "what"][i % 5]] for i in range(60)]
    monkeypatch.setattr("clipdash.chat.download_twitch_chat", lambda *a, **k: (a[1].write_text(json.dumps(sorted(rows))), True)[1])
    db.set_setting("auto_render_top", 1)   # off by default; this test checks the flagged moment is skipped when it is on
    v = pipeline.add_video(campaign["id"], "https://www.twitch.tv/videos/123456789", queue=False)
    pipeline.scan_video(v["id"], lambda f, m=None: None, window="whole")
    ms = db.rows("moments", "video_id=?", (v["id"],), order="rank")
    asleep = [m for m in ms if abs(m["t"] - 600) < 60]
    assert asleep and "low_speech" in asleep[0]["flags"] and asleep[0]["speech"] == 0.02
    assert ms[0]["t"] >= 1400
    clips = db.rows("clips", "video_id=?", (v["id"],))
    assert len(clips) == 1 and clips[0]["moment_id"] == ms[0]["id"]
