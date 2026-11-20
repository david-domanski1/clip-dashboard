"""Section picker: section bounds, clip-length math, new videos from watched channels, chat density for the heat strip."""
import json

import pytest

from clipdash import pipeline


def test_section_presets_and_explicit_bounds():
    D = 29 * 3600
    assert pipeline.section_bounds(D, "last30m") == (D - 1800, D)
    assert pipeline.section_bounds(D, "last1h") == (D - 3600, D)
    assert pipeline.section_bounds(D, "last2h") == (D - 7200, D)
    assert pipeline.section_bounds(D, "whole") == (0, D)
    assert pipeline.section_bounds(D) == (0, D)
    assert pipeline.section_bounds(D, start=100, end=999) == (100, 999)
    assert pipeline.section_bounds(D, start=-50, end=10 ** 9) == (0, D)         # clamped into the video
    assert pipeline.section_bounds(D, start=5000, end=4000) == (4000, 5000)    # swapped if reversed
    assert pipeline.section_bounds(1000, "last2h") == (0, 1000)                # shorter than the preset
    assert pipeline.section_bounds(D, start=1000, end=1010) == (1000, 1060)    # widened to a minute
    assert pipeline.section_bounds(D, start=D - 10, end=D) == (D - 60, D)
    with pytest.raises(ValueError):
        pipeline.section_bounds(0, "whole")
    with pytest.raises(ValueError):
        pipeline.section_bounds(D, "last9h")


def test_clip_bounds_centre_the_moment_with_lead_in():
    # 90 s clip, 25 s lead-in: starts 25 s before the moment, downloads 2 s of padding, trims to exactly 90 s
    assert pipeline.clip_bounds(1000, 90, 25, 10000) == (975, 92, 90)
    assert pipeline.clip_bounds(1000, 61, 0, 10000) == (1000, 63, 61)
    assert pipeline.clip_bounds(10, 120, 25, 10000) == (0, 122, 120)           # near the start
    assert pipeline.clip_bounds(9990, 120, 25, 10000) == (9878, 122, 120)      # near the end: pulled back inside
    assert pipeline.clip_bounds(50, 120, 25, 100) == (0, 100, 100)             # video shorter than the clip
    assert pipeline.clip_bounds(1000, 90, 500, None) == (910, 92, 90)          # lead-in capped at the clip length
    assert pipeline.clip_bounds(1000, 3, 0, None)[2] == 10                     # silly lengths are floored


def test_parse_window_keeps_clip_parameters_and_minute_presets():
    w = pipeline.parse_window({"start": 10, "end": 500, "clip_len": "120", "lead_in": 30})
    assert w == {"kind": "range", "start": 10.0, "end": 500.0, "clip_len": 120.0, "lead_in": 30.0}
    assert pipeline.parse_window("last30m") == {"kind": "last", "hours": 0.5}
    assert pipeline.resolve_window(pipeline.parse_window("last30m"), 7200) == (5400, 7200)


def test_render_params_prefer_explicit_then_section_then_settings(fresh_db, campaign):
    v = {"scan_window": {"kind": "range", "start": 0, "end": 10, "clip_len": 120, "lead_in": 40}}
    assert pipeline.render_params(v, 61, 5) == (61, 5)
    assert pipeline.render_params(v) == (120, 40)
    assert pipeline.render_params({"scan_window": {"kind": "auto"}}) == (90, 25)


def test_found_videos_are_listed_as_new_not_scanned(fresh_db, campaign, monkeypatch):
    from clipdash import sources
    sid = fresh_db.insert("sources", {"campaign_id": campaign["id"], "kind": "twitch_channel", "url": "https://twitch.tv/lacy", "external_id": "lacy", "name": "LACY", "watch": 1})
    fresh_db.update("campaigns", campaign["id"], {"active": 1})
    monkeypatch.setattr(sources, "list_recent", lambda s, limit=10: [
        {"platform": "twitch", "external_id": "1000000001", "url": "https://www.twitch.tv/videos/1000000001", "title": "GTA 6 marathon", "channel": "lacy",
         "published_at": "2026-11-19T10:00:00+00:00", "duration": 7200, "is_stream": 1, "live_now": True},
        {"platform": "twitch", "external_id": "1000000000", "url": "https://www.twitch.tv/videos/1000000000", "title": "GTA 6 old", "channel": "lacy",
         "published_at": "2026-11-10T10:00:00+00:00", "duration": 7200, "is_stream": 1, "live_now": False}])
    fresh_db.set_setting("gta_only", False)
    pipeline.check_sources(lambda *a: None, campaign["id"])
    rows = {r["external_id"]: r for r in fresh_db.rows("videos")}
    assert rows["1000000001"]["status"] == "new" and rows["1000000001"]["live"] == 1 and "pick a section" in rows["1000000001"]["stage"]
    assert rows["1000000000"]["status"] == "skipped"
    assert fresh_db.execute("SELECT COUNT(*) c FROM jobs WHERE type='scan_video'").fetchone()["c"] == 0
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        t = c.get(f"/api/today?campaign_id={campaign['id']}").json()
        assert [n["title"] for n in t["new_videos"]] == ["GTA 6 marathon"] and t["new_videos"][0]["live"] is True
        sec = c.get(f"/api/videos/{rows['1000000001']['id']}/section").json()
        assert sec["duration"] == 7200 and sec["clip_len"] == 90 and sec["lead_in"] == 25 and sec["density"]["complete"] is False
        # a section search stores the clip parameters with the window
        monkeypatch.setattr("clipdash.jobs.enqueue", lambda *a, **k: 1)
        r = c.post(f"/api/videos/{rows['1000000001']['id']}/rescan", json={"preset": "last30m", "clip_len": 61, "lead_in": 10}).json()
        w = fresh_db.get("videos", rows["1000000001"]["id"])["scan_window"]
        assert w["kind"] == "range" and (w["start"], w["end"]) == (5400, 7200) and w["clip_len"] == 61 and w["lead_in"] == 10


def test_section_search_refreshes_a_live_streams_length(fresh_db, campaign, monkeypatch):
    from clipdash import sources
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "v9", "url": "u", "status": "new", "live": 1, "duration": 100})
    monkeypatch.setattr(sources, "video_info", lambda url: {"title": "still going", "channel": "lacy", "published_at": None, "duration": 4000, "is_stream": 1, "live_now": True})
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        sec = c.get(f"/api/videos/{vid}/section?refresh=1").json()   # the picker asks for a refresh when it opens
    assert sec["duration"] == 4000 and sec["title"] == "still going" and sec["live"] is True


def test_density_accumulates_per_minute_and_reports_coverage(fresh_db, campaign, tmp_path):
    v = {"id": 1, "platform": "twitch", "external_id": "d1", "campaign_id": campaign["id"]}
    dens = {"bin_s": 60, "bins": [], "covered": [], "source": None}
    rows = [[t, "hi"] for t in range(0, 600, 2)] + [[650 + i * 0.1, "KEKW"] for i in range(300)]
    pipeline._density_add(dens, rows, 0, 720, 7200)
    assert len(dens["bins"]) == 121 and dens["bins"][0] == 30 and dens["bins"][10] == 100 and dens["bins"][11] == 200 and dens["bins"][20] == 0
    assert dens["covered"] == [[0, 720]] and pipeline.density_complete(dens, 7200) is False
    pipeline._density_add(dens, [], 720, 7200, 7200)
    assert pipeline.density_complete(dens, 7200) is True


def test_queue_render_uses_section_clip_length(fresh_db, campaign, monkeypatch):
    monkeypatch.setattr("clipdash.jobs.enqueue", lambda *a, **k: 1)
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "q1", "url": "u", "status": "scanned", "duration": 10000,
                                     "scan_window": {"kind": "range", "start": 0, "end": 10000, "clip_len": 120, "lead_in": 30}})
    mid = fresh_db.insert("moments", {"video_id": vid, "t": 5000, "score": 9, "reasons": [], "chat_words": [], "rank": 1})
    cid = pipeline.queue_render(mid)
    clip = fresh_db.get("clips", cid)
    assert (clip["source_start"], clip["source_length"], clip["trim_end"]) == (4970, 122, 120)
    mid2 = fresh_db.insert("moments", {"video_id": vid, "t": 6000, "score": 8, "reasons": [], "chat_words": [], "rank": 2})
    clip2 = fresh_db.get("clips", pipeline.queue_render(mid2, clip_len=61, lead_in=0))
    assert (clip2["source_start"], clip2["source_length"], clip2["trim_end"]) == (6000, 63, 61)
