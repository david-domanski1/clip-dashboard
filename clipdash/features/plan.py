"""Plans and licensing: "free" (one campaign besides the Streamer clips workspace, assisted posting only, no narration,
no analytics) and "pro" (everything).

The licence lives in data/license.json (mode 0600, gitignored with the rest of data/):
  {key, plan, email_masked, activated_at, last_validated_at, valid_until, instance_id}
Nothing is phoned home except the licence key itself; no usage data leaves the machine.

Licence store: Lemon Squeezy's License API (docs read on 2026-11-21, https://docs.lemonsqueezy.com/api/license-api).
No API key is needed for these three endpoints; the body is form-encoded and the response is JSON:
  POST https://api.lemonsqueezy.com/v1/licenses/activate    license_key (required), instance_name (required)
    -> {activated: bool, error: str|null,
        license_key: {id, status, key, activation_limit, activation_usage, created_at, expires_at|null},
        instance: {id (uuid), name, created_at}   (only on success),
        meta: {store_id, order_id, order_item_id, product_id, product_name, variant_id, variant_name, customer_id,
               customer_name, customer_email}}
  POST https://api.lemonsqueezy.com/v1/licenses/validate    license_key (required), instance_id (optional)
    -> {valid: bool, error: str|null, license_key: {...as above}, instance: {...}|null, meta: {...}}
  POST https://api.lemonsqueezy.com/v1/licenses/deactivate  license_key (required), instance_id (required)
    -> {deactivated: bool, error: str|null, license_key: {...}, meta: {...}}
license_key.status is "active", "inactive", "expired" or "disabled". A failed call answers with error set and the
boolean false (HTTP 400/404). The settings license_store_id / license_product_id, when filled in, must match meta.store_id /
meta.product_id so a key bought for another product does not unlock this one.
NOTE (seen on the docs pages on 2026-11-21): Lemon Squeezy is no longer accepting new sign-ups and announces a shutdown on
2027-03-31. The wire format above is small; swapping the three URLs and the field names in _call() is all a different
store needs. See docs/monetization.md.

Offline grace: a pro licence stays pro for GRACE_DAYS after last_validated_at even without network, then falls back to
free until a validation succeeds. GET /api/plan re-validates quietly once a day when the machine is online.

Override: the environment variable CLIPDASH_PLAN=pro (or free) forces the plan without any licence; the test suite sets
it so existing tests can create several campaigns, and tests/test_plan.py removes it to exercise the free plan.

Developer / dry-run path: a key starting with "DEV-" activates locally as pro with no network call. It is meant for
development and demos; the setting license_dev_keys defaults to off in a packaged (frozen) build.

Other modules gate behaviour with require("campaigns" | "direct_posting" | "narration" | "analytics"), which raises
HTTPException(402, {"message", "feature", "upgrade_url"}) on the free plan when the limit is reached."""
from __future__ import annotations

import json
import math
import os
import socket
import stat
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, HTTPException

from .. import config, db

router = APIRouter()

API = "https://api.lemonsqueezy.com/v1/licenses"
GRACE_DAYS = 14
REVALIDATE_AFTER = timedelta(days=1)
TIMEOUT = 15

FREE_CAMPAIGNS = 1
PLANS = {
    "free": {"name": "Free", "campaigns": FREE_CAMPAIGNS, "direct_posting": False, "narration": False, "analytics": False},
    "pro": {"name": "Pro", "campaigns": None, "direct_posting": True, "narration": True, "analytics": True},
}
# What each plan includes, in the words the Settings tab shows.
INCLUDES = {
    "free": ["The Streamer clips workspace", "1 clipping campaign", "Moment finding, captions, vertical render",
             "Review checklist and rule checks", "Assisted posting (you upload the file)", "30-minute submission countdown"],
    "pro": ["Everything in Free", "Unlimited campaigns", "Direct and posting-service routes (post from the app, schedule)",
            "Narrated hook intros", "Earnings and views analytics", "Priority fixes for platform changes"],
}
UPGRADE_MESSAGES = {
    "campaigns": "The free plan includes one campaign. Upgrade to Pro in Settings → Plan to add more.",
    "direct_posting": "The free plan posts through the assisted route only. Upgrade to Pro in Settings → Plan to connect "
                      "an account and post from the app.",
    "narration": "Narrated intros are part of Pro. Upgrade in Settings → Plan to switch narration on.",
    "analytics": "Views and earnings analytics are part of Pro. Upgrade in Settings → Plan to see them.",
}

# Source checkouts accept DEV- keys; a packaged (PyInstaller) build does not unless the setting is switched on.
DEV_KEYS_DEFAULT = not getattr(sys, "frozen", False)

config.register_settings({
    "license_store_id": "",          # Lemon Squeezy store id; blank = accept any store (development)
    "license_product_id": "",        # Lemon Squeezy product id; blank = accept any product
    "license_checkout_url": "https://clipdash.lemonsqueezy.com/checkout",
    "license_dev_keys": DEV_KEYS_DEFAULT,   # DEV-… keys activate locally as pro; off by default in a packaged build
})

# The most recent "limit hit", kept in memory so Today can show an upgrade card for a day.
last_402: dict = {"at": None, "feature": None, "message": None}


# ------------------------------------------------------------------ licence file

def license_path():
    return config.DATA / "license.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def read_license() -> dict | None:
    try:
        d = json.loads(license_path().read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("key") else None


def write_license(d: dict | None) -> None:
    p = license_path()
    if d is None:
        try:
            p.unlink()
        except OSError:
            pass
        return
    config.ensure_dirs()
    p.write_text(json.dumps(d, indent=2))
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def mask_key(key: str | None) -> str:
    if not key:
        return ""
    key = str(key)
    return key if len(key) <= 8 else f"{key[:4]}…{key[-4:]}"


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    name, domain = email.split("@", 1)
    return f"{name[:1]}…@{domain}"


def is_dev_key(key: str | None) -> bool:
    return bool(key) and str(key).upper().startswith("DEV-") and bool(db.get_setting("license_dev_keys", DEV_KEYS_DEFAULT))


# ------------------------------------------------------------------ plan state

def grace_days_left(lic: dict | None = None) -> int | None:
    """Days of offline grace left for a pro licence (None when there is no licence or it is a developer key)."""
    lic = lic if lic is not None else read_license()
    if not lic or lic.get("plan") != "pro":
        return None
    if is_dev_key(lic.get("key")):
        return None
    last = _parse(lic.get("last_validated_at"))
    if not last:
        return 0
    left = (last + timedelta(days=GRACE_DAYS)) - _now()
    return max(0, math.ceil(left.total_seconds() / 86400))


def plan_override() -> str | None:
    """CLIPDASH_PLAN=pro|free forces a plan (the test suite and local development set it; a shipped build never does)."""
    v = os.environ.get("CLIPDASH_PLAN", "").strip().lower()
    return v if v in PLANS else None


def current_plan() -> str:
    forced = plan_override()
    if forced:
        return forced
    lic = read_license()
    if not lic or lic.get("plan") != "pro":
        return "free"
    if is_dev_key(lic.get("key")):
        return "pro"
    until = _parse(lic.get("valid_until"))
    if until and until + timedelta(days=GRACE_DAYS) < _now():
        return "free"
    return "pro" if (grace_days_left(lic) or 0) > 0 else "free"


def _campaigns_used() -> int:
    return db.execute("SELECT COUNT(*) c FROM campaigns WHERE COALESCE(kind,'') != 'streamer'").fetchone()["c"]


def limits(plan: str | None = None) -> dict:
    """Used vs allowed for everything the plan gates; the workspace never counts as a campaign."""
    plan = plan or current_plan()
    p = PLANS[plan]
    used_c = _campaigns_used()
    used_d = db.execute("SELECT COUNT(*) c FROM accounts WHERE route != 'assisted'").fetchone()["c"]
    return {
        "campaigns": {"used": used_c, "allowed": p["campaigns"], "ok": p["campaigns"] is None or used_c < p["campaigns"]},
        "direct_posting": {"used": used_d, "allowed": p["direct_posting"], "ok": p["direct_posting"]},
        "narration": {"allowed": p["narration"], "ok": p["narration"]},
        "analytics": {"allowed": p["analytics"], "ok": p["analytics"]},
    }


def upgrade_url() -> str:
    return db.get_setting("license_checkout_url", "") or ""


def require(feature: str) -> None:
    """Raise 402 with a plain message when the current plan does not allow `feature` (or its limit is reached)."""
    if feature not in UPGRADE_MESSAGES:
        raise ValueError(f"unknown feature {feature!r}")
    plan = current_plan()
    if plan == "pro":
        return
    lim = limits(plan)[feature]
    if lim.get("ok"):
        return
    msg = UPGRADE_MESSAGES[feature]
    last_402.update({"at": _now(), "feature": feature, "message": msg})
    raise HTTPException(402, {"message": msg, "feature": feature, "upgrade_url": upgrade_url()})


def limit_hit_recently(within: timedelta = timedelta(days=1)) -> dict | None:
    at = last_402.get("at")
    if not at or _now() - at > within:
        return None
    return {"feature": last_402["feature"], "message": last_402["message"], "at": _iso(at)}


# ------------------------------------------------------------------ store calls

def _instance_name() -> str:
    try:
        host = socket.gethostname()
    except OSError:
        host = "unknown"
    return f"Clip Dashboard on {host}"[:100]


def _call(endpoint: str, **fields) -> dict:
    """One License API call; raises requests exceptions when offline, returns the parsed body otherwise."""
    r = requests.post(f"{API}/{endpoint}", data=fields, headers={"Accept": "application/json"}, timeout=TIMEOUT)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    body.setdefault("_status", r.status_code)
    return body


def _product_matches(meta: dict) -> tuple[bool, str]:
    want_store = str(db.get_setting("license_store_id", "") or "").strip()
    want_product = str(db.get_setting("license_product_id", "") or "").strip()
    if want_store and str(meta.get("store_id", "")) != want_store:
        return False, "This key belongs to a different store."
    if want_product and str(meta.get("product_id", "")) != want_product:
        return False, "This key is for a different product."
    return True, ""


def _record_from(key: str, body: dict, instance_id: str | None, existing: dict | None = None) -> dict:
    lk = body.get("license_key") or {}
    meta = body.get("meta") or {}
    now = _now()
    return {
        "key": key, "plan": "pro",
        "email_masked": mask_email(meta.get("customer_email")) or (existing or {}).get("email_masked", ""),
        "activated_at": (existing or {}).get("activated_at") or _iso(now),
        "last_validated_at": _iso(now),
        "valid_until": lk.get("expires_at"),
        "instance_id": instance_id or (existing or {}).get("instance_id"),
        "status": lk.get("status", "active"),
    }


def activate(key: str) -> dict:
    key = (key or "").strip()
    if not key:
        raise HTTPException(400, {"message": "Paste your licence key first."})
    if is_dev_key(key):
        write_license({"key": key, "plan": "pro", "email_masked": "developer", "activated_at": _iso(_now()),
                       "last_validated_at": _iso(_now()), "valid_until": None, "instance_id": "dev-" + uuid.uuid4().hex[:8],
                       "status": "active"})
        return info()
    try:
        body = _call("activate", license_key=key, instance_name=_instance_name())
    except requests.RequestException:
        raise HTTPException(503, {"message": "Could not reach the licence server. Check your connection and try again."})
    if not body.get("activated"):
        raise HTTPException(400, {"message": _plain_error(body.get("error"))})
    ok, why = _product_matches(body.get("meta") or {})
    if not ok:
        inst = (body.get("instance") or {}).get("id")
        if inst:
            try:
                _call("deactivate", license_key=key, instance_id=inst)
            except requests.RequestException:
                pass
        raise HTTPException(400, {"message": why})
    write_license(_record_from(key, body, (body.get("instance") or {}).get("id")))
    return info()


def validate() -> dict:
    """Re-check the stored licence with the store. Offline: nothing changes (grace keeps counting down)."""
    lic = read_license()
    if not lic:
        return info()
    if is_dev_key(lic.get("key")):
        return info()
    try:
        body = _call("validate", license_key=lic["key"], **({"instance_id": lic["instance_id"]} if lic.get("instance_id") else {}))
    except requests.RequestException:
        out = info()
        out["validation"] = "offline"
        return out
    if body.get("valid"):
        write_license(_record_from(lic["key"], body, lic.get("instance_id"), existing=lic))
        out = info()
        out["validation"] = "ok"
        return out
    lic = dict(lic)
    lic["plan"] = "free"
    lic["status"] = (body.get("license_key") or {}).get("status") or "invalid"
    lic["last_error"] = _plain_error(body.get("error"))
    write_license(lic)
    out = info()
    out["validation"] = "invalid"
    return out


def deactivate() -> dict:
    lic = read_license()
    if lic and not is_dev_key(lic.get("key")) and lic.get("instance_id"):
        try:
            _call("deactivate", license_key=lic["key"], instance_id=lic["instance_id"])
        except requests.RequestException:
            pass   # the seat frees itself when the store sees no validations; the local file goes regardless
    write_license(None)
    return info()


def _plain_error(err) -> str:
    e = str(err or "").strip()
    if not e:
        return "The licence server did not accept this key."
    low = e.lower()
    if "activation limit" in low:
        return "This key is already in use on its allowed number of computers. Deactivate it there first (Settings → Plan)."
    if "expired" in low:
        return "This licence has expired. Renew it, then activate again."
    if "disabled" in low:
        return "This licence has been disabled. Contact support."
    if "not found" in low or "invalid" in low:
        return "That key was not recognised. Check it for typos."
    return e


# ------------------------------------------------------------------ API

def info() -> dict:
    lic = read_license()
    plan = current_plan()
    out = {
        "plan": plan, "plans": {k: {"name": v["name"], "includes": INCLUDES[k]} for k, v in PLANS.items()},
        "limits": limits(plan), "upgrade_url": upgrade_url(), "grace_days": GRACE_DAYS,
        "grace_days_left": grace_days_left(lic), "limit_hit": limit_hit_recently(), "license": None,
    }
    if lic:
        out["license"] = {
            "key_masked": mask_key(lic.get("key")), "email_masked": lic.get("email_masked", ""), "plan": lic.get("plan"),
            "activated_at": lic.get("activated_at"), "last_validated_at": lic.get("last_validated_at"),
            "valid_until": lic.get("valid_until"), "status": lic.get("status"), "dev": is_dev_key(lic.get("key")),
            "last_error": lic.get("last_error"),
        }
    return out


def _maybe_revalidate() -> None:
    lic = read_license()
    if not lic or lic.get("plan") != "pro" or is_dev_key(lic.get("key")):
        return
    last = _parse(lic.get("last_validated_at"))
    if last and _now() - last < REVALIDATE_AFTER:
        return
    try:
        validate()
    except Exception:  # noqa: BLE001  never let a store hiccup break the Settings page
        pass


@router.get("/api/plan")
def get_plan():
    _maybe_revalidate()
    return info()


@router.post("/api/plan/activate")
def post_activate(body: dict):
    return activate(str(body.get("key", "")))


@router.post("/api/plan/validate")
def post_validate():
    return validate()


@router.post("/api/plan/deactivate")
def post_deactivate():
    return deactivate()
