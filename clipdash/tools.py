"""External tools: ffmpeg (from the imageio-ffmpeg wheel), yt-dlp (as a module), TwitchDownloaderCLI
(downloaded on first use), Whisper models. Also the dependency checks shown on the Setup screen."""
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable

from . import config


class ToolError(RuntimeError):
    pass


def ffmpeg_path() -> str:
    """ffmpeg bundled with the imageio-ffmpeg pip package (works on macOS/Windows/Linux without Homebrew),
    with a PATH ffmpeg as fallback."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        p = shutil.which("ffmpeg")
        if p:
            return p
        raise ToolError("ffmpeg not found. Run the installer again (it installs the imageio-ffmpeg package).")


def _popen_kwargs() -> dict:
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw


def run(cmd: list[str], timeout: int | None = None, on_line: Callable[[str], None] | None = None,
        cwd: str | None = None) -> str:
    """Run a command and return stdout; raise ToolError with the stderr tail on failure.
    `ffmpeg` and `yt-dlp` at cmd[0] are resolved to the bundled binaries."""
    cmd = list(cmd)
    if cmd[0] == "ffmpeg":
        cmd[0] = ffmpeg_path()
    elif cmd[0] == "yt-dlp":
        cmd = [sys.executable, "-m", "yt_dlp", "--ffmpeg-location", ffmpeg_path(), "--no-warnings"] + cmd[1:]
    if on_line is None:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                           encoding="utf-8", errors="replace", **_popen_kwargs())
        if r.returncode != 0:
            raise ToolError(f"{Path(cmd[0]).name} failed:\n{r.stderr[-2500:] or r.stdout[-2500:]}")
        return r.stdout
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd,
                         encoding="utf-8", errors="replace", **_popen_kwargs())
    out: list[str] = []
    assert p.stdout is not None
    for line in p.stdout:
        out.append(line)
        try:
            on_line(line.rstrip())
        except Exception:
            pass
    p.wait(timeout=timeout)
    if p.returncode != 0:
        raise ToolError(f"{Path(cmd[0]).name} failed:\n{''.join(out)[-2500:]}")
    return "".join(out)


def ytdlp_json(url: str, extra: list[str] | None = None, timeout: int = 300) -> dict:
    out = run(["yt-dlp", "-J", "--skip-download", *(extra or []), url], timeout=timeout)
    return json.loads(out)


def media_duration(path: Path) -> float:
    """Duration in seconds via ffmpeg -i (imageio-ffmpeg ships no ffprobe)."""
    r = subprocess.run([ffmpeg_path(), "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **_popen_kwargs())
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if not m:
        raise ToolError(f"Could not read duration of {path.name}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def media_size(path: Path) -> tuple[int, int]:
    r = subprocess.run([ffmpeg_path(), "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **_popen_kwargs())
    m = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", r.stderr)
    if not m:
        raise ToolError(f"Could not read resolution of {path.name}")
    return int(m.group(1)), int(m.group(2))


# ---- TwitchDownloaderCLI ----

def tdcli_path() -> Path:
    return config.TOOLS / ("TwitchDownloaderCLI.exe" if os.name == "nt" else "TwitchDownloaderCLI")


def tdcli_asset_name() -> str:
    sysname = platform.system()
    arch = platform.machine().lower()
    if sysname == "Darwin":
        return "MacOSArm64" if arch in ("arm64", "aarch64") else "MacOS-x64"
    if sysname == "Windows":
        return "Windows-x64"
    return "LinuxArm64" if arch in ("arm64", "aarch64") else "Linux-x64"


_tdcli_lock = threading.Lock()


def install_tdcli(progress: Callable[[str], None] | None = None) -> Path:
    """Fetch the latest TwitchDownloaderCLI release asset for this OS/arch. Twitch's GQL endpoint rejects plain
    scripts with an integrity check; this tool is what works for chat replays. Serialised with a lock (two scans
    starting at once used to download over each other and extract a truncated zip), downloaded to a temp file,
    size-checked, and the binary is verified before it is used."""
    import requests
    say = progress or (lambda s: None)
    with _tdcli_lock:
        exe = tdcli_path()
        if exe.exists():
            return exe
        config.ensure_dirs()
        say("Looking up the latest TwitchDownloaderCLI release…")
        rel = requests.get("https://api.github.com/repos/lay295/TwitchDownloader/releases/latest", timeout=30).json()
        want = tdcli_asset_name()
        asset = next((a for a in rel.get("assets", []) if "CLI" in a["name"] and want in a["name"]), None)
        if not asset:
            raise ToolError(f"No TwitchDownloaderCLI build for {want} in release {rel.get('tag_name')}")
        zpath = config.TOOLS / f"tdcli-{os.getpid()}-{threading.get_ident()}.zip.part"
        say(f"Downloading {asset['name']} ({asset['size'] // 1_000_000} MB)…")
        try:
            with requests.get(asset["browser_download_url"], stream=True, timeout=600) as r:
                r.raise_for_status()
                with open(zpath, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            got = zpath.stat().st_size
            if asset.get("size") and got != asset["size"]:
                raise ToolError(f"TwitchDownloaderCLI download was incomplete ({got} of {asset['size']} bytes). Check the connection and retry.")
            try:
                with zipfile.ZipFile(zpath) as z:
                    z.extractall(config.TOOLS)
            except (zipfile.BadZipFile, EOFError) as e:
                raise ToolError(f"TwitchDownloaderCLI archive was corrupt ({got} bytes): {e}. Retry the scan.") from e
        finally:
            zpath.unlink(missing_ok=True)
        if not exe.exists():
            raise ToolError("Downloaded archive did not contain TwitchDownloaderCLI")
        if os.name != "nt":
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if platform.system() == "Darwin":
                subprocess.run(["xattr", "-d", "com.apple.quarantine", str(exe)], capture_output=True)
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, **_popen_kwargs())
        if r.returncode != 0:
            exe.unlink(missing_ok=True)
            raise ToolError(f"TwitchDownloaderCLI does not run on this computer: {(r.stderr or r.stdout)[-300:]}")
        say(f"TwitchDownloaderCLI installed ({r.stdout.strip().split(chr(10))[0]}).")
        return exe


# ---- Whisper ----

def whisper_model_present(name: str) -> bool:
    try:
        from huggingface_hub import scan_cache_dir
        info = scan_cache_dir()
        return any(r.repo_id == f"Systran/faster-whisper-{name}" for r in info.repos)
    except Exception:
        return False


def download_whisper_model(name: str, progress: Callable[[str], None] | None = None) -> None:
    say = progress or (lambda s: None)
    say(f"Downloading Whisper '{name}' model (a few hundred MB, once)…")
    from faster_whisper import WhisperModel
    WhisperModel(name, device="auto", compute_type="int8")
    say("Whisper model ready.")


# ---- dependency checks for the Setup screen ----

def check_dependencies() -> list[dict]:
    checks = []

    def add(name, ok, detail, fix=None, action=None):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "fix": fix, "action": action})

    add("Python", sys.version_info >= (3, 10), f"Python {platform.python_version()} at {sys.executable}",
        "Install Python 3.10 or newer from python.org and run the installer again.")
    try:
        p = ffmpeg_path()
        ver = subprocess.run([p, "-version"], capture_output=True, text=True, **_popen_kwargs()).stdout.split("\n")[0]
        has_ass = "--enable-libass" in subprocess.run([p, "-buildconf"], capture_output=True, text=True,
                                                      **_popen_kwargs()).stdout
        add("ffmpeg", True, f"{ver}" + ("" if has_ass else " (no libass: captions can't be burned in)"),
            None if has_ass else "This ffmpeg build lacks libass. Install ffmpeg from ffmpeg.org and put it on PATH.")
    except Exception as e:
        add("ffmpeg", False, str(e), "Run the installer again; it installs the imageio-ffmpeg package.")
    try:
        import yt_dlp
        add("yt-dlp", True, f"yt-dlp {yt_dlp.version.__version__}",
            "If downloads start failing, update: the installer upgrades it.")
    except Exception as e:
        add("yt-dlp", False, str(e), "Run the installer again.")
    try:
        import faster_whisper  # noqa: F401
        name = "small"
        try:
            from . import db
            name = db.get_setting("whisper_model")
        except Exception:
            pass
        present = whisper_model_present(name)
        add("Whisper captions", present, f"faster-whisper installed; '{name}' model " + ("cached" if present else "not downloaded yet"),
            None if present else "Click Download to fetch the caption model.", None if present else "download_whisper")
    except Exception as e:
        add("Whisper captions", False, str(e), "Run the installer again.")
    exe = tdcli_path()
    add("TwitchDownloaderCLI", exe.exists(), str(exe) if exe.exists() else "Needed only for Twitch chat replays",
        None if exe.exists() else "Click Download to fetch it for this computer.", None if exe.exists() else "install_tdcli")
    return checks
