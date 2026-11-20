"""Turns stored failure text into one sentence a non-developer understands, plus a suggested fix for publish failures.

The raw text (exception type, stderr tail) stays on the row and in the job log; only the wording shown in the UI changes.
Patterns are matched in order; the first hit wins. Keep sentences short and tell the person what to do next."""
from __future__ import annotations

import re

INTERRUPTED = "Interrupted — retry"

# (regex on the raw text, sentence shown)
_PATTERNS: list[tuple[str, str]] = [
    (r"Interrupted", INTERRUPTED),
    (r"cancelled", "Cancelled."),
    (r"No space left|disk full|ENOSPC", "The disk is full. Free some space (Settings → Clear cache) and retry."),
    (r"timed out|Timeout|Read timed out|ReadTimeout", "The download took too long. Check the connection and retry."),
    (r"BadZipFile|EOFError|archive was corrupt|download was incomplete", "A download came through incomplete. Retry; it starts over."),
    (r"TwitchDownloaderCLI does not run", "TwitchDownloaderCLI could not start on this computer. Re-download it under Settings → Setup."),
    (r"Chat replay is not available|no chat replay|chat.*404", "Twitch has no chat replay for this video, so chat moments can't be found. Scan still works from audio."),
    (r"subscriber-only|sub-only|sub only|403 Forbidden", "The platform refused the download (private, sub-only or removed video)."),
    (r"VOD has no playable segments", "The stream just started and has no video yet. Try again in a few minutes."),
    (r"live event will begin|Premieres in|has not started|is upcoming", "The stream hasn't started yet. Add it again once it is live."),
    (r"Video unavailable|This video is private|has been removed", "The video is unavailable (removed, private or still processing)."),
    (r"Could not read the video's duration|unavailable", "The video is unavailable (removed, private or still processing)."),
    (r"HTTP Error 429|Too Many Requests|rate limit", "The platform is rate-limiting downloads. Wait a few minutes and retry."),
    (r"ffmpeg timed out", "The video converter (ffmpeg) got stuck. Retry; if it keeps happening, re-scan the video."),
    (r"ffmpeg.*failed|ffprobe", "The video converter (ffmpeg) failed on this clip. Retry; if it keeps failing, re-cut it 15 s earlier or later."),
    (r"faster_whisper|whisper|model.*not found|snapshot", "The caption model could not load. Download it under Settings → Setup, then retry."),
    (r"piper|Piper|say failed|No voice could be produced", "No voice could be produced for the intro. Download the Piper voice under Settings → Setup or pick another voice."),
    (r"No API key|api key|Add your .* key", "An API key is missing. Add it under Settings → Setup."),
    (r"refusal|refused to write", "The hook writer declined this clip. Write the hook yourself."),
    (r"not connected|Click Connect", "The account is not connected. Open Settings → Accounts and click Connect."),
    (r"token expired|token refresh failed|reconnect the account|invalid_grant", "The account's login expired. Open Settings → Accounts and reconnect it."),
    (r"quota|quotaExceeded", "YouTube's daily upload quota is used up. It resets at midnight Pacific time."),
    (r"Rule check failed", "A campaign rule fails for this clip. Open it in Review to see which."),
    (r"Only approved clips can be posted", "The clip is no longer approved. Approve it again in Review."),
    (r"ConnectionError|Connection refused|Name or service not known|nodename nor servname|getaddrinfo|Max retries", "No internet connection, or the platform is unreachable. Retry when online."),
    (r"Service refused the post", "The posting service refused the post."),
    (r"Could not resolve that channel", "That channel could not be found. Check the URL."),
]


def friendly(raw: str | None) -> str:
    """One plain sentence for the given raw failure text. Never empty."""
    text = str(raw or "").strip()
    if not text:
        return "It failed and no reason was recorded. Retry; if it fails again the reason will be here."
    for pat, sentence in _PATTERNS:
        if re.search(pat, text, re.I):
            return sentence
    # Fall back to the tool's own ERROR line if there is one, else the first line that is not just "<tool> failed:".
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    err_lines = [l for l in lines if l.startswith("ERROR")]
    line = err_lines[0] if err_lines else next((l for l in lines if not re.search(r"failed:?$", l)), lines[0])
    line = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*\S+:\s*)?", "", line)
    line = re.sub(r"^[A-Za-z_.]*(Error|Exception|Cancelled)\s*:\s*", "", line).strip()
    return (line[:157] + "…") if len(line) > 160 else line


def platform_message(raw: str | None) -> str:
    """The platform's own words, if the raw text carries them (after the last ': ' or inside braces)."""
    text = str(raw or "").strip().splitlines()[0] if raw else ""
    m = re.search(r"\{.*\}", text)
    if m:
        return m.group(0)[:300]
    return text[:300]


def suggest_fix(platform: str, route: str, raw: str | None) -> str:
    """A next step for a failed post, from the platform's message."""
    t = str(raw or "")
    low = t.lower()
    if re.search(r"not connected|click connect", low):
        return "Settings → Accounts → Connect."
    if re.search(r"expired|invalid_grant|refresh failed|401|unauthorized", low):
        return "Settings → Accounts → reconnect the account, then Retry."
    if "quota" in low:
        return "Wait until YouTube's quota resets (midnight Pacific) or post through the posting service / assisted route."
    if re.search(r"privacy level|privacy_level", low):
        return "Open Post / schedule again and pick who can view the video."
    if re.search(r"unaudited|SELF_ONLY|only me", low):
        return "Until TikTok audits the app, direct posts are 'only me'. Use the posting service or the assisted route for public posts."
    if re.search(r"too (long|large)|under 300 MB|at most \d+s", low):
        return "Trim the clip in Review (shorter or smaller), approve again, then Retry."
    if re.search(r"rule check failed", low):
        return "Open the clip in Review, fix the failing rule, approve again, then Retry."
    if re.search(r"still processing", low):
        return "Wait a few minutes and click Retry; the upload is already on the platform."
    if route == "service":
        if re.search(r"401|api key|rejected the api key", low):
            return "Check the posting-service API key under Settings → Setup."
        if re.search(r"no .* account connected|connect it on their dashboard", low):
            return "Connect the account on the posting service's dashboard, then Retry."
        return "Retry once. If it fails again, post it through the assisted route (Reveal file + Copy caption)."
    if re.search(r"connection|unreachable|timed out|max retries", low):
        return "Check the internet connection and Retry."
    return "Retry once. If it fails again, post it through the assisted route (Reveal file + Copy caption)."
