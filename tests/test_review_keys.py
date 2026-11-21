def test_review_keys_script_is_listed_and_served(fresh_db):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        assert "/static/features/review_keys.js" in c.get("/api/features").json()
        r = c.get("/static/features/review_keys.js")
        assert r.status_code == 200 and "REVIEW_KEYS" in r.text
