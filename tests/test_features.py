"""Extension points: feature modules register schema, settings, routers and event handlers without touching the core."""
from clipdash import config, db, events


def test_feature_registration_and_events(fresh_db, monkeypatch):
    db.register_table("CREATE TABLE IF NOT EXISTS t_feature (id INTEGER PRIMARY KEY, note TEXT)")
    db.register_column("clips", "feature_flag", "INTEGER NOT NULL DEFAULT 0")
    db.init()
    assert db.execute("SELECT COUNT(*) c FROM t_feature").fetchone()["c"] == 0
    assert "feature_flag" in {r["name"] for r in db.execute("PRAGMA table_info(clips)")}
    config.register_settings({"feature_knob": 3})
    assert db.all_settings()["feature_knob"] == 3
    seen = []
    events.on("clip.ready", lambda clip_id: seen.append(clip_id))
    events.on("clip.ready", lambda clip_id: 1 / 0)          # a broken handler never breaks the emitter
    events.emit("clip.ready", clip_id=7)
    assert seen == [7]
    events.clear()


def test_feature_scripts_are_listed(fresh_db):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        r = c.get("/api/features").json()
        assert isinstance(r, list) and all(x.startswith("/static/features/") for x in r)
        assert c.put("/api/settings", json={"feature_knob": 4}).json().get("feature_knob") == 4
