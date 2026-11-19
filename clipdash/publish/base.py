from __future__ import annotations

import os
from dataclasses import dataclass, field

from .. import db


@dataclass
class PublishResult:
    status: str                      # live | scheduled | pending | private | draft | manual | failed
    url: str | None = None
    external_id: str | None = None
    detail: str = ""
    posted_at: str | None = None
    extra: dict = field(default_factory=dict)


def is_dry_run() -> bool:
    return os.environ.get("CLIPDASH_DRY_RUN") == "1" or bool(db.get_setting("dry_run", False))


class Publisher:
    platform = ""
    route = ""
    can_schedule_natively = False
    can_public = True
    notes = ""

    def __init__(self, account: dict):
        self.account = account

    # --- connection
    def connect(self) -> dict:
        """Start connecting the account. Returns {'auth_url': ...} or {'done': True, ...}."""
        return {"done": True}

    def verify(self) -> tuple[bool, str]:
        return True, "Ready"

    def options_schema(self) -> list[dict]:
        """Per-post options the UI must show for this route (e.g. TikTok privacy level with no default)."""
        return []

    # --- posting
    def publish(self, clip_path: str, post: dict, options: dict) -> PublishResult:
        raise NotImplementedError

    def check(self, post: dict) -> PublishResult | None:
        return None

    def stats(self, post: dict) -> dict | None:
        return None

    def upload_page(self) -> str:
        return {"youtube": "https://studio.youtube.com/channel/upload", "tiktok": "https://www.tiktok.com/upload",
                "instagram": "https://www.instagram.com/", "x": "https://x.com/compose/post"}.get(self.platform, "")


def route_info() -> dict:
    """Capabilities of each (platform, route) for the UI and docs."""
    from . import assisted, instagram_direct, service, tiktok_direct, youtube_direct
    out = {}
    for cls in (assisted.AssistedYouTube, assisted.AssistedTikTok, assisted.AssistedInstagram, assisted.AssistedX,
                youtube_direct.YouTubeDirect, tiktok_direct.TikTokDirect, instagram_direct.InstagramDirect,
                service.ServiceYouTube, service.ServiceTikTok, service.ServiceInstagram):
        out[f"{cls.platform}:{cls.route}"] = {"platform": cls.platform, "route": cls.route, "notes": cls.notes,
                                              "native_schedule": cls.can_schedule_natively, "public": cls.can_public}
    return out


def get_publisher(account: dict) -> Publisher:
    from . import assisted, instagram_direct, service, tiktok_direct, youtube_direct
    key = (account["platform"], account.get("route") or "assisted")
    table = {
        ("youtube", "assisted"): assisted.AssistedYouTube, ("tiktok", "assisted"): assisted.AssistedTikTok,
        ("instagram", "assisted"): assisted.AssistedInstagram, ("x", "assisted"): assisted.AssistedX,
        ("youtube", "direct"): youtube_direct.YouTubeDirect, ("tiktok", "direct"): tiktok_direct.TikTokDirect,
        ("instagram", "direct"): instagram_direct.InstagramDirect,
        ("youtube", "service"): service.ServiceYouTube, ("tiktok", "service"): service.ServiceTikTok,
        ("instagram", "service"): service.ServiceInstagram,
    }
    cls = table.get(key)
    if cls is None:
        raise ValueError(f"No publisher for {key[0]} via {key[1]}")
    return cls(account)
