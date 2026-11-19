"""Disk accounting and cache pruning. Segments are large (100-300 MB each) and VODs are long."""
import os
import shutil
from pathlib import Path

from . import config


def dir_size(p: Path) -> int:
    total = 0
    if not p.exists():
        return 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def usage() -> dict:
    config.ensure_dirs()
    du = shutil.disk_usage(config.DATA)
    return {
        "cache_bytes": dir_size(config.CACHE),
        "clips_bytes": dir_size(config.CLIPS),
        "tools_bytes": dir_size(config.TOOLS) + dir_size(config.MODELS),
        "free_bytes": du.free,
        "total_bytes": du.total,
        "data_dir": str(config.DATA),
    }


def prune_cache(cap_bytes: int) -> int:
    """Delete oldest cache files until the cache is under cap_bytes. Returns bytes freed."""
    files = []
    for root, _d, fs in os.walk(config.CACHE):
        for f in fs:
            p = Path(root) / f
            try:
                files.append((p.stat().st_mtime, p.stat().st_size, p))
            except OSError:
                pass
    total = sum(s for _m, s, _p in files)
    freed = 0
    for _m, s, p in sorted(files):
        if total - freed <= cap_bytes:
            break
        try:
            p.unlink()
            freed += s
        except OSError:
            pass
    return freed


def remove_tree(p: Path) -> None:
    shutil.rmtree(p, ignore_errors=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"
