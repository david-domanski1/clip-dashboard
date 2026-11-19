"""Posting-service route: one adapter for an audited third-party publishing API.

Candidate implemented: Zernio (formerly Late, getlate.dev). Chosen as the default candidate because the first two
connected accounts are free, posts are unlimited, local files upload via a presigned URL, and the create-post
response returns the platform post URL. The user confirms the choice before paying for a third account.
Read from docs.zernio.com on 2026-11-19 (not exercised against a live key: see README "What was not verified").

Flow: POST /v1/media/presign → PUT bytes → POST /v1/posts {content, mediaItems, platforms[{platform, accountId,
platformSpecificData}], publishNow | scheduledFor+timezone} → GET /v1/posts/{id} for platformPostUrl → GET /v1/analytics.
"""
from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

import requests

from .. import db, secrets_store
from .base import Publisher, PublishResult, is_dry_run

DEFAULT_BASE = "https://zernio.com/api/v1"


def _base() -> str:
    return (db.get_setting("service_base_url") or DEFAULT_BASE).rstrip("/")


def _key() -> str:
    k = secrets_store.get("service_api_key")
    if not k:
        raise RuntimeError("Enter your posting-service API key in Setup → Posting service first.")
    return k


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}


def list_service_accounts() -> list[dict]:
    if is_dry_run():
        return [{"_id": "dry_yt", "platform": "youtube", "username": "dry-run"}, {"_id": "dry_tt", "platform": "tiktok", "username": "dry-run"},
                {"_id": "dry_ig", "platform": "instagram", "username": "dry-run"}]
    r = requests.get(f"{_base()}/accounts", headers=_headers(), params={"status": "connected"}, timeout=30)
    if r.status_code == 401:
        raise RuntimeError("The posting service rejected the API key.")
    r.raise_for_status()
    return r.json().get("accounts", [])


def connect_url(platform: str) -> str:
    """URL where the user connects a social account on the service's side (needs a profile)."""
    profiles = requests.get(f"{_base()}/profiles", headers=_headers(), timeout=30).json()
    plist = profiles if isinstance(profiles, list) else profiles.get("profiles", [])
    if plist:
        pid = plist[0].get("_id")
    else:
        pid = requests.post(f"{_base()}/profiles", headers=_headers(), json={"name": "Clip Dashboard"}, timeout=30).json()["profile"]["_id"]
    r = requests.get(f"{_base()}/connect/{platform}", headers=_headers(), params={"profileId": pid}, timeout=30).json()
    return r.get("authUrl") or r.get("url") or ""


class ServiceBase(Publisher):
    route = "service"
    notes = ("Posts through an audited third-party publishing API (Zernio). You connect your accounts on their dashboard; "
             "this app calls their API with your key. Public posting and scheduling are handled on their side, so scheduled "
             "posts fire even if this app is closed. First two accounts are free.")
    can_schedule_natively = True

    def connect(self) -> dict:
        accounts = list_service_accounts()
        mine = [a for a in accounts if a.get("platform") == self.platform]
        if not mine:
            try:
                url = connect_url(self.platform)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"No {self.platform} account connected on the service yet and no connect link: {e}")
            return {"auth_url": url, "detail": "Connect the account on the service's page, then click Test."}
        a = mine[0]
        db.update("accounts", self.account["id"], {"external_id": a.get("_id"), "handle": a.get("username") or a.get("name")})
        return {"done": True, "ok": True, "detail": f"Linked to service account {a.get('username') or a.get('_id')}"}

    def verify(self) -> tuple[bool, str]:
        try:
            accounts = list_service_accounts()
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]
        ext = self.account.get("external_id")
        match = next((a for a in accounts if a.get("_id") == ext), None) or \
            next((a for a in accounts if a.get("platform") == self.platform), None)
        if not match:
            return False, f"No connected {self.platform} account on the service. Connect it on their dashboard."
        db.update("accounts", self.account["id"], {"external_id": match.get("_id"), "handle": match.get("username") or match.get("name")})
        return True, f"Service account {match.get('username') or match.get('_id')} is connected"

    def _platform_data(self, post: dict, options: dict) -> dict:
        return {}

    def _root_extra(self, options: dict) -> dict:
        return {}

    def publish(self, clip_path: str, post: dict, options: dict) -> PublishResult:
        if is_dry_run():
            return PublishResult(status="scheduled" if post.get("scheduled_at") else "live", external_id=f"svc_dry_{post['id']}",
                                 url=f"https://example.invalid/{self.platform}/dry{post['id']}", detail="Dry run: nothing was sent to the service")
        acct = self.account.get("external_id")
        if not acct:
            ok, detail = self.verify()
            if not ok:
                raise RuntimeError(detail)
            acct = db.get("accounts", self.account["id"])["external_id"]
        ctype = mimetypes.guess_type(clip_path)[0] or "video/mp4"
        p = Path(clip_path)
        pre = requests.post(f"{_base()}/media/presign", headers=_headers(),
                            json={"filename": p.name, "contentType": ctype, "size": p.stat().st_size}, timeout=30)
        pre.raise_for_status()
        pre = pre.json()
        with open(clip_path, "rb") as f:
            up = requests.put(pre["uploadUrl"], data=f, headers={"Content-Type": ctype}, timeout=1800)
        if up.status_code >= 300:
            raise RuntimeError(f"Service upload failed ({up.status_code})")
        body = {"content": post.get("post_text") or "", "mediaItems": [{"url": pre["publicUrl"], "type": "video"}],
                "platforms": [{"platform": self.platform, "accountId": acct, "platformSpecificData": self._platform_data(post, options)}]}
        body.update(self._root_extra(options))
        if post.get("scheduled_at"):
            body["scheduledFor"] = post["scheduled_at"]
            if post.get("tz"):
                body["timezone"] = post["tz"]
        else:
            body["publishNow"] = True
        r = requests.post(f"{_base()}/posts", headers={**_headers(), "Idempotency-Key": str(uuid.uuid4())}, json=body, timeout=120)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(f"Service refused the post ({r.status_code}): {data.get('error') or data}")
        postobj = data.get("post") or {}
        pid = postobj.get("_id")
        return self._from_post(postobj, pid, scheduled=bool(post.get("scheduled_at")))

    def _from_post(self, postobj: dict, pid: str | None, scheduled: bool) -> PublishResult:
        plat = next((x for x in postobj.get("platforms", []) if x.get("platform") == self.platform), {})
        st = (plat.get("status") or postobj.get("status") or "").lower()
        url = plat.get("platformPostUrl")
        if st in ("published",) or url:
            return PublishResult(status="live", external_id=pid, url=url, detail="Published by the service")
        if st in ("failed", "cancelled"):
            return PublishResult(status="failed", external_id=pid, detail=plat.get("errorMessage") or "Service reported failure")
        if scheduled or st == "scheduled":
            return PublishResult(status="scheduled", external_id=pid, detail="Scheduled on the service (fires without this app)")
        return PublishResult(status="pending", external_id=pid, detail=f"Service status: {st or 'pending'}")

    def check(self, post: dict) -> PublishResult | None:
        if not post.get("external_id") or is_dry_run():
            return None
        r = requests.get(f"{_base()}/posts/{post['external_id']}", headers=_headers(), timeout=30)
        if r.status_code == 404:
            return PublishResult(status="failed", detail="Post no longer exists on the service")
        r.raise_for_status()
        return self._from_post(r.json().get("post") or {}, post["external_id"], scheduled=post["status"] == "scheduled")

    def stats(self, post: dict) -> dict | None:
        if not post.get("external_id") or is_dry_run():
            return None
        r = requests.get(f"{_base()}/analytics", headers=_headers(), params={"postId": post["external_id"], "platform": self.platform}, timeout=30)
        if r.status_code != 200:
            return None
        for p in r.json().get("posts", []):
            a = p.get("analytics") or {}
            if "views" in a:
                return {"views": int(a["views"] or 0)}
        return None


class ServiceYouTube(ServiceBase):
    platform = "youtube"

    def options_schema(self) -> list[dict]:
        return [{"key": "visibility", "label": "Visibility", "type": "select", "default": "public",
                 "choices": [["public", "Public"], ["unlisted", "Unlisted"], ["private", "Private"]]}]

    def _platform_data(self, post: dict, options: dict) -> dict:
        return {"title": (post.get("title") or "")[:100], "visibility": options.get("visibility", "public"),
                "categoryId": "20", "madeForKids": False}


class ServiceTikTok(ServiceBase):
    platform = "tiktok"

    def options_schema(self) -> list[dict]:
        return [{"key": "privacy_level", "label": "Who can view", "type": "select", "default": None,
                 "choices": [["PUBLIC_TO_EVERYONE", "Everyone"], ["MUTUAL_FOLLOW_FRIENDS", "Friends"], ["SELF_ONLY", "Only me"]]},
                {"key": "allow_comment", "label": "Allow comments", "type": "bool", "default": False},
                {"key": "allow_duet", "label": "Allow Duet", "type": "bool", "default": False},
                {"key": "allow_stitch", "label": "Allow Stitch", "type": "bool", "default": False},
                {"key": "_consent", "type": "note", "label": "By posting you confirm you previewed the clip and agree to TikTok's Music Usage Confirmation."}]

    def _root_extra(self, options: dict) -> dict:
        if not options.get("privacy_level"):
            raise RuntimeError("Pick a TikTok privacy level.")
        return {"tiktokSettings": {"privacy_level": options["privacy_level"], "allow_comment": bool(options.get("allow_comment")),
                                   "allow_duet": bool(options.get("allow_duet")), "allow_stitch": bool(options.get("allow_stitch")),
                                   "content_preview_confirmed": True, "express_consent_given": True}}


class ServiceInstagram(ServiceBase):
    platform = "instagram"

    def options_schema(self) -> list[dict]:
        return [{"key": "share_to_feed", "label": "Also show in Feed", "type": "bool", "default": True}]

    def _platform_data(self, post: dict, options: dict) -> dict:
        return {"shareToFeed": bool(options.get("share_to_feed", True))}
