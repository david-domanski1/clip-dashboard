def test_running_jobs_are_requeued_on_start(fresh_db, monkeypatch):
    from clipdash import jobs
    monkeypatch.setattr(jobs.threading.Thread, "start", lambda self: None)
    jid = fresh_db.insert("jobs", {"type": "render_clip", "payload": {"clip_id": 1}, "status": "running"})
    jobs.start(1)
    assert fresh_db.get("jobs", jid)["status"] == "queued"


def test_job_error_marks_clip_and_video(fresh_db, campaign):
    from clipdash import jobs
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "youtube", "external_id": "x", "url": "u", "status": "rendering"})
    cid = fresh_db.insert("clips", {"video_id": vid, "campaign_id": campaign["id"], "source_start": 0, "source_length": 90, "status": "rendering"})
    jobs.HANDLERS["boom"] = lambda p, pr: (_ for _ in ()).throw(RuntimeError("segment download failed"))
    jid = jobs.enqueue("boom", {"clip_id": cid})
    fresh_db.update("jobs", jid, {"type": "render_clip"})
    job = fresh_db.get("jobs", jid)
    jobs.HANDLERS["render_clip"] = jobs.HANDLERS["boom"]
    jobs._run(job)
    assert fresh_db.get("jobs", jid)["status"] == "error"
    assert fresh_db.get("clips", cid)["status"] == "error"
    assert fresh_db.get("videos", vid)["status"] == "done"
