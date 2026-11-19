import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="clipdash-test-")
os.environ["CLIPDASH_DATA"] = _tmp
os.environ["CLIPDASH_DRY_RUN"] = "1"
os.environ["CLIPDASH_NO_WORKERS"] = "1"


@pytest.fixture()
def fresh_db():
    from clipdash import config, db
    config.ensure_dirs()
    if config.DB_PATH.exists():
        db.conn().close()
        db._local.conn = None
        config.DB_PATH.unlink()
    db.init()
    yield db


@pytest.fixture()
def campaign(fresh_db):
    cid = fresh_db.insert("campaigns", {"name": "LACY GTA VI", "creator": "LACY", "required_names": ["LACY"],
                                       "required_hashtags": ["#LACY"], "min_len": 61, "max_len": 180,
                                       "platforms": ["youtube", "tiktok", "instagram"], "source_start": "2026-11-18",
                                       "watermark_allowed": 0, "submit_minutes": 30, "pay_model": "pool", "pool_budget": 12500})
    return fresh_db.get("campaigns", cid)
