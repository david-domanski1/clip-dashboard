from pathlib import Path

from clipdash import campaign_import as ci
from clipdash import rules

URL = "https://contentrewards.com/c/campaigns/1d2bcb00-c904-415f-aa82-f95b96baad86"
HTML = (Path(__file__).parent / "fixtures" / "whop_kaicenat.html").read_text(encoding="utf-8", errors="replace")


def test_whop_page_imports_cleanly():
    f = ci.fields_from_page(HTML, URL)
    assert f["creator"] == "Kai Cenat"
    assert f["name"] == "Kai Cenat - $10,000 GTA 6 Pool Campaign"
    assert f["pay_model"] == "pool" and f["pool_budget"] == 10000
    assert set(f["platforms"]) == {"tiktok", "youtube", "instagram", "x"}
    assert f["submission_url"] == URL and f["url"] == URL
    assert f["rules_locked"] is True and "paste the rules" in f["note"].lower()
    assert f["required_hashtags"] == [] and "source_start" not in f
    assert "151515" not in f["rules_text"] and "function" not in f["rules_text"] and "{" not in f["rules_text"]
    assert "Kai Cenat" in f["rules_text"]


def test_visible_text_excludes_scripts_and_styles():
    page = ci.extract_page("<html><head><title>T | Site</title><style>.a{color:#151515}</style><script>var x = '#ffe066';</script></head>"
                           "<body><h1>Head</h1><p>Visible #LACY text</p><script>junk()</script></body></html>")
    assert page["title"] == "T | Site" and page["headings"] == ["Head"]
    assert "151515" not in page["text"] and "junk" not in page["text"] and "Visible #LACY text" in page["text"]


def test_hashtags_never_come_from_colour_codes_or_tokens():
    assert ci.safe_hashtags("use #LACY and #gta6; css #151515 #fff #1fd1d6 #123 #_") == ["#LACY", "#gta6"]
    parsed = rules.parse_rules_text("background: #151515; .x{} Captions must use #KaiCenat")
    assert parsed["required_hashtags"] == ["#KaiCenat"] and parsed.get("creator") == "KaiCenat"
    assert "required_hashtags" not in rules.parse_rules_text("color: #151515 #ffe066")


def test_api_parse_uses_importer(fresh_db, monkeypatch):
    from fastapi.testclient import TestClient
    from clipdash.app import app

    class R:
        text = HTML
        def raise_for_status(self): pass
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    with TestClient(app) as c:
        f = c.post("/api/campaigns/parse", json={"url": URL}).json()["fields"]
    assert f["creator"] == "Kai Cenat" and f["rules_locked"] and f["submission_url"] == URL
