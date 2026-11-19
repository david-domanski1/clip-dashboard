"""End-to-end smoke test on a short public YouTube video: scan → render → review data, stopping before posting.
Needs network, ffmpeg and the Whisper model; skipped unless CLIPDASH_E2E=1 (takes a few minutes)."""
import os
import time

import pytest

VIDEO = os.environ.get("CLIPDASH_E2E_VIDEO", "https://www.youtube.com/watch?v=NxgYtzq_n70")


@pytest.mark.skipif(os.environ.get("CLIPDASH_E2E") != "1", reason="set CLIPDASH_E2E=1 to run the online smoke test")
def test_scan_and_render_one_clip(fresh_db, campaign):
    from clipdash import db, jobs, pipeline, rules
    db.set_setting("auto_render_top", 1)
    db.set_setting("max_moments", 3)
    jobs.register_handlers()
    video = pipeline.add_video(campaign["id"], VIDEO, queue=False)
    t0 = time.time()
    pipeline.scan_video(video["id"], lambda f, m=None: None)
    moments = db.rows("moments", "video_id=?", (video["id"],))
    assert moments, "no moments found"
    assert all(m["reasons"] for m in moments)
    clip = db.one("clips", "video_id=?", (video["id"],))
    assert clip and clip["status"] == "rendering"
    pipeline.render_clip(clip["id"], lambda f, m=None: None)
    clip = db.get("clips", clip["id"])
    assert clip["status"] == "pending"
    assert (clip["width"], clip["height"]) == (1080, 1920)
    assert clip["duration"] >= 61
    assert clip["transcript"], "no transcript"
    assert "#LACY" in clip["post_text"]["tiktok"]["text"] and "LACY" in clip["post_text"]["tiktok"]["text"]
    res = rules.validate_clip(dict(clip, duration=clip["trim_end"] - clip["trim_start"]), campaign, db.get("videos", video["id"]))
    assert rules.all_pass(res), [r for r in res if not r["ok"]]
    pipeline.finalize_clip(clip["id"], lambda f, m=None: None)
    assert os.path.exists(db.get("clips", clip["id"])["final_path"])
    print(f"e2e took {time.time() - t0:.0f}s")
