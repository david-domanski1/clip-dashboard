"""YouTube Data API v3 with the user's own OAuth desktop client.

Checked against the official docs on 2026-11-19:
- Since 2 Nov 2026 uploads from unverified API projects are no longer forced private; privacyStatus
  defaults to public, so it is always set explicitly here.
- Scheduling is native: status.publishAt with privacyStatus=private.
- Default quota: 100 videos.insert calls per day (1 unit each in their own bucket).
- An OAuth consent screen in "Testing" mode expires refresh tokens after 7 days; the Setup screen says so.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import db, secrets_store
from .base import Publisher, PublishResult, is_dry_run

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


class YouTubeDirect(Publisher):
    platform = "youtube"
    route = "direct"
    can_schedule_natively = True
    notes = ("Uploads through your own Google Cloud OAuth client. Public uploads are allowed for unverified projects "
             "(policy changed 2 Nov 2026). Scheduling uses YouTube's own publishAt, so it fires even if this app is closed. "
             "If your OAuth consent screen is in Testing mode you must reconnect every 7 days.")

    def _token_key(self) -> str:
        return f"youtube_token_{self.account['id']}"

    def _client_config(self) -> dict:
        raw = secrets_store.get("youtube_client_secret")
        if not raw:
            raise RuntimeError("Paste your Google OAuth client JSON (Desktop app) in Setup → YouTube first.")
        return json.loads(raw) if isinstance(raw, str) else raw

    def _creds(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        tok = secrets_store.get(self._token_key())
        if not tok:
            raise RuntimeError("YouTube account not connected. Click Connect on the account.")
        creds = Credentials.from_authorized_user_info(json.loads(tok) if isinstance(tok, str) else tok, SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                secrets_store.set(self._token_key(), creds.to_json())
            else:
                raise RuntimeError("YouTube token expired. Reconnect the account (Testing-mode consent screens expire after 7 days).")
        return creds

    def _service(self):
        from googleapiclient.discovery import build
        return build("youtube", "v3", credentials=self._creds(), cache_discovery=False)

    def connect(self) -> dict:
        """Runs Google's loopback flow: opens the browser, waits for consent. Called from a background job."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_config(self._client_config(), SCOPES)
        creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True, timeout_seconds=300,
                                      authorization_prompt_message="")
        secrets_store.set(self._token_key(), creds.to_json())
        ok, detail = self.verify()
        return {"done": True, "ok": ok, "detail": detail}

    def verify(self) -> tuple[bool, str]:
        if is_dry_run():
            return True, "Dry run: pretending the channel is connected"
        try:
            yt = self._service()
            r = yt.channels().list(part="snippet", mine=True).execute()
            items = r.get("items") or []
            if not items:
                return False, "Signed in, but this Google account has no YouTube channel"
            title = items[0]["snippet"]["title"]
            db.update("accounts", self.account["id"], {"handle": title, "external_id": items[0]["id"]})
            return True, f"Connected to channel “{title}”"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]

    def options_schema(self) -> list[dict]:
        return [{"key": "privacy", "label": "Visibility", "type": "select", "default": "public",
                 "choices": [["public", "Public"], ["unlisted", "Unlisted"], ["private", "Private"]]}]

    @staticmethod
    def build_body(post: dict, options: dict) -> dict:
        scheduled = post.get("scheduled_at")
        privacy = options.get("privacy", "public")
        body = {
            "snippet": {"title": (post.get("title") or post.get("post_text") or "Clip")[:100],
                        "description": (post.get("post_text") or "")[:5000], "categoryId": "20",
                        "tags": [t.lstrip("#") for t in (options.get("tags") or [])][:20]},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        if options.get("synthetic"):
            body["status"]["containsSyntheticMedia"] = True   # "altered or synthetic content" disclosure
        if scheduled:
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = _rfc3339(scheduled)
        return body

    def publish(self, clip_path: str, post: dict, options: dict) -> PublishResult:
        scheduled = post.get("scheduled_at")
        privacy = options.get("privacy", "public")
        body = self.build_body(post, options)
        if is_dry_run():
            vid = f"dry{post['id']:08d}"
            return PublishResult(status="scheduled" if scheduled else "live", url=f"https://youtube.com/shorts/{vid}",
                                 external_id=vid, detail="Dry run: nothing was uploaded")
        from googleapiclient.http import MediaFileUpload
        yt = self._service()
        req = yt.videos().insert(part="snippet,status", body=body,
                                 media_body=MediaFileUpload(clip_path, chunksize=8 * 1024 * 1024, resumable=True))
        resp = None
        while resp is None:
            _status, resp = req.next_chunk()
        vid = resp["id"]
        url = f"https://youtube.com/shorts/{vid}"
        if scheduled:
            return PublishResult(status="scheduled", url=url, external_id=vid,
                                 detail=f"Uploaded as private; YouTube publishes it at {scheduled} (native schedule).")
        if privacy != "public":
            return PublishResult(status="private", url=url, external_id=vid,
                                 detail=f"Uploaded as {privacy}. Finish in YouTube Studio: https://studio.youtube.com/video/{vid}/edit")
        return PublishResult(status="live", url=url, external_id=vid, detail="Uploaded and public")

    def check(self, post: dict) -> PublishResult | None:
        if not post.get("external_id"):
            return None
        if is_dry_run():
            return PublishResult(status="live", url=post.get("url"), external_id=post["external_id"], detail="Dry run")
        yt = self._service()
        r = yt.videos().list(part="status,statistics", id=post["external_id"]).execute()
        items = r.get("items") or []
        if not items:
            return PublishResult(status="failed", detail="Video no longer exists on YouTube")
        st = items[0]["status"]
        views = int((items[0].get("statistics") or {}).get("viewCount") or 0)
        if st.get("uploadStatus") == "rejected":
            return PublishResult(status="failed", detail=f"YouTube rejected the upload: {st.get('rejectionReason')}")
        if st.get("privacyStatus") == "public":
            return PublishResult(status="live", url=post.get("url"), external_id=post["external_id"], detail="Public", extra={"views": views})
        if st.get("publishAt"):
            return PublishResult(status="scheduled", url=post.get("url"), external_id=post["external_id"],
                                 detail=f"Private until {st['publishAt']}")
        return PublishResult(status="private", url=post.get("url"), external_id=post["external_id"],
                             detail=f"Still {st.get('privacyStatus')} on YouTube")

    def stats(self, post: dict) -> dict | None:
        r = self.check(post)
        if r and "views" in r.extra:
            return {"views": r.extra["views"]}
        return None


def _rfc3339(s: str) -> str:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
