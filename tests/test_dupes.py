"""Duplicate guard: overlap maths, cross-campaign detection by platform + external_id, blocked vs warn, self-exclusion,
and the /api/dupes endpoints on two campaigns sharing one VOD."""
import pytest
from fastapi.testclient import TestClient

from clipdash.features import dupes


def test_overlap_maths():
    assert dupes.overlap(0, 90, 60, 90) == 30
    assert dupes.overlap(60, 90, 0, 90) == 30
    assert dupes.overlap(0, 90, 90, 30) == 0          # touching, not overlapping
    assert dupes.overlap(0, 30, 100, 30) == 0
    assert dupes.overlap(10, 100, 20, 10) == 10        # contained
    assert dupes.overlap(0, 0, 0, 50) == 0
    assert dupes.overlap(5.5, 10, 0, 10) == 4.5


def _c(clip_id, campaign_id=1, start=100, length=90, t=None, platform="twitch", ext="v1", status="pending", posts=()):
    return {"clip_id": clip_id, "campaign_id": campaign_id, "campaign_name": f"C{campaign_id}", "status": status,
            "platform": platform, "external_id": ext, "start": start, "length": length, "t": t, "posts": list(posts)}


def test_find_duplicates_rules_and_self_exclusion():
    target = {"clip_id": 1, "campaign_id": 1, "platform": "twitch", "external_id": "v1", "start": 100, "length": 90, "t": 110}
    clips = [_c(1), _c(2, start=170), _c(3, start=175, t=None), _c(4, start=500, t=150), _c(5, ext="v2"),
             _c(6, platform="youtube"), _c(7, campaign_id=2, start=120)]
    got = {m["clip_id"]: m for m in dupes.find_duplicates(target, clips)}
    assert 1 not in got                                 # itself
    assert got[2]["overlap_s"] == 20                    # exactly 20 s counts
    assert 3 not in got                                 # 15 s overlap, no moment time
    assert 4 in got and got[4]["overlap_s"] == 0        # moment within 45 s
    assert 5 not in got and 6 not in got                # other VOD / other platform
    assert got[7]["same_campaign"] is False and got[2]["same_campaign"] is True
    # moment 46 s away and no overlap → not a match
    assert not dupes.find_duplicates(target, [_c(8, start=500, t=156)])


def test_blocked_vs_warn():
    target = {"clip_id": 1, "campaign_id": 1, "platform": "twitch", "external_id": "v1", "start": 100, "length": 90}
    posted = [{"platform": "tiktok", "status": "live", "posted_at": "2026-11-20T10:00:00+00:00"}]
    failed = [{"platform": "tiktok", "status": "failed"}]
    same_posted = dupes.find_duplicates(target, [_c(2, posts=posted, status="posted")])
    assert same_posted[0]["posted_platforms"] == ["tiktok"]
    a = dupes.assess(same_posted, ["tiktok", "youtube"])
    assert a == {"severity": "blocked", "message": "Already posted for this campaign: tiktok on 2026-11-20"}
    # posted to a platform this campaign does not use → only a warning
    assert dupes.assess(same_posted, ["youtube"])["severity"] == "warn"
    # failed posts do not count as posted
    assert dupes.assess(dupes.find_duplicates(target, [_c(2, posts=failed)]), ["tiktok"])["severity"] == "warn"
    # another campaign, even posted → warn naming that campaign
    other = dupes.find_duplicates(target, [_c(3, campaign_id=2, posts=posted, status="posted")])
    assert dupes.assess(other, ["tiktok"]) == {"severity": "warn", "message": "This moment was already clipped for C2 (Posted)"}
    assert dupes.assess([], ["tiktok"]) == {"severity": None, "message": ""}


# ---- endpoints: two campaigns sharing one VOD ----

@pytest.fixture()
def shared(fresh_db, campaign):
    db = fresh_db
    c2 = db.insert("campaigns", {"name": "Other campaign", "creator": "LACY", "platforms": ["tiktok"]})
    v1 = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "2600001", "url": "u", "status": "scanned"})
    v2 = db.insert("videos", {"campaign_id": c2, "platform": "twitch", "external_id": "2600001", "url": "u", "status": "scanned"})
    v3 = db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "999", "url": "u", "status": "scanned"})
    m1 = db.insert("moments", {"video_id": v1, "t": 1000, "score": 3})
    m1b = db.insert("moments", {"video_id": v1, "t": 4000, "score": 2})
    m2 = db.insert("moments", {"video_id": v2, "t": 1010, "score": 3})
    m3 = db.insert("moments", {"video_id": v3, "t": 1000, "score": 3})
    clip = lambda vid, cid, mid, start, status: db.insert("clips", {"video_id": vid, "campaign_id": cid, "moment_id": mid,
                                                                   "source_start": start, "source_length": 90, "status": status})
    a = clip(v1, campaign["id"], m1, 992, "pending")
    b = clip(v2, c2, m2, 1002, "posted")
    other_vod = clip(v3, campaign["id"], m3, 992, "posted")
    acc = db.insert("accounts", {"platform": "tiktok", "label": "tt", "route": "assisted"})
    db.insert("posts", {"clip_id": b, "account_id": acc, "campaign_id": c2, "platform": "tiktok", "route": "assisted",
                        "status": "live", "posted_at": "2026-11-20T10:00:00+00:00"})
    db.insert("posts", {"clip_id": other_vod, "account_id": acc, "campaign_id": campaign["id"], "platform": "tiktok",
                        "route": "assisted", "status": "live", "posted_at": "2026-11-20T10:00:00+00:00"})
    from clipdash.app import app
    with TestClient(app) as client:
        yield {"client": client, "db": db, "c1": campaign["id"], "c2": c2, "v1": v1, "v2": v2, "m1": m1, "m1b": m1b,
               "a": a, "b": b, "acc": acc}


def test_clip_endpoint_cross_campaign_warn_then_same_campaign_blocked(shared):
    s, cl = shared, shared["client"]
    r = cl.get(f"/api/dupes/clip/{s['a']}").json()
    assert r["severity"] == "warn"
    assert [m["clip_id"] for m in r["matches"]] == [s["b"]]          # itself and the other VOD excluded
    m = r["matches"][0]
    assert m["campaign_name"] == "Other campaign" and m["same_campaign"] is False and m["posted_platforms"] == ["tiktok"]
    assert m["overlap_s"] == 80 and m["status_label"] == "Posted"
    assert r["message"] == "This moment was already clipped for Other campaign (Posted)"
    # the other campaign's clip sees ours as a warning (ours is not posted)
    rb = cl.get(f"/api/dupes/clip/{s['b']}").json()
    assert rb["severity"] == "warn" and [x["clip_id"] for x in rb["matches"]] == [s["a"]]
    # a second clip of the same footage in campaign 1 that is already live on tiktok → a is blocked
    db = s["db"]
    dup = db.insert("clips", {"video_id": s["v1"], "campaign_id": s["c1"], "moment_id": None, "source_start": 1010,
                              "source_length": 90, "status": "posted"})
    db.insert("posts", {"clip_id": dup, "account_id": s["acc"], "campaign_id": s["c1"], "platform": "tiktok",
                        "route": "assisted", "status": "scheduled", "scheduled_at": "2026-11-22T18:00:00+00:00"})
    r2 = cl.get(f"/api/dupes/clip/{s['a']}").json()
    assert r2["severity"] == "blocked"
    assert r2["message"] == "Already posted for this campaign: tiktok on 2026-11-22"
    assert r2["matches"][0]["clip_id"] == dup and r2["matches"][0]["same_campaign"] is True
    # the Today card data lists the pending clip
    assert [x["clip_id"] for x in cl.get(f"/api/dupes/campaign/{s['c1']}").json()["blocked"]] == [s["a"]]
    assert cl.get(f"/api/dupes/campaign/{s['c2']}").json()["blocked"] == []


def test_video_endpoint_batches_by_moment(shared):
    s, cl = shared, shared["client"]
    r = cl.get(f"/api/dupes/video/{s['v1']}").json()
    assert set(r) == {str(s["m1"])}                                  # the far moment has no match
    assert [m["clip_id"] for m in r[str(s["m1"])]] == [s["b"]]      # its own clip excluded
    r2 = cl.get(f"/api/dupes/video/{s['v2']}").json()
    assert [m["clip_id"] for m in r2[next(iter(r2))]] == [s["a"]]


def test_unknown_ids_404(shared):
    cl = shared["client"]
    assert cl.get("/api/dupes/clip/99999").status_code == 404
    assert cl.get("/api/dupes/video/99999").status_code == 404


def test_server_refuses_to_approve_a_blocked_duplicate(fresh_db, campaign):
    """The Review guard is not the only protection: the approve endpoint itself refuses a blocked duplicate."""
    from fastapi.testclient import TestClient
    from clipdash.app import app
    vid = fresh_db.insert("videos", {"campaign_id": campaign["id"], "platform": "twitch", "external_id": "999000111", "url": "u", "status": "done"})
    base = {"video_id": vid, "campaign_id": campaign["id"], "path": "/nonexistent.mp4", "source_start": 1000, "source_length": 92, "duration": 80.0,
            "trim_start": 0, "trim_end": 70.0, "width": 1080, "height": 1920, "hook": "LACY loses it.",
            "post_text": {"youtube": {"title": "LACY loses it", "text": "LACY loses it #LACY"}, "tiktok": {"title": "", "text": "LACY #LACY"},
                          "instagram": {"title": "", "text": "LACY #LACY"}}, "checklist": {"0": True, "1": True, "2": True, "3": True}}
    first = fresh_db.insert("clips", {**base, "status": "posted"})
    acc = fresh_db.insert("accounts", {"platform": "youtube", "route": "assisted", "label": "yt"})
    fresh_db.insert("posts", {"clip_id": first, "campaign_id": campaign["id"], "account_id": acc, "platform": "youtube", "route": "assisted",
                              "status": "live", "post_text": "", "posted_at": "2026-11-20T10:00:00+00:00"})
    second = fresh_db.insert("clips", {**base, "source_start": 1010, "status": "pending"})
    with TestClient(app) as c:
        r = c.post(f"/api/clips/{second}/approve", json={"checklist": {"0": True, "1": True, "2": True, "3": True}})
    assert r.status_code == 409 and "already posted" in r.json()["message"].lower()
