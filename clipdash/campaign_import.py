"""Import campaign fields from a campaign page (Whop Content Rewards, Reach, …). Scripts, styles and inline JS are
dropped before any text is read; values come from the page title, Open Graph tags and visible headings/text."""
from __future__ import annotations

import html as htmllib
import re
from html.parser import HTMLParser

from . import rules

HEX_LIKE = re.compile(r"^#?[0-9a-fA-F]{3,8}$")
LOCKED_PATTERNS = re.compile(r"visible once you join|join to unlock|join to see|only visible (?:to|after)|after joining|members only", re.I)
PLATFORM_WORDS = (("tiktok", "tiktok"), ("youtube", "youtube"), ("shorts", "youtube"), ("instagram", "instagram"), ("reels", "instagram"),
                  (r"\bx\b", "x"), ("twitter", "x"))


class _Visible(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    HEAD = {"h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_head: str | None = None
        self.text: list[str] = []
        self.headings: list[str] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self._head_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = a.get("property") or a.get("name")
            if key and a.get("content"):
                self.meta[key.lower()] = htmllib.unescape(a["content"]).strip()
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in self.SKIP and tag != "head":
            self.skip += 1
        elif tag in self.HEAD:
            self.in_head = tag
            self._head_buf = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and tag != "head":
            self.skip = max(0, self.skip - 1)
        elif tag in self.HEAD and self.in_head:
            h = re.sub(r"\s+", " ", " ".join(self._head_buf)).strip()
            if h:
                self.headings.append(h)
            self.in_head = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip:
            return
        d = data.strip()
        if not d:
            return
        if self.in_head:
            self._head_buf.append(d)
        self.text.append(d)


def extract_page(html: str) -> dict:
    p = _Visible()
    p.feed(html)
    text = re.sub(r"\s+", " ", " ".join(p.text)).strip()
    text = re.sub(r"[⁦-⁩]", "", text)
    return {"title": re.sub(r"\s+", " ", htmllib.unescape(p.title)).strip(), "headings": p.headings, "meta": p.meta, "text": text}


def _clean_title(t: str) -> str:
    t = re.split(r"\s*\|\s*", t)[0]
    t = re.sub(r"\s+by\s+.+$", "", t)
    return t.strip()


def _creator_from_title(title: str) -> str | None:
    m = re.match(r"\s*(.+?)\s*[-–—:]\s*\$?\d", title)
    if m:
        return m.group(1).strip()
    m = re.match(r"\s*(.+?)\s*[-–—:]\s", title)
    return m.group(1).strip() if m else None


def safe_hashtags(text: str) -> list[str]:
    """Hashtags that are real words: never colour codes, CSS tokens or script fragments."""
    out = []
    for h in re.findall(r"#([A-Za-z0-9_]{2,30})", text or ""):
        if HEX_LIKE.match(h) or h.isdigit() or not re.search(r"[A-Za-z]", h):
            continue
        tag = "#" + h
        if tag.lower() not in (x.lower() for x in out):
            out.append(tag)
    return out[:3]


def fields_from_page(html: str, url: str) -> dict:
    page = extract_page(html)
    meta = page["meta"]
    title = _clean_title(meta.get("og:title") or page["title"] or (page["headings"][0] if page["headings"] else ""))
    desc = meta.get("og:description") or meta.get("description") or ""
    visible = page["text"]
    corpus = f"{title}\n{desc}\n{visible}"
    fields: dict = {"url": url, "submission_url": url}
    if title:
        fields["name"] = title
        creator = _creator_from_title(title)
        if creator and not HEX_LIKE.match(creator):
            fields["creator"] = creator
            fields["required_names"] = [creator]
    parsed = rules.parse_rules_text(corpus)
    for k in ("pool_budget", "pay_model", "rate_per_1k", "per_clip_cap", "min_views", "submit_minutes", "min_len", "source_start", "watermark_allowed", "bans_ai", "ai_narration"):
        if k in parsed:
            fields[k] = parsed[k]
    if re.search(r"\bpool\b", corpus, re.I):
        fields["pay_model"] = "pool"
    if m := re.search(r"\$\s?([\d,]+(?:\.\d+)?)\s*(?:K\b)?", corpus) if "pool_budget" not in fields else None:
        val = float(m.group(1).replace(",", ""))
        if re.search(rf"\$\s?{re.escape(m.group(1))}\s*K\b", corpus):
            val *= 1000
        fields["pool_budget"] = val
    plats = []
    for word, key in PLATFORM_WORDS:
        if re.search(word, f"{desc} {visible}", re.I) and key not in plats:
            plats.append(key)
    if plats:
        fields["platforms"] = plats
    tags = safe_hashtags(f"{desc} {visible}")
    locked = bool(LOCKED_PATTERNS.search(visible))
    fields["rules_locked"] = locked
    if locked:
        fields["note"] = ("This page only shows the campaign's rules after you join. Join it, then paste the rules text here "
                          "and click Pre-fill: required hashtag, name and footage dates are left empty on purpose.")
        fields["required_hashtags"] = []
        fields.pop("source_start", None)
    elif tags:
        fields["required_hashtags"] = tags
    fields["rules_text"] = (f"{title}\n{desc}\n\n{visible}" if not locked else f"{title}\n{desc}\n\n(Rules are visible only after joining: paste them here.)")[:5000]
    return fields
