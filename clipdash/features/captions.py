"""Caption styles and brand kit: how burned-in captions look, per campaign, with a global default.

Four styles (cut.CAPTION_STYLES), all transcribing exactly what was said: they change the look and timing, never the words.
  classic    uppercase groups of ~4 words, white with a black outline, lower-middle (the original look)
  pop        each word of a group appears at its spoken time (\\alpha + \\t, like the narrated intro)
  highlight  the whole group is shown and words turn the highlight colour as they are spoken (ASS \\k karaoke)
  boxed      1–2 words at a time on a dark box (BorderStyle 3)

A brand kit is font, size, colour, highlight colour, outline, position, name badge (plain text, never a logo) and
uppercase. The global default lives in settings (caption_style, caption_brand); a campaign may override it in
campaigns.brand (JSON, which may also carry "style"). style_for() merges campaign over default and validates every field,
falling back to the default for anything unusable. Changes apply to clips rendered afterwards; re-render to restyle."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config, cut, db, tools

log = logging.getLogger("clipdash.features.captions")

STYLES = list(cut.CAPTION_STYLES)
FONTS = ["Arial", "Helvetica", "Impact", "Verdana", "Trebuchet MS", "Georgia"]
POSITIONS = ["lower", "middle", "upper"]
SIZE_RANGE = (56, 110)
OUTLINE_RANGE = (0, 8)
DEFAULT_BRAND = dict(cut.DEFAULT_BRAND)
SAMPLE_WORDS = [[0.0, 0.3, "you"], [0.3, 0.55, "did"], [0.6, 1.0, "NOT"], [1.05, 1.4, "just"], [1.45, 1.7, "do"],
                [1.75, 2.1, "that"]]
SAMPLE_AT = 0.8          # mid-group: "you did NOT" spoken, "just" still to come
PREVIEW_VERSION = 1      # bump when the preview drawing changes so cached images are rebuilt

db.register_column("campaigns", "brand", "TEXT")
config.register_settings({"caption_style": "classic", "caption_brand": dict(DEFAULT_BRAND)})

router = APIRouter()
_render_lock = threading.Lock()
_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


# ---- validation and merging ----

def _bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        if v.strip().lower() in ("1", "true", "yes", "on"):
            return True
        if v.strip().lower() in ("0", "false", "no", "off"):
            return False
    return default


def _int(v, lo: int, hi: int, default: int) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _hex(v, default: str) -> str:
    if isinstance(v, str) and _HEX.match(v.strip()):
        return "#" + v.strip().lstrip("#").upper()
    return default


def validate_brand(raw: dict | None, base: dict | None = None) -> dict:
    """Every key of DEFAULT_BRAND, each taken from raw when usable, else from base (else the built-in default)."""
    base = {**DEFAULT_BRAND, **(base or {})}
    raw = raw if isinstance(raw, dict) else {}
    font = raw.get("font", base["font"])
    pos = raw.get("position", base["position"])
    return {
        "font": font if font in FONTS else (base["font"] if base["font"] in FONTS else "Arial"),
        "size": _int(raw.get("size", base["size"]), *SIZE_RANGE, default=_int(base["size"], *SIZE_RANGE, default=78)),
        "colour": _hex(raw.get("colour"), _hex(base["colour"], DEFAULT_BRAND["colour"])),
        "highlight": _hex(raw.get("highlight"), _hex(base["highlight"], DEFAULT_BRAND["highlight"])),
        "outline": _int(raw.get("outline", base["outline"]), *OUTLINE_RANGE, default=_int(base["outline"], *OUTLINE_RANGE, default=6)),
        "position": pos if pos in POSITIONS else (base["position"] if base["position"] in POSITIONS else "lower"),
        "name_badge": _bool(raw.get("name_badge"), _bool(base["name_badge"], True)),
        "uppercase": _bool(raw.get("uppercase"), _bool(base["uppercase"], True)),
    }


def _loads(v) -> dict | None:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else None
        except ValueError:
            return None
    return None


def default_style(settings: dict | None = None) -> dict:
    settings = settings if settings is not None else db.all_settings()
    style = settings.get("caption_style")
    return {"style": style if style in STYLES else "classic", "brand": validate_brand(settings.get("caption_brand"))}


def style_for(campaign: dict | None, settings: dict | None = None) -> dict:
    """{"style", "brand"} for rendering a campaign's clips: the campaign's own kit merged over the global default."""
    d = default_style(settings)
    own = _loads((campaign or {}).get("brand"))
    if not own:
        return d
    style = own.get("style")
    return {"style": style if style in STYLES else d["style"], "brand": validate_brand(own, d["brand"])}


# ---- preview ----

def preview_path(style: str, brand: dict, creator: str):
    key = json.dumps({"v": PREVIEW_VERSION, "style": style, "brand": brand, "creator": creator}, sort_keys=True)
    return config.CACHE / "caption_previews" / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".png")


def render_preview(style: str, brand: dict, creator: str = "CREATOR"):
    """One 1080×1920 PNG of the sample line on a dark gradient at a mid-group moment. Cached by its parameters."""
    out = preview_path(style, brand, creator)
    if out.exists() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    with _render_lock:
        if out.exists() and out.stat().st_size > 0:
            return out
        ass = out.with_suffix(".ass")
        cut.write_ass(cut.group_captions(SAMPLE_WORDS), creator, ass, style=style, brand=brand, words=SAMPLE_WORDS)
        tmp = out.with_name(out.stem + ".part.png")
        src = "gradients=s=1080x1920:c0=0x3a4660:c1=0x0c0f16:x0=0:y0=0:x1=1080:y1=1920:r=10:d=2"
        try:
            # Output-side -ss keeps real timestamps through the ass filter, so the frame shows the sample at SAMPLE_AT.
            tools.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", src,
                       "-vf", f"ass='{cut._ass_filter_path(ass)}'", "-ss", f"{SAMPLE_AT:.2f}", "-frames:v", "1", str(tmp)], timeout=60)
            tmp.replace(out)
        finally:
            ass.unlink(missing_ok=True)
            tmp.unlink(missing_ok=True)
    return out


def _campaign(campaign_id) -> dict | None:
    if campaign_id in (None, "", "default", "0", 0):
        return None
    try:
        c = db.get("campaigns", int(campaign_id))
    except (TypeError, ValueError):
        c = None
    if not c:
        raise HTTPException(404, "No such campaign")
    return c


# ---- API ----

@router.get("/api/captions/brand")
def get_brand(campaign_id: str | None = None):
    c = _campaign(campaign_id)
    eff = style_for(c)
    own = _loads(c.get("brand")) if c else None
    return {"campaign_id": c["id"] if c else None, "style": eff["style"], "brand": eff["brand"], "custom": bool(own),
            "default": default_style(), "styles": STYLES, "fonts": FONTS, "positions": POSITIONS,
            "size_range": list(SIZE_RANGE), "outline_range": list(OUTLINE_RANGE)}


@router.put("/api/captions/brand")
def put_brand(body: dict, campaign_id: str | None = None):
    """Save a style + brand kit for a campaign, or as the default (no campaign_id). {"inherit": true} on a campaign
    drops its own kit so it follows the default again."""
    c = _campaign(campaign_id)
    if c and body.get("inherit"):
        db.update("campaigns", c["id"], {"brand": None})
        return get_brand(str(c["id"]))
    base = default_style()
    style = body.get("style")
    if c:
        style = style if style in STYLES else style_for(c)["style"]
        db.update("campaigns", c["id"], {"brand": json.dumps({"style": style, **validate_brand(body, style_for(c)["brand"])})})
        return get_brand(str(c["id"]))
    db.set_setting("caption_style", style if style in STYLES else base["style"])
    db.set_setting("caption_brand", validate_brand(body, base["brand"]))
    return get_brand(None)


@router.get("/api/captions/preview.png")
def preview(request: Request, style: str | None = None, campaign_id: str | None = None):
    c = _campaign(campaign_id)
    eff = style_for(c)
    overrides = {k: v for k, v in request.query_params.items() if k in DEFAULT_BRAND}
    brand = validate_brand(overrides, eff["brand"])
    st = style if style in STYLES else eff["style"]
    creator = (c or {}).get("creator") or "CREATOR"
    try:
        path = render_preview(st, brand, creator)
    except tools.ToolError as e:
        raise HTTPException(500, f"Could not draw the caption preview: {str(e)[-200:]}")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=3600"})
