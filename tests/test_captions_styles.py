"""Caption styles and brand kit: ASS per style, classic byte-identical, style_for merging/validation, the brand API,
the render_clip hand-off and the preview PNG."""
import json
import time

from clipdash import config, cut, tools
from clipdash.features import captions as cs

WORDS = [[0.0, 0.3, "you"], [0.3, 0.55, "did"], [0.6, 1.0, "NOT"], [1.05, 1.4, "just"], [1.45, 1.7, "do"], [1.75, 2.1, "that"]]

ORIGINAL_CLASSIC = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial,78,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Name,Arial,60,&H0066E0FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,5,2,8,60,60,160,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.00,Name,,0,0,0,,LACY
Dialogue: 0,0:00:00.00,0:00:01.40,Cap,,0,0,0,,YOU DID NOT JUST
Dialogue: 0,0:00:01.45,0:00:02.10,Cap,,0,0,0,,DO THAT
"""


def _ass(tmp_path, name="x", **kw):
    p = cut.write_ass(cut.group_captions(WORDS), "lacy", tmp_path / f"{name}.ass", **kw)
    return p.read_text(encoding="utf-8")


def _events(text):
    return [l for l in text.splitlines() if l.startswith("Dialogue:") and ",Cap," in l]


def test_classic_is_byte_identical(tmp_path):
    assert _ass(tmp_path, "plain") == ORIGINAL_CLASSIC
    assert _ass(tmp_path, "explicit", style="classic", brand=dict(cut.DEFAULT_BRAND), words=WORDS) == ORIGINAL_CLASSIC
    assert _ass(tmp_path, "valid", style="classic", brand=cs.validate_brand({})) == ORIGINAL_CLASSIC
    assert _ass(tmp_path, "unknown", style="sparkle") == ORIGINAL_CLASSIC


def test_pop_reveals_each_word_at_its_time(tmp_path):
    ev = _events(_ass(tmp_path, style="pop", words=WORDS))
    assert len(ev) == 2
    assert ev[0].endswith("{\\alpha&HFF&\\t(0,40,\\alpha&H00&)}YOU {\\alpha&HFF&\\t(300,340,\\alpha&H00&)}DID "
                          "{\\alpha&HFF&\\t(600,640,\\alpha&H00&)}NOT {\\alpha&HFF&\\t(1050,1090,\\alpha&H00&)}JUST")
    assert "\\t(300,340" in ev[1] and ev[1].count("\\alpha&HFF&") == 2  # times are relative to the group's start


def test_highlight_uses_karaoke_with_highlight_as_primary(tmp_path):
    txt = _ass(tmp_path, style="highlight", words=WORDS, brand={**cut.DEFAULT_BRAND, "highlight": "#00E0FF"})
    ev = _events(txt)
    assert ev[0].endswith("{\\k30}YOU {\\k30}DID {\\k45}NOT {\\k35}JUST")  # each word runs to the next word's start
    cap_style = next(l for l in txt.splitlines() if l.startswith("Style: Cap,"))
    # Primary (spoken) = highlight, Secondary (not yet spoken) = text colour
    assert cap_style.split(",")[3] == "&H00FFE000" and cap_style.split(",")[4] == "&H00FFFFFF"


def test_boxed_uses_an_opaque_box_and_two_words(tmp_path):
    txt = _ass(tmp_path, style="boxed", words=WORDS)
    cap_style = next(l for l in txt.splitlines() if l.startswith("Style: Cap,")).split(",")
    assert cap_style[15] == "3" and cap_style[7] != "&H80000000"   # BorderStyle 3 with a dark BackColour
    assert [e.split(",,")[-1] for e in _events(txt)] == ["YOU DID", "NOT JUST", "DO THAT"]


def test_styles_never_change_the_words(tmp_path):
    for style in cut.CAPTION_STYLES:
        for upper in (True, False):
            txt = _ass(tmp_path, style, style=style, words=WORDS, brand={**cut.DEFAULT_BRAND, "uppercase": upper})
            import re
            spoken = " ".join(re.sub(r"\{[^}]*\}", "", e.split(",,")[-1]) for e in _events(txt)).split()
            expect = [w[2] for w in WORDS]
            assert spoken == ([w.upper() for w in expect] if upper else expect)


def test_brand_font_size_position_badge(tmp_path):
    txt = _ass(tmp_path, style="classic", brand={**cut.DEFAULT_BRAND, "font": "Impact", "size": 100, "outline": 3,
                                                 "position": "upper", "name_badge": False, "colour": "#112233"})
    assert "Style: Cap,Impact,100,&H00332211," in txt and ",1,3,2,8,60,60,300,1" in txt
    assert ",Name,,0,0,0,,LACY" not in txt


def test_censor_still_applies(tmp_path, monkeypatch):
    monkeypatch.setattr(cut.cap, "censor", lambda s: s.replace("NOT", "N*T"))
    assert "N*T" in _ass(tmp_path, style="pop", words=WORDS, censor=True)


def test_validate_and_style_for_merge():
    bad = cs.validate_brand({"font": "Comic Sans", "size": 500, "colour": "red", "highlight": "#12345", "outline": -3,
                             "position": "sideways", "name_badge": "no", "uppercase": "maybe"})
    assert bad == {**cs.DEFAULT_BRAND, "size": 110, "outline": 0, "name_badge": False}
    assert cs.validate_brand({"colour": "00ff00"})["colour"] == "#00FF00"
    settings = {"caption_style": "pop", "caption_brand": {"font": "Verdana", "highlight": "#FF0000", "size": 90}}
    d = cs.style_for(None, settings)
    assert d["style"] == "pop" and d["brand"]["font"] == "Verdana" and d["brand"]["size"] == 90 and d["brand"]["outline"] == 6
    camp = {"brand": json.dumps({"style": "boxed", "size": 70, "colour": "nope", "font": "Georgia"})}
    m = cs.style_for(camp, settings)
    assert m["style"] == "boxed" and m["brand"]["size"] == 70 and m["brand"]["font"] == "Georgia"
    assert m["brand"]["colour"] == "#FFFFFF" and m["brand"]["highlight"] == "#FF0000"   # bad hex → default, rest inherited
    assert cs.style_for({"brand": "{broken"}, settings) == d and cs.style_for({"brand": None}, settings) == d
    assert cs.style_for({"brand": {"style": "weird"}}, {"caption_style": "nope"})["style"] == "classic"


def test_brand_api_and_render_hand_off(fresh_db, campaign, monkeypatch):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        st = c.get("/api/captions/brand").json()
        assert st["style"] == "classic" and st["brand"] == cs.DEFAULT_BRAND and st["fonts"][0] == "Arial"
        r = c.put("/api/captions/brand", json={"style": "highlight", "highlight": "#00ff88", "size": 999}).json()
        assert r["style"] == "highlight" and r["brand"]["highlight"] == "#00FF88" and r["brand"]["size"] == 110
        r = c.get(f"/api/captions/brand?campaign_id={campaign['id']}").json()
        assert r["custom"] is False and r["style"] == "highlight"           # follows the default
        r = c.put(f"/api/captions/brand?campaign_id={campaign['id']}", json={"style": "boxed", "font": "Impact"}).json()
        assert r["custom"] and r["style"] == "boxed" and r["brand"]["font"] == "Impact" and r["brand"]["highlight"] == "#00FF88"
        assert c.get("/api/captions/brand").json()["style"] == "highlight"   # default untouched
        assert cs.style_for(fresh_db.get("campaigns", campaign["id"]))["style"] == "boxed"
        r = c.put(f"/api/captions/brand?campaign_id={campaign['id']}", json={"inherit": True}).json()
        assert r["custom"] is False and r["style"] == "highlight"
        assert c.get("/api/captions/brand?campaign_id=99999").status_code == 404
        assert "/static/features/captions.js" in c.get("/api/features").json()


def test_preview_png_is_1080x1920_and_cached(fresh_db, campaign, monkeypatch):
    from fastapi.testclient import TestClient
    from clipdash.app import app
    with TestClient(app) as c:
        t = time.time()
        r = c.get(f"/api/captions/preview.png?style=highlight&campaign_id={campaign['id']}&highlight=%2300E0FF&font=Impact")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and time.time() - t < 20
        files = list((config.CACHE / "caption_previews").glob("*.png"))
        assert len(files) == 1 and tools.media_size(files[0]) == (1080, 1920)
        calls = []
        monkeypatch.setattr(tools, "run", lambda *a, **k: calls.append(a))
        again = c.get(f"/api/captions/preview.png?style=highlight&campaign_id={campaign['id']}&highlight=%2300E0FF&font=Impact")
        assert again.content == r.content and calls == []                    # served from the cache
