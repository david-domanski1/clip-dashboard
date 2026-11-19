"""Assisted route: always available. The app copies the final clip and caption into an outbox folder,
reveals it, opens the platform's upload page, and the user pastes the post link back."""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config
from .base import Publisher, PublishResult

OUTBOX = config.DATA / "outbox"


class Assisted(Publisher):
    route = "assisted"
    notes = "You upload in the browser or the app. Clip Dashboard reveals the file, copies the caption and opens the upload page; paste the link back to start the submission timer."

    def publish(self, clip_path: str, post: dict, options: dict) -> PublishResult:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        src = Path(clip_path)
        name = f"{post['platform']}_{post['id']}_{src.stem}.mp4"
        dst = OUTBOX / name
        if not dst.exists():
            shutil.copyfile(src, dst)
        text = post.get("post_text") or ""
        if post.get("title"):
            text = f"{post['title']}\n\n{text}"
        (OUTBOX / (dst.stem + ".txt")).write_text(text + "\n", encoding="utf-8")
        return PublishResult(status="manual", detail=f"Ready in outbox: {name}. Upload it on {self.platform}, then paste the link.",
                             extra={"outbox_file": str(dst), "upload_page": self.upload_page()})


class AssistedYouTube(Assisted):
    platform = "youtube"


class AssistedTikTok(Assisted):
    platform = "tiktok"


class AssistedInstagram(Assisted):
    platform = "instagram"


class AssistedX(Assisted):
    platform = "x"
