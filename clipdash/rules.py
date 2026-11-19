"""Campaign rules as data. Machine-checkable rules block approval; manual ones are a checklist."""
from __future__ import annotations

import re
from datetime import datetime, timezone

DEFAULT_CHECKLIST = [
    "The creator is the main focus for the whole clip",
    "Nothing in the clip makes the creator look bad or pushes a false narrative",
    "No logos, overlays or third-party watermarks are visible",
    "The clip stands on its own (a viewer gets it without context)",
]


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _contains(text: str, needle: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_])" + re.escape(needle) + r"(?![A-Za-z0-9_])", text or "", re.I))


def validate_clip(clip: dict, campaign: dict, video: dict | None, platforms: list[str] | None = None) -> list[dict]:
    """Return [{rule, ok, detail, platform?}]. Every rule here is checked by the machine."""
    res: list[dict] = []
    dur = clip.get("duration")
    mn, mx = campaign.get("min_len") or 0, campaign.get("max_len") or 0
    if campaign.get("kind") == "streamer":
        mn, mx = 0, 0
    if dur is None:
        res.append({"rule": "length", "ok": False, "detail": "Clip has not been rendered yet"})
    else:
        ok = dur >= mn - 0.05 and (not mx or dur <= mx + 0.05)
        res.append({"rule": "length", "ok": ok,
                    "detail": f"{dur:.1f}s (campaign wants {mn}s to {mx or '∞'}s)"})
    w, h = clip.get("width"), clip.get("height")
    if w and h:
        ok = (w, h) == (1080, 1920)
        res.append({"rule": "format", "ok": ok, "detail": f"{w}×{h}" + ("" if ok else ", expected 1080×1920")})
    else:
        res.append({"rule": "format", "ok": False, "detail": "Resolution unknown (not rendered)"})

    workspace = campaign.get("kind") == "streamer"
    names = [] if workspace else (campaign.get("required_names") or [campaign.get("creator")])
    tags = [] if workspace else (campaign.get("required_hashtags") or [])
    post_text = clip.get("post_text") or {}
    check_platforms = platforms or [p for p in (campaign.get("platforms") or []) if p in post_text] or list(post_text)
    for p in check_platforms:
        pt = post_text.get(p) or {}
        full = f"{pt.get('title', '')}\n{pt.get('text', '')}"
        for n in names:
            if n:
                res.append({"rule": "name", "platform": p, "ok": _contains(full, n),
                            "detail": f"{p}: mentions “{n}”" if _contains(full, n) else f"{p}: post text must mention “{n}”"})
        for t in tags:
            if t:
                res.append({"rule": "hashtag", "platform": p, "ok": _contains(full, t),
                            "detail": f"{p}: has {t}" if _contains(full, t) else f"{p}: post text must include {t}"})
        if has_profanity_text(full):
            res.append({"rule": "clean_text", "platform": p, "ok": False, "detail": f"{p}: post text contains profanity"})

    start, end = _parse_dt(campaign.get("source_start")), _parse_dt(campaign.get("source_end"))
    if video and (start or end) and not workspace:
        pub = _parse_dt(video.get("published_at"))
        if pub is None:
            res.append({"rule": "source_date", "ok": False, "detail": "Source publish date unknown"})
        else:
            ok = (not start or pub >= start) and (not end or pub <= end)
            res.append({"rule": "source_date", "ok": ok,
                        "detail": f"Source published {pub.date()}" + ("" if ok else f", campaign window starts {start.date() if start else '…'}")})
    if not campaign.get("watermark_allowed", 0) and not workspace:
        res.append({"rule": "no_watermark", "ok": not clip.get("watermark"),
                    "detail": "No logo or watermark added" if not clip.get("watermark") else "A watermark was added; the campaign forbids logos"})
    res.append({"rule": "no_ai_text", "ok": not clip.get("ai_text"),
                "detail": "Captions and hook come from the real transcript" if not clip.get("ai_text")
                else "Model-written text is in use; review it"})
    return res


def has_profanity_text(text: str) -> bool:
    from .captions import has_profanity
    return has_profanity(text)


def all_pass(results: list[dict]) -> bool:
    return all(r["ok"] for r in results)


def platform_allowed(campaign: dict, platform: str) -> bool:
    return platform in (campaign.get("platforms") or [])


def checklist_complete(campaign: dict, ticks: dict) -> bool:
    items = campaign.get("checklist") or DEFAULT_CHECKLIST
    if campaign.get("kind") == "streamer" and not campaign.get("checklist"):
        items = ["Credits the streamer and respects their clipping terms"]
    return all(ticks.get(str(i)) for i in range(len(items)))


def parse_rules_text(text: str) -> dict:
    """Best-effort extraction of rule fields from pasted campaign rules. Everything stays editable."""
    out: dict = {}
    t = text or ""
    if m := re.search(r"#([A-Za-z0-9_]+)", t):
        out["required_hashtags"] = sorted({"#" + x for x in re.findall(r"#([A-Za-z0-9_]{2,})", t)})[:3]
        out.setdefault("creator", m.group(1))
    if m := re.search(r"(\d+)\s*(?:min(?:ute)?s?)\s*(?:of|after)\s*posting", t, re.I):
        out["submit_minutes"] = int(m.group(1))
    if m := re.search(r"\$\s?(\d+(?:\.\d+)?)\s*(?:per|/)\s*(?:1,?000|1k|thousand)\s*views", t, re.I):
        out["pay_model"] = "cpm"
        out["rate_per_1k"] = float(m.group(1))
    if m := re.search(r"\$\s?([\d,]+(?:\.\d+)?)[^$]{0,60}?\b(?:pool|budget|prize pool)\b", t, re.I) or \
            re.search(r"\b(?:pool|budget)\b[^$]{0,40}\$\s?([\d,]+(?:\.\d+)?)", t, re.I):
        out["pool_budget"] = float(m.group(1).replace(",", ""))
        out["pay_model"] = "pool"
    if m := re.search(r"(?:cap|max(?:imum)?)[^$]{0,40}\$\s?([\d,]+(?:\.\d+)?)\s*(?:per|/)\s*(?:clip|video|post)", t, re.I):
        out["per_clip_cap"] = float(m.group(1).replace(",", ""))
    if m := re.search(r"([\d,]{5,})\s*(?:total\s+)?views?\s*(?:requirement|minimum|min)", t, re.I) or \
            re.search(r"(?:minimum|min)\s*(?:of\s*)?([\d,]{5,})\s*views", t, re.I):
        out["min_views"] = int(m.group(1).replace(",", ""))
    plats = []
    for name, key in (("tiktok", "tiktok"), ("reels", "instagram"), ("instagram", "instagram"),
                      ("shorts", "youtube"), ("youtube", "youtube"), (r"\bx\b", "x"), ("twitter", "x")):
        if re.search(name, t, re.I) and key not in plats:
            plats.append(key)
    if plats:
        out["platforms"] = plats
    if m := re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+(\d{4})", t, re.I) or \
            re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t, re.I):
        g = m.groups()
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        try:
            if g[0].isdigit():
                day, mon, year = int(g[0]), months.index(g[1][:3].lower()) + 1, int(g[2])
            else:
                mon, day, year = months.index(g[0][:3].lower()) + 1, int(g[1]), int(g[2])
            out["source_start"] = f"{year:04d}-{mon:02d}-{day:02d}"
        except (ValueError, IndexError):
            pass
    if re.search(r"no\s+(logos?|watermarks?)", t, re.I):
        out["watermark_allowed"] = 0
    if m := re.search(r"(?:at least|minimum|min)\s*(\d{2,3})\s*(?:seconds|secs|s)\b", t, re.I):
        out["min_len"] = int(m.group(1))
    return out
