"""Instagram Reels via the Instagram API with Facebook Login for Business (the only variant whose docs
allow uploading a local file with the resumable endpoint; the Instagram-Login variant needs a public video URL).

Checked against developers.facebook.com on 2026-11-19:
- Needs an Instagram professional account linked to a Facebook Page, and a Meta app of type Business.
- The app owner can publish to accounts that have a role on the app without App Review (Standard Access,
  Development mode). Other people's accounts need App Review + Business Verification.
- Flow: POST /{ig-user}/media (media_type=REELS, upload_type=resumable) → POST rupload.facebook.com/ig-api-upload/...
  → poll status_code until FINISHED → POST /{ig-user}/media_publish → GET permalink.
- Limit: 100 API posts per rolling 24 h (older docs say 50); the content_publishing_limit endpoint is authoritative.
- No scheduling parameter exists; this app's scheduler fires it while running.
- Reel specs: MP4/MOV, H.264, AAC, 23-60 fps, 9:16, max 15 min, max 300 MB. Insights metric is 'views'.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

from .. import config, db, secrets_store
from . import oauth_state
from .base import Publisher, PublishResult, is_dry_run

GRAPH = "https://graph.facebook.com/v21.0"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload/v21.0"
SCOPES = "instagram_basic,instagram_content_publish,instagram_manage_insights,pages_show_list,pages_read_engagement"


def redirect_uri() -> str:
    return f"http://localhost:{config.PORT}/oauth/callback/instagram/"


class InstagramDirect(Publisher):
    platform = "instagram"
    route = "direct"
    notes = ("Posts Reels through your own Meta app using Facebook Login for Business, which allows uploading the file "
             "directly. Your Instagram must be a professional account linked to a Facebook Page, and your account must "
             "have a role on the Meta app (no App Review needed for your own accounts). No scheduling on Instagram's side; "
             "this app's scheduler fires it while running. About 100 API posts per day.")

    def _tok_key(self) -> str:
        return f"instagram_token_{self.account['id']}"

    def _app(self) -> tuple[str, str]:
        a, s = secrets_store.get("facebook_app_id"), secrets_store.get("facebook_app_secret")
        if not a or not s:
            raise RuntimeError("Enter your Meta app ID and secret in Setup → Instagram first.")
        return a, s

    def connect(self) -> dict:
        app_id, _ = self._app()
        state = oauth_state.new_state(self.account["id"], {"platform": "instagram"})
        url = "https://www.facebook.com/v21.0/dialog/oauth?" + urlencode({
            "client_id": app_id, "redirect_uri": redirect_uri(), "state": state, "scope": SCOPES, "response_type": "code"})
        return {"auth_url": url, "redirect_uri": redirect_uri()}

    def finish_connect(self, code: str, pending: dict) -> dict:
        app_id, secret = self._app()
        r = requests.get(f"{GRAPH}/oauth/access_token", params={"client_id": app_id, "redirect_uri": redirect_uri(),
                                                                 "client_secret": secret, "code": code}, timeout=30).json()
        if "access_token" not in r:
            raise RuntimeError(f"Facebook token exchange failed: {r}")
        ll = requests.get(f"{GRAPH}/oauth/access_token", params={"grant_type": "fb_exchange_token", "client_id": app_id,
                                                                  "client_secret": secret, "fb_exchange_token": r["access_token"]},
                          timeout=30).json()
        token = ll.get("access_token", r["access_token"])
        pages = requests.get(f"{GRAPH}/me/accounts", params={"fields": "id,name,instagram_business_account{id,username}",
                                                              "access_token": token}, timeout=30).json()
        ig = None
        for p in pages.get("data", []):
            if p.get("instagram_business_account"):
                ig = p["instagram_business_account"]
                break
        if not ig:
            raise RuntimeError("No Instagram professional account is linked to a Facebook Page you manage.")
        secrets_store.set(self._tok_key(), json.dumps({"access_token": token, "ig_user_id": ig["id"], "obtained_at": time.time(),
                                                       "expires_in": ll.get("expires_in")}))
        db.update("accounts", self.account["id"], {"handle": ig.get("username"), "external_id": ig["id"]})
        return {"ok": True, "detail": f"Connected to @{ig.get('username')}"}

    def _tok(self) -> dict:
        raw = secrets_store.get(self._tok_key())
        if not raw:
            raise RuntimeError("Instagram account not connected. Click Connect on the account.")
        return json.loads(raw) if isinstance(raw, str) else raw

    def verify(self) -> tuple[bool, str]:
        if is_dry_run():
            return True, "Dry run: pretending the account is connected"
        try:
            t = self._tok()
            r = requests.get(f"{GRAPH}/{t['ig_user_id']}", params={"fields": "username", "access_token": t["access_token"]}, timeout=30).json()
            if "username" not in r:
                return False, f"Instagram token check failed: {r.get('error', r)}"
            lim = requests.get(f"{GRAPH}/{t['ig_user_id']}/content_publishing_limit",
                               params={"fields": "quota_usage,config", "access_token": t["access_token"]}, timeout=30).json()
            used = (lim.get("data") or [{}])[0]
            db.update("accounts", self.account["id"], {"handle": r["username"], "meta": {"quota": used}})
            q = used.get("quota_usage")
            return True, f"Connected to @{r['username']}" + (f" ({q} API posts used in the last 24h)" if q is not None else "")
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]

    def options_schema(self) -> list[dict]:
        return [{"key": "share_to_feed", "label": "Also show in Feed", "type": "bool", "default": True}]

    def publish(self, clip_path: str, post: dict, options: dict) -> PublishResult:
        if is_dry_run():
            return PublishResult(status="live", external_id=f"dry{post['id']}", url=f"https://www.instagram.com/reel/dry{post['id']}/",
                                 detail="Dry run: nothing was uploaded")
        t = self._tok()
        ig, token = t["ig_user_id"], t["access_token"]
        size = Path(clip_path).stat().st_size
        if size > 300 * 1024 * 1024:
            raise RuntimeError("Instagram Reels must be under 300 MB.")
        r = requests.post(f"{GRAPH}/{ig}/media", data={"media_type": "REELS", "upload_type": "resumable",
                                                      "caption": (post.get("post_text") or "")[:2200],
                                                      "share_to_feed": "true" if options.get("share_to_feed", True) else "false",
                                                      "access_token": token}, timeout=60).json()
        if "id" not in r:
            raise RuntimeError(f"Instagram container failed: {r.get('error', r)}")
        container = r["id"]
        with open(clip_path, "rb") as f:
            up = requests.post(f"{RUPLOAD}/{container}", headers={"Authorization": f"OAuth {token}", "offset": "0",
                                                                   "file_size": str(size)}, data=f, timeout=1800).json()
        if not up.get("success"):
            raise RuntimeError(f"Instagram upload failed: {up}")
        for _ in range(30):
            st = requests.get(f"{GRAPH}/{container}", params={"fields": "status_code,status", "access_token": token}, timeout=30).json()
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram processing failed: {st.get('status')}")
            time.sleep(10)
        else:
            raise RuntimeError("Instagram is still processing the video after 5 minutes; try again later.")
        pub = requests.post(f"{GRAPH}/{ig}/media_publish", data={"creation_id": container, "access_token": token}, timeout=60).json()
        if "id" not in pub:
            raise RuntimeError(f"Instagram publish failed: {pub.get('error', pub)}")
        media_id = pub["id"]
        perm = requests.get(f"{GRAPH}/{media_id}", params={"fields": "permalink", "access_token": token}, timeout=30).json()
        return PublishResult(status="live", external_id=media_id, url=perm.get("permalink"), detail="Published as a Reel")

    def stats(self, post: dict) -> dict | None:
        if not post.get("external_id") or is_dry_run():
            return None
        t = self._tok()
        r = requests.get(f"{GRAPH}/{post['external_id']}/insights", params={"metric": "views", "access_token": t["access_token"]},
                         timeout=30).json()
        for m in r.get("data", []):
            if m.get("name") == "views":
                vals = m.get("values") or []
                if vals:
                    return {"views": int(vals[0].get("value") or 0)}
        return None
