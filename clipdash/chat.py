"""Chat replays: YouTube live chat via yt-dlp subtitles, Twitch via TwitchDownloaderCLI.
Rows are stored as compact [t_seconds, message] lists."""
import json
from pathlib import Path

from . import tools


def download_youtube_chat(url: str, out: Path) -> bool:
    """Stream VODs on YouTube expose their chat replay as a 'live_chat' subtitle track. Regular uploads
    have none and return False."""
    d = out.parent
    stem = d / "ytchat"
    for f in d.glob("ytchat*.live_chat.json"):
        f.unlink()
    try:
        tools.run(["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat",
                   "-o", str(stem), url], timeout=3600)
    except tools.ToolError:
        pass
    files = list(d.glob("ytchat*.live_chat.json"))
    if not files:
        return False
    rows = []
    with open(files[0], encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            t_ms = item.get("videoOffsetTimeMsec")
            if t_ms is None:
                continue
            try:
                r = item["replayChatItemAction"]["actions"][0]["addChatItemAction"]["item"]
                msg = r.get("liveChatTextMessageRenderer") or r.get("liveChatPaidMessageRenderer")
                text = "".join(x.get("text", "") or x.get("emoji", {}).get("emojiId", "")
                               for x in (msg or {}).get("message", {}).get("runs", []))
            except (KeyError, IndexError, TypeError):
                continue
            rows.append([round(float(t_ms) / 1000.0, 2), text])
    files[0].unlink(missing_ok=True)
    out.write_text(json.dumps(rows))
    return True


def download_twitch_chat(vod_id: str, out: Path, on_line=None) -> bool:
    exe = tools.tdcli_path()
    if not exe.exists():
        tools.install_tdcli(on_line)
    raw = out.with_suffix(".tdcli.json")
    tools.run([str(exe), "chatdownload", "-u", vod_id, "-o", str(raw), "--compression", "None"],
              timeout=7200, on_line=on_line)
    data = json.loads(raw.read_text(encoding="utf-8", errors="replace"))
    rows = [[round(float(c["content_offset_seconds"]), 2), (c.get("message") or {}).get("body", "")]
            for c in data.get("comments", [])]
    raw.unlink(missing_ok=True)
    out.write_text(json.dumps(rows))
    return True


def load(path: Path) -> list[list]:
    if not path.exists():
        return []
    return json.loads(path.read_text())
