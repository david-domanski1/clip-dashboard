"""TikTok Content Posting API with the user's own developer app (Desktop platform, PKCE).

Checked against developers.tiktok.com on 2026-11-19:
- Unaudited clients can only post with privacy SELF_ONLY, and the TikTok account must be private.
  The video then has to be switched to "Everyone" inside the TikTok app.
- The app must query creator info first and offer exactly the privacy options it returns, with no default.
- Upload-to-inbox (video.upload scope) sends the video as a draft the creator finishes in the TikTok app;
  the docs do not state whether unaudited apps are exempt from the private rule there. Not verified.
- Access tokens last 2 hours, refresh tokens 180 days; each refresh rotates the refresh token.
- Rate limit 6 init calls/min per user; post ID is only returned once a public post passes moderation.
- The desktop OAuth doc describes code_challenge as hex-encoded SHA-256 (not RFC 7636 base64url). Both are
  supported here via the 'tiktok_pkce_hex' setting (default on, per the docs).
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets as pysecrets
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from .. import config, db, secrets_store
from . import oauth_state
from .base import Publisher, PublishResult, is_dry_run

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.publish,video.upload"


def redirect_uri() -> str:
    return f"http://127.0.0.1:{config.PORT}/oauth/callback/tiktok/"


class TikTokDirect(Publisher):
    platform = "tiktok"
    route = "direct"
    can_public = False
    notes = ("Posts through your own TikTok developer app. Until TikTok audits the app, posts can only be SELF_ONLY "
             "(and the account must be private): the clip lands on your profile as 'only me' and you flip it to Everyone "
             "in the TikTok app. The 'Send to inbox as draft' option hands the video to the TikTok app to finish there. "
             "No scheduling on TikTok's side; this app's scheduler fires it while running.")

    def _tok_key(self) -> str:
        return f"tiktok_token_{self.account['id']}"

    def _client(self) -> tuple[str, str]:
        k, s = secrets_store.get("tiktok_client_key"), secrets_store.get("tiktok_client_secret")
        if not k or not s:
            raise RuntimeError("Enter your TikTok app's client key and secret in Setup → TikTok first.")
        return k, s

    def connect(self) -> dict:
        key, _ = self._client()
        verifier = pysecrets.token_urlsafe(64)[:96]
        digest = hashlib.sha256(verifier.encode()).digest()
        if db.get_setting("tiktok_pkce_hex", True):
            challenge = hashlib.sha256(verifier.encode()).hexdigest()
        else:
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        state = oauth_state.new_state(self.account["id"], {"verifier": verifier, "platform": "tiktok"})
        url = AUTH_URL + "?" + urlencode({"client_key": key, "response_type": "code", "scope": SCOPES,
                                          "redirect_uri": redirect_uri(), "state": state,
                                          "code_challenge": challenge, "code_challenge_method": "S256"})
        return {"auth_url": url, "redirect_uri": redirect_uri()}

    def finish_connect(self, code: str, pending: dict) -> dict:
        key, secret = self._client()
        r = requests.post(TOKEN_URL, data={"client_key": key, "client_secret": secret, "code": code,
                                           "grant_type": "authorization_code", "redirect_uri": redirect_uri(),
                                           "code_verifier": pending["verifier"]}, timeout=30)
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"TikTok token exchange failed: {data}")
        data["obtained_at"] = time.time()
        secrets_store.set(self._tok_key(), json.dumps(data))
        ok, detail = self.verify()
        return {"ok": ok, "detail": detail}

    def _token(self) -> str:
        raw = secrets_store.get(self._tok_key())
        if not raw:
            raise RuntimeError("TikTok account not connected. Click Connect on the account.")
        data = json.loads(raw) if isinstance(raw, str) else raw
        if time.time() - data.get("obtained_at", 0) > data.get("expires_in", 7200) - 120:
            key, secret = self._client()
            r = requests.post(TOKEN_URL, data={"client_key": key, "client_secret": secret, "grant_type": "refresh_token",
                                               "refresh_token": data["refresh_token"]}, timeout=30)
            new = r.json()
            if "access_token" not in new:
                raise RuntimeError(f"TikTok token refresh failed, reconnect the account: {new}")
            new["obtained_at"] = time.time()
            secrets_store.set(self._tok_key(), json.dumps(new))
            data = new
        return data["access_token"]

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json; charset=UTF-8"}

    def creator_info(self) -> dict:
        if is_dry_run():
            return {"creator_nickname": "dry-run", "creator_username": "dryrun",
                    "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                    "max_video_post_duration_sec": 600, "comment_disabled": False, "duet_disabled": False, "stitch_disabled": False}
        r = requests.post(f"{API}/post/publish/creator_info/query/", headers=self._headers(), timeout=30).json()
        if r.get("error", {}).get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok creator info: {r['error']}")
        return r["data"]

    def verify(self) -> tuple[bool, str]:
        try:
            ci = self.creator_info()
            db.update("accounts", self.account["id"], {"handle": ci.get("creator_username"), "meta": {
                "nickname": ci.get("creator_nickname"), "privacy_level_options": ci.get("privacy_level_options"),
                "max_video_post_duration_sec": ci.get("max_video_post_duration_sec"),
                "comment_disabled": ci.get("comment_disabled"), "duet_disabled": ci.get("duet_disabled"),
                "stitch_disabled": ci.get("stitch_disabled")}})
            return True, f"Connected as {ci.get('creator_nickname')} (@{ci.get('creator_username')})"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]

    def options_schema(self) -> list[dict]:
        meta = self.account.get("meta") or {}
        opts = meta.get("privacy_level_options") or ["SELF_ONLY"]
        labels = {"PUBLIC_TO_EVERYONE": "Everyone", "MUTUAL_FOLLOW_FRIENDS": "Friends", "FOLLOWER_OF_CREATOR": "Followers",
                  "SELF_ONLY": "Only me (required while the app is unaudited)"}
        return [
            {"key": "mode", "label": "How to post", "type": "select", "default": "direct",
             "choices": [["direct", "Direct post"], ["inbox", "Send to TikTok inbox as a draft"]]},
            {"key": "privacy_level", "label": "Who can view (TikTok requires you to pick)", "type": "select", "default": None,
             "choices": [[o, labels.get(o, o)] for o in opts], "required_for": {"mode": "direct"}},
            {"key": "allow_comment", "label": "Allow comments", "type": "bool", "default": False, "disabled": bool(meta.get("comment_disabled"))},
            {"key": "allow_duet", "label": "Allow Duet", "type": "bool", "default": False, "disabled": bool(meta.get("duet_disabled"))},
            {"key": "allow_stitch", "label": "Allow Stitch", "type": "bool", "default": False, "disabled": bool(meta.get("stitch_disabled"))},
            {"key": "commercial", "label": "Content discloses commercial content", "type": "bool", "default": False},
            {"key": "brand_organic", "label": "Your brand (promotes yourself)", "type": "bool", "default": False, "requires": "commercial"},
            {"key": "branded_content", "label": "Branded content (paid partnership)", "type": "bool", "default": False, "requires": "commercial"},
            {"key": "_consent", "type": "note", "label": "By posting, you agree to TikTok's Music Usage Confirmation (and Branded Content Policy if branded)."},
            {"key": "_nick", "type": "note", "label": f"Posting as {meta.get('nickname') or self.account.get('handle') or 'this account'}. It can take a few minutes for a post to appear on your profile."},
        ]

    def _upload(self, init_path: str, body: dict, clip_path: str) -> str:
        size = Path(clip_path).stat().st_size
        chunk = size if size < 64 * 1024 * 1024 else 10 * 1024 * 1024
        count = 1 if size < 5 * 1024 * 1024 or chunk == size else max(1, size // chunk)
        body["source_info"] = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}
        r = requests.post(f"{API}{init_path}", headers=self._headers(), json=body, timeout=60).json()
        err = r.get("error", {})
        if err.get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok init failed: {err.get('code')}: {err.get('message')}")
        publish_id, upload_url = r["data"]["publish_id"], r["data"]["upload_url"]
        with open(clip_path, "rb") as f:
            sent = 0
            for i in range(count):
                n = chunk if i < count - 1 else size - sent
                data = f.read(n)
                h = {"Content-Type": "video/mp4", "Content-Length": str(len(data)),
                     "Content-Range": f"bytes {sent}-{sent + len(data) - 1}/{size}"}
                resp = requests.put(upload_url, headers=h, data=data, timeout=600)
                if resp.status_code not in (200, 201, 206):
                    raise RuntimeError(f"TikTok chunk upload failed ({resp.status_code}): {resp.text[:200]}")
                sent += len(data)
        return publish_id

    def publish(self, clip_path: str, post: dict, options: dict) -> PublishResult:
        mode = options.get("mode", "direct")
        if is_dry_run():
            st = "draft" if mode == "inbox" else ("live" if options.get("privacy_level") == "PUBLIC_TO_EVERYONE" else "private")
            return PublishResult(status=st, external_id=f"dry{post['id']}", detail="Dry run: nothing was uploaded")
        ci = self.creator_info()
        max_dur = ci.get("max_video_post_duration_sec")
        if mode == "inbox":
            pid = self._upload("/post/publish/inbox/video/init/", {}, clip_path)
            return PublishResult(status="draft", external_id=pid,
                                 detail="Sent to your TikTok inbox. Open the TikTok app notification, add the caption, and post.")
        privacy = options.get("privacy_level")
        if not privacy or privacy not in (ci.get("privacy_level_options") or []):
            raise RuntimeError("Pick a TikTok privacy level from the options TikTok returned for this account.")
        if options.get("commercial") and not (options.get("brand_organic") or options.get("branded_content")):
            raise RuntimeError("Commercial content is on: choose Your brand, Branded content, or both.")
        body = {"post_info": {
            "title": (post.get("post_text") or "")[:2200], "privacy_level": privacy,
            "disable_comment": not options.get("allow_comment", False),
            "disable_duet": not options.get("allow_duet", False),
            "disable_stitch": not options.get("allow_stitch", False),
            "brand_content_toggle": bool(options.get("branded_content")),
            "brand_organic_toggle": bool(options.get("brand_organic")),
            "is_aigc": False,
        }}
        if max_dur and post.get("duration") and post["duration"] > max_dur:
            raise RuntimeError(f"This TikTok account can post at most {max_dur}s videos.")
        pid = self._upload("/post/publish/video/init/", body, clip_path)
        return PublishResult(status="pending", external_id=pid, detail="Uploaded; TikTok is processing it.",
                             extra={"privacy_level": privacy})

    def check(self, post: dict) -> PublishResult | None:
        pid = post.get("external_id")
        if not pid:
            return None
        if is_dry_run():
            return None
        r = requests.post(f"{API}/post/publish/status/fetch/", headers=self._headers(), json={"publish_id": pid}, timeout=30).json()
        data = r.get("data") or {}
        st = data.get("status")
        username = self.account.get("handle") or ""
        if st == "FAILED":
            return PublishResult(status="failed", external_id=pid, detail=f"TikTok failed: {data.get('fail_reason')}")
        if st == "SEND_TO_USER_INBOX":
            return PublishResult(status="draft", external_id=pid, detail="Waiting for you to finish the post in the TikTok app")
        if st == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
            if ids:
                url = f"https://www.tiktok.com/@{username}/video/{ids[0]}"
                return PublishResult(status="live", external_id=pid, url=url, detail="Public on TikTok")
            privacy = (post.get("options") or {}).get("privacy_level")
            if privacy == "PUBLIC_TO_EVERYONE":
                return PublishResult(status="pending", external_id=pid, detail="Posted; waiting for TikTok moderation to assign a public ID")
            return PublishResult(status="private", external_id=pid,
                                 detail="Posted as 'Only me'. In the TikTok app open the video → Privacy settings → Everyone, then paste its link here.")
        return PublishResult(status="pending", external_id=pid, detail=f"TikTok status: {st}")

    def stats(self, post: dict) -> dict | None:
        """View counts via the Display API need the video.list scope and only cover public videos."""
        return None
