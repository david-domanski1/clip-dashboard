"""Post text: hooks from the transcript (never invented), a profanity filter, and per-platform caption
building with the campaign's required name and hashtags plus a few relevant tags (no tag stuffing)."""
from __future__ import annotations

import re

PROFANITY = {
    "fuck", "fucking", "fucked", "fucker", "motherfucker", "shit", "shitty", "bullshit", "bitch", "bitches",
    "ass", "asshole", "dick", "dicks", "pussy", "cunt", "cock", "nigga", "niggas", "nigger", "fag", "faggot",
    "retard", "retarded", "whore", "slut", "damn", "goddamn", "piss", "pissed", "bastard", "cum", "tits",
    "hoe", "hoes", "wtf", "stfu", "lmfao",
}
_WORD = re.compile(r"[A-Za-z']+")
PLATFORM_LIMITS = {
    "youtube": {"title": 100, "text": 5000, "tags": 6},
    "tiktok": {"title": 2200, "text": 2200, "tags": 6},
    "instagram": {"title": 0, "text": 2200, "tags": 8},
    "x": {"title": 0, "text": 280, "tags": 3},
}
GAME_TAGS = ["#gta6", "#gtavi", "#vicecity"]
DEFAULT_CREDIT_TEMPLATE = "{hook} — clip from {creator}'s stream ({handle})"


def credit_line(template: str | None, creator: str, handle: str | None, hook: str) -> str:
    """Caption opener for streamer-workspace clips: credits the streamer by name and handle. Editable per source."""
    t = (template or DEFAULT_CREDIT_TEMPLATE)
    h = handle or ""
    if h and not h.startswith("@"):
        h = "@" + h
    try:
        out = t.format(creator=creator, handle=h, hook=hook.strip())
    except (KeyError, IndexError, ValueError):
        out = DEFAULT_CREDIT_TEMPLATE.format(creator=creator, handle=h, hook=hook.strip())
    return re.sub(r"\(\s*\)", "", out).replace("  ", " ").strip()


def has_profanity(text: str) -> bool:
    return any(w.lower().strip("'") in PROFANITY for w in _WORD.findall(text or ""))


def censor(text: str) -> str:
    def rep(m):
        w = m.group(0)
        return w[0] + "*" * (len(w) - 1) if w.lower().strip("'") in PROFANITY else w
    return _WORD.sub(rep, text or "")


def sentences(words: list, max_gap: float = 0.8) -> list[dict]:
    """Group word-timed transcript entries (start, end, word) into sentences at punctuation or pauses."""
    out: list[dict] = []
    cur: list = []
    for i, (s, e, w) in enumerate(words):
        cur.append((s, e, w))
        nxt_gap = words[i + 1][0] - e if i + 1 < len(words) else 99
        if re.search(r"[.!?]$", w) or nxt_gap > max_gap:
            out.append({"start": cur[0][0], "end": cur[-1][1], "text": " ".join(x[2] for x in cur).strip()})
            cur = []
    if cur:
        out.append({"start": cur[0][0], "end": cur[-1][1], "text": " ".join(x[2] for x in cur).strip()})
    return out


def _clean_sentence(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^[^A-Za-z0-9\"']+", "", t)
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def hook_candidates(words: list, clip_start: float = 0.0, window: float = 25.0) -> list[dict]:
    """Complete, clean sentences (3-12 words) from the clip, best first. Earlier sentences and ones
    with a question or exclamation score higher; profanity disqualifies."""
    cands = []
    for s in sentences(words):
        text = _clean_sentence(s["text"])
        n = len(text.split())
        if n < 3 or n > 12 or has_profanity(text):
            continue
        if not re.search(r"[A-Za-z]{3,}", text):
            continue
        score = 1.0
        if re.search(r"[!?]$", text):
            score += 0.5
        if s["start"] - clip_start <= window:
            score += 0.5
        if 4 <= n <= 9:
            score += 0.3
        if re.search(r"\b(I|you|we|he|she|they|this|that|what|why|how|no|wait|bro|chat)\b", text, re.I):
            score += 0.2
        if not re.search(r"[.!?]$", text):
            text += "."
        cands.append({"text": text, "score": round(score, 2), "t": s["start"]})
    cands.sort(key=lambda c: (-c["score"], c["t"]))
    seen = set()
    out = []
    for c in cands:
        if c["text"].lower() in seen:
            continue
        seen.add(c["text"].lower())
        out.append(c)
    return out


def default_hook(creator: str, words: list, chat_words: list[str], clip_start: float = 0.0) -> str:
    c = hook_candidates(words, clip_start)
    if c:
        return c[0]["text"]
    tags = [w for w in chat_words if re.fullmatch(r"[A-Za-z]{3,15}", w) and not has_profanity(w)]
    if tags:
        return f"{creator} broke chat ({', '.join(tags[:2])})"
    return f"{creator} in the GTA 6 marathon"


def mention_creator(hook: str, creator: str, names: list[str]) -> str:
    """Make sure the required name appears in the text without mangling a sentence."""
    if any(re.search(rf"\b{re.escape(n)}\b", hook, re.I) for n in [creator, *names]):
        return hook
    return f"{creator}: {hook}"


def moment_tags(chat_words: list[str], max_n: int = 2) -> list[str]:
    out = []
    for w in chat_words:
        if w.startswith(("@", "http")) or "/" in w:
            continue
        w2 = re.sub(r"[^A-Za-z0-9]", "", w)
        if 3 <= len(w2) <= 15 and not has_profanity(w2) and w2.lower() not in ("the", "and", "this", "that", "lol"):
            out.append("#" + w2.lower())
        if len(out) >= max_n:
            break
    return out


def build_hashtags(campaign: dict, chat_words: list[str], platform: str, extra: list[str] | None = None) -> list[str]:
    """Required tags first, then game, streamer and moment tags up to the platform's sensible limit."""
    limit = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["tiktok"])["tags"]
    tags: list[str] = []

    def add(t: str):
        t = t.strip()
        if not t:
            return
        if not t.startswith("#"):
            t = "#" + t
        t = re.sub(r"[^#A-Za-z0-9_]", "", t)
        if t.lower() not in (x.lower() for x in tags) and len(t) > 1:
            tags.append(t)

    for t in campaign.get("required_hashtags") or []:
        add(t)
    required_n = len(tags)
    for t in extra or []:
        add(t)
    for t in GAME_TAGS:
        add(t)
    creator_tag = "#" + re.sub(r"[^A-Za-z0-9]", "", campaign.get("creator", ""))
    if len(creator_tag) > 1:
        add(creator_tag)
        add(creator_tag + "clips")
    for t in moment_tags(chat_words):
        add(t)
    return tags[: max(limit, required_n)]


def build_post_text(platform: str, campaign: dict, hook: str, hashtags: list[str]) -> dict:
    """Returns {'title': ..., 'text': ...} for the platform."""
    creator = campaign.get("creator", "")
    names = campaign.get("required_names") or []
    if campaign.get("kind") == "streamer":
        hook = credit_line(campaign.get("caption_template"), creator, campaign.get("handle"), hook)
    else:
        hook = mention_creator(hook.strip(), creator, names)
    lim = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["tiktok"])
    tagline = " ".join(hashtags)
    if platform == "youtube":
        title = hook
        if "#shorts" not in title.lower():
            title = (title[: lim["title"] - 8]).rstrip() + " #Shorts"
        text = f"{hook}\n\n{tagline}"
        return {"title": title[: lim["title"]], "text": text[: lim["text"]]}
    text = f"{hook} {tagline}".strip()
    if len(text) > lim["text"]:
        text = text[: lim["text"]]
    return {"title": "", "text": text}


def workspace_campaign(campaign: dict, source: dict | None) -> dict:
    """For streamer-workspace clips the 'creator' is the source channel; no required names or tags."""
    c = dict(campaign)
    c.update({"creator": (source or {}).get("name") or c.get("creator") or "the streamer",
              "handle": (source or {}).get("handle") or "", "caption_template": (source or {}).get("caption_template"),
              "required_names": [], "required_hashtags": []})
    return c


def build_all(campaign: dict, hook: str, chat_words: list[str], platforms: list[str] | None = None, source: dict | None = None) -> dict:
    if campaign.get("kind") == "streamer":
        campaign = workspace_campaign(campaign, source)
    out = {}
    for p in platforms or campaign.get("platforms") or ["youtube", "tiktok", "instagram"]:
        tags = build_hashtags(campaign, chat_words, p)
        out[p] = build_post_text(p, campaign, hook, tags)
    return out
