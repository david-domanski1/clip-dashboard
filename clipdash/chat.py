"""Chat replays: YouTube live chat via yt-dlp subtitles, Twitch via TwitchDownloaderCLI (optionally a time window).
Rows are stored as compact [t_seconds, message] lists."""
import json
from pathlib import Path

from . import tools


def download_youtube_chat(url: str, out: Path) -> bool:
    """Stream VODs on YouTube expose their chat replay as a 'live_chat' subtitle track (whole stream only;
    yt-dlp has no time-window option, so it is downloaded once and cached). Regular uploads have none → False."""
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


def download_twitch_chat(vod_id: str, out: Path, start: float | None = None, end: float | None = None, on_line=None) -> bool:
    """Chat for a VOD, or just [start, end] seconds of it (-b/-e). A 2-hour window takes seconds; a 29-hour VOD ~10 minutes.
    Works on VODs that are still being streamed."""
    exe = tools.tdcli_path()
    if not exe.exists():
        tools.install_tdcli(on_line)
    raw = out.with_suffix(".tdcli.json")
    raw.unlink(missing_ok=True)
    cmd = [str(exe), "chatdownload", "-u", vod_id, "-o", str(raw), "--compression", "None",
           "--collision", "Overwrite", "--banner", "false"]
    if start is not None and start > 0:
        cmd += ["-b", f"{int(start)}s"]
    if end is not None:
        cmd += ["-e", f"{int(end)}s"]
    tail: list[str] = []

    def collect(line: str) -> None:
        line = line.strip()
        if line:
            tail.append(line[-160:])
            del tail[:-6]
        if on_line:
            on_line(line)

    tools.run(cmd, timeout=7200, on_line=collect)
    if not raw.exists():
        raise tools.ToolError("TwitchDownloaderCLI finished without writing the chat file (was the video deleted mid-scan?). "
                              "Last output: " + " | ".join(tail))
    try:
        data = json.loads(raw.read_text(encoding="utf-8", errors="replace"))
    except ValueError as e:
        raise tools.ToolError(f"TwitchDownloaderCLI wrote an incomplete chat file ({raw.stat().st_size} bytes): {e}. "
                              "Last output: " + " | ".join(tail)) from e
    rows = [[round(float(c["content_offset_seconds"]), 2), (c.get("message") or {}).get("body", "")]
            for c in data.get("comments", [])]
    raw.unlink(missing_ok=True)
    out.write_text(json.dumps(rows))
    return True


def load(path: Path) -> list[list]:
    if not path.exists():
        return []
    return json.loads(path.read_text())
