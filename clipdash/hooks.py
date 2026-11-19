"""Model-written cliffhanger hooks for the optional narrated intro. Generated from the clip's real transcript and
chat context, validated (factual framing is the model's job; length and profanity are checked here), and always
shown to the user before anything renders. Nothing here runs unless the campaign allows AI narration."""
from __future__ import annotations

import json
import re

from . import captions, db, secrets_store

MAX_WORDS = 15
SYSTEM = (
    "You write one-sentence spoken hooks for short vertical gaming clips. The hook plays as a voice-over before the clip "
    "starts, so it must be a cliffhanger that is TRUE to what actually happens in the transcript: never invent events, "
    "names or outcomes that are not in the transcript or chat. Third person, present or past tense, no hashtags, no emojis, "
    "no profanity, no quotes around it, under 15 words, one sentence ending with a period or question mark. "
    "Example of the style: He had no idea the cops were already behind him."
)


def validate_hook(text: str) -> tuple[bool, str]:
    t = re.sub(r"\s+", " ", (text or "")).strip().strip('"“”')
    if not t:
        return False, "Empty hook"
    if "\n" in (text or "") or len(t.split()) > MAX_WORDS:
        return False, f"Hook must be one sentence under {MAX_WORDS + 1} words"
    if captions.has_profanity(t):
        return False, "Hook contains profanity"
    if re.search(r"#\w|@\w|[\U0001F300-\U0001FAFF]", t):
        return False, "No hashtags, mentions or emojis in a spoken hook"
    if not re.search(r"[.!?]$", t):
        t += "."
    return True, t


def _context(clip: dict, creator: str) -> str:
    words = clip.get("transcript") or []
    text = " ".join(w[2] for w in words)[:4000]
    chat = ", ".join((clip.get("chat_words") or [])[:8])
    return (f"Streamer: {creator}\nGame: GTA VI\nTranscript of the clip (what was actually said):\n{text or '(no speech)'}\n"
            f"Top chat words during the moment: {chat or '(none)'}\n\n"
            "Write 3 different hooks as a JSON array of strings and nothing else.")


def provider() -> str:
    return db.get_setting("llm_provider", "anthropic")


def available() -> tuple[bool, str]:
    p = provider()
    key = "anthropic_api_key" if p == "anthropic" else "openai_api_key"
    if not secrets_store.has(key):
        return False, f"Add your {'Anthropic' if p == 'anthropic' else 'OpenAI'} API key in Setup → Narration first."
    return True, p


def _anthropic(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=secrets_store.get("anthropic_api_key"))
    resp = client.messages.create(model=db.get_setting("llm_model") or "claude-opus-5-5", max_tokens=400,
                                  output_config={"effort": "low"}, system=SYSTEM,
                                  messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason == "refusal":
        raise RuntimeError("The model declined to write a hook for this clip")
    return "".join(b.text for b in resp.content if b.type == "text")


def _openai(prompt: str) -> str:
    import requests
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {secrets_store.get('openai_api_key')}", "Content-Type": "application/json"},
                      json={"model": db.get_setting("llm_model") or "gpt-4o-mini", "max_tokens": 400,
                            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def parse_hooks(raw: str) -> list[str]:
    m = re.search(r"\[.*\]", raw, re.S)
    cands: list[str] = []
    if m:
        try:
            cands = [str(x) for x in json.loads(m.group(0))]
        except ValueError:
            cands = []
    if not cands:
        cands = [l.strip("-•* 0123456789.)\"") for l in raw.split("\n") if l.strip()]
    out = []
    for c in cands:
        ok, t = validate_hook(c)
        if ok and t not in out:
            out.append(t)
    return out[:3]


def generate_hooks(clip: dict, creator: str) -> list[str]:
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    prompt = _context(clip, creator)
    raw = _anthropic(prompt) if provider() == "anthropic" else _openai(prompt)
    hooks = parse_hooks(raw)
    if not hooks:
        raise RuntimeError("The model returned no usable hook (all failed length or profanity checks)")
    return hooks
