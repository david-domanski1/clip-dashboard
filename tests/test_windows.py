import numpy as np

from clipdash import pipeline, signals


def test_parse_and_resolve_windows():
    assert pipeline.resolve_window(pipeline.parse_window("auto"), 29 * 3600) == (27 * 3600, 29 * 3600)
    assert pipeline.resolve_window(pipeline.parse_window("auto"), 3 * 3600) == (0, 3 * 3600)
    assert pipeline.resolve_window(pipeline.parse_window("last6h"), 29 * 3600) == (23 * 3600, 29 * 3600)
    assert pipeline.resolve_window(pipeline.parse_window("whole"), 500) == (0, 500)
    assert pipeline.resolve_window(pipeline.parse_window({"start": 100, "end": 999999}), 5000) == (100, 5000)
    assert pipeline.resolve_window(pipeline.parse_window("last2h"), 3600) == (0, 3600)


def test_merge_ranges_and_unscanned_chunks():
    r = pipeline.merge_ranges([], (27 * 3600, 29 * 3600))
    r = pipeline.merge_ranges(r, (25 * 3600, 27 * 3600))
    assert r == [[25 * 3600, 29 * 3600]]
    chunks = pipeline.unscanned_chunks(r, 29 * 3600)
    assert chunks[0] == (23 * 3600, 25 * 3600) and chunks[-1] == (0, 3600) and len(chunks) == 13
    assert pipeline.unscanned_chunks([[0, 29 * 3600]], 29 * 3600) == []


def test_outside_campaign_window(campaign):
    assert "published 2026-11-11" in pipeline.outside_campaign_window(campaign, "2026-11-11T10:00:00+00:00")
    assert pipeline.outside_campaign_window(campaign, "2026-11-18T10:11:23+00:00") is None
    assert pipeline.outside_campaign_window(campaign, None) is None
    campaign["source_end"] = "2026-11-20"
    assert "ends" in pipeline.outside_campaign_window(campaign, "2026-11-25T00:00:00+00:00")


def test_pick_moments_respects_window_and_keeps_stream_start():
    rows = [[float(t), "KEKW"] for t in np.linspace(0, 60, 400)] + [[float(t), "hi"] for t in range(60, 2000, 2)]
    rows += [[1500 + i * 0.05, "LMAO"] for i in range(150)]
    rows.sort()
    sig = {"chat": signals.chat_signal(rows, ["KEKW", "LMAO"])}
    score = signals.combine(sig, {})
    inside = signals.pick_moments(score, sig, {"skip_start": 120, "min_gap": 90, "max_moments": 5}, t_min=1200, t_max=1800)
    assert inside and all(1200 <= m["t"] <= 1800 for m in inside)
    whole = signals.pick_moments(score, sig, {"skip_start": 120, "min_gap": 90, "max_moments": 5})
    assert all(m["t"] >= 120 for m in whole)


def test_audio_offset_bins_align_with_video_time():
    pcm = np.concatenate([np.random.randn(8000 * 10 * 5) * 50, np.random.randn(8000 * 10) * 3000, np.random.randn(8000 * 10 * 3) * 50]).astype(np.float32)
    a = signals.audio_signal(pcm, 8000)
    a["offset_bins"] = 100
    score = signals.combine({"audio": a}, {})
    assert int(np.argmax(score)) == 105


def test_add_video_gate_and_skip_then_override(fresh_db, campaign, monkeypatch):
    from clipdash import db, jobs, sources
    monkeypatch.setattr(sources, "video_info", lambda url: {"title": "old vod", "channel": "lacy", "published_at": "2026-11-11T00:00:00+00:00",
                                                             "duration": 1000.0, "is_stream": 1, "live_now": False, "heatmap": [], "has_auto_subs": False, "has_live_chat": False})
    jobs.register_handlers()
    v = pipeline.add_video(campaign["id"], "https://www.twitch.tv/videos/123456789", queue=False)
    pipeline.scan_video(v["id"], lambda f, m=None: None)
    v = db.get("videos", v["id"])
    assert v["status"] == "skipped" and "Outside campaign window" in v["stage"]
    db.update("videos", v["id"], {"override": 1})
    monkeypatch.setattr("clipdash.chat.download_twitch_chat", lambda *a, **k: (a[1].write_text("[[10, \"hi\"], [500, \"KEKW\"]]"), True)[1])
    pipeline.scan_video(v["id"], lambda f, m=None: None, window="whole")
    v = db.get("videos", v["id"])
    assert v["status"] in ("scanned", "done", "rendering") and v["scanned_ranges"] == [[0.0, 1000.0]]


def test_skipped_videos_hidden_from_pipeline_and_revealed_by_toggle(fresh_db, campaign, monkeypatch):
    from fastapi.testclient import TestClient
    from clipdash import db
    from clipdash.app import app
    ok = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "1", "url": "u1", "status": "done",
                              "published_at": "2026-11-19T00:00:00+00:00"})
    old = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "2", "url": "u2", "status": "skipped",
                               "stage": "Outside campaign window: published 2026-11-11, campaign footage starts 2026-11-18",
                               "published_at": "2026-11-11T00:00:00+00:00"})
    legacy = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "3", "url": "u3", "status": "error",
                                  "stage": "Outside campaign window: published 2026-11-15, campaign footage starts 2026-11-18"})
    db.init()  # upgrade: legacy outside-window rows become skipped
    with TestClient(app) as c:
        listed = [v["id"] for v in c.get(f"/api/videos?campaign_id={campaign['id']}").json()]
        assert listed == [ok]
        skipped = c.get(f"/api/videos/skipped?campaign_id={campaign['id']}").json()
        assert {v["id"] for v in skipped} == {old, legacy}
        assert all(v["status"] == "skipped" for v in skipped)
        monkeypatch.setattr("clipdash.jobs.enqueue", lambda *a, **k: 1)
        r = c.post(f"/api/videos/{old}/rescan", json={"window": "last2h", "override": True})
        assert r.status_code == 200 and db.get("videos", old)["status"] == "queued" and db.get("videos", old)["override"] == 1
        assert old in [v["id"] for v in c.get(f"/api/videos?campaign_id={campaign['id']}").json()]
