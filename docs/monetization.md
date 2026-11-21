# Monetisation plan

## Value proposition

Clip Dashboard turns a streamer's multi-hour GTA VI VOD into ready-to-post vertical clips for paid clipping campaigns.
Doing this by hand (scrubbing the VOD for a moment, cutting, reframing to 9:16 with the facecam, captioning, writing a
caption with the campaign's required names and hashtags, uploading, then pasting the link into the campaign before the
deadline) takes a clipper roughly 20 to 40 minutes per clip; with the app it is about 3 to 5 minutes of a person
reviewing and approving a clip the app already found, cut and captioned. The 30-minute submission guard counts down
from the moment a post goes live so the link reaches the campaign inside its window, which is the most common reason
paid clips are not counted. Rule checks (length, required names and hashtags, watermark and AI-voice bans, source date
window) run before a clip can be approved, so clips are not rejected after the work is done. Earnings analytics show
views and estimated pay per campaign, so a clipper knows which streamers and campaigns are worth their time.

## Pricing

| Plan | Price | For |
|---|---|---|
| **Free** | USD 0 | Trying the app on one campaign; clippers who upload by hand |
| **Pro** | **USD 24 / month** or **USD 190 / year** (34% off, about 8 months' price) | Clippers working several campaigns who want to post from the app |

Reasoning:
- **Anchored on earnings.** Campaigns typically pay USD 1 to 3 per 1,000 views. One clip reaching 10,000 to 20,000
  views pays for a month of Pro; an active clipper posts dozens a week. The price is a small share of what the tool
  helps earn, and time saved alone (20+ minutes per clip) passes USD 24 within the first few clips.
- **Below generic clipping tools.** AI clip-finder SaaS (Opus Clip, Vizard and similar) sells at roughly USD 15 to 30
  a month with minute quotas and cloud processing. Clip Dashboard runs locally with no minute quota, so it sits in the
  same band without metering.
- **Yearly plan** rewards the clippers who stay for a whole game season, gives cash up front, and lowers churn.
- **Free is genuinely useful** (one full campaign, every rule check, the countdown) so people see the time saving
  before paying; the upgrade moment is natural (a second campaign, wanting to post from the app).

## What is gated and why

| Gate | Free | Pro | Why this line |
|---|---|---|---|
| Campaigns | 1 (the *Streamer clips* workspace does not count) | Unlimited | Running several campaigns is what a serious, earning clipper does; one is enough to evaluate. |
| Posting routes | Assisted (the app prepares the file and caption, you upload) | Direct and posting-service routes, scheduling | Posting from the app is the biggest time saver after clip finding, and the routes cost real setup and maintenance. |
| Narrated hook intros | Off | On | Optional extra; needs a voice model and campaign rules that allow it. |
| Earnings tab (Settings → Stats) | Hidden | Shown | Analytics matter once there is income across several campaigns. |

Never gated: moment finding, captions, vertical render, the review checklist, rule checks and the 30-minute guard.
Gating those would make free clips worse or get them rejected, which hurts the clipper and the reputation of the app.
The product rules (a person approves every clip, official posting routes only, one account per platform per clip, no
generated media or model-written captions, credentials stay local) apply on every plan.

Enforcement is in `clipdash/features/plan.py`: `require("campaigns" | "direct_posting" | "narration")` answers
HTTP 402 with a plain message naming what to do; the UI shows it as a toast and puts an upgrade card on Today for a day.
Existing data is never deleted or locked: a lapsed Pro user keeps every campaign and account and can still review and
post through the assisted route; only adding more is blocked.

## Licence flow

1. The buyer clicks **Get Pro** in Settings → Plan, which opens the store checkout (`license_checkout_url`).
2. The store emails a licence key. The buyer pastes it into Settings → Plan → **Activate**.
3. The app calls the store's licence API `activate` with the key and an instance name ("Clip Dashboard on <computer
   name>"), checks the store and product ids match, and writes `license.json` (mode 0600) in the data folder with the
   key, masked email, instance id and `last_validated_at`. Nothing else leaves the machine: no usage data, no clips.
4. Once a day, when online, `GET /api/plan` re-validates quietly (`validate` with key and instance id). **Check now**
   does it on demand.
5. **Deactivate on this computer** frees the seat (`deactivate`) so the key can move to a new machine. Suggested
   activation limit per key: 2 computers.
6. A subscription that is cancelled or fails payment makes `validate` answer invalid/expired; the app drops to Free and
   says why.

Developer keys starting `DEV-` activate Pro locally with no network (source checkouts only; refused in a packaged build).
`CLIPDASH_PLAN=pro` forces Pro for development and the test suite.

**Store: Lemon Squeezy, with a deadline.** The code uses Lemon Squeezy's License API (merchant of record, so it handles
sales tax/VAT). Its documentation states, as read on 2026-11-21: *"Lemon Squeezy is no longer accepting new sign-ups and
will shut down on March 31, 2027."* So Lemon Squeezy only works if the owner already has a store, and only until that
date. Plan for a replacement before launch. Merchant-of-record options with a licence-key API include Paddle and
Polar; Gumroad and Keygen are alternatives (re-read their docs before choosing). The store-specific code is small: three
URLs and field names in `plan._call()`, `activate()`, `validate()` and `deactivate()`.

## Refunds and offline use

- **Refunds:** 14 days, no questions asked, for the first payment of a monthly or yearly plan (the store issues it and
  the key is disabled, so the app drops to Free at its next validation). Renewals are not refunded but can be cancelled
  at any time and stay Pro until the paid period ends.
- **Offline grace:** Pro keeps working for **14 days** after the last successful validation without network (so a
  laptop on a trip or a flaky connection never loses Pro mid-campaign). Settings → Plan warns from 5 days left. After
  that the app falls back to Free until a validation succeeds; nothing is deleted.
- **Store outage:** if the licence server is unreachable, that counts as offline (grace continues), never as invalid.

## Before selling: prerequisites

1. **Licence store account** (see the Lemon Squeezy note above): products for Pro monthly and yearly with licence keys
   enabled, activation limit 2; then fill in `license_store_id`, `license_product_id` and `license_checkout_url`
   defaults in `plan.py`.
2. **Signed builds:** Apple Developer ID (USD 99/year) with notarisation, and a Windows code-signing certificate. See
   `packaging/README.md`. Unsigned apps get Gatekeeper / SmartScreen warnings that kill conversions.
3. **Posting routes for buyers.** Direct routes need each buyer's own developer apps, which is too much to ask of most
   customers:
   - *YouTube direct:* an unverified Google Cloud app is limited to 100 test users, shows the "unverified app" screen,
     and uploads from unaudited API projects are locked private. Shipping one shared OAuth client means Google OAuth
     verification for the `youtube.upload` scope plus a YouTube API compliance audit (weeks).
   - *TikTok direct:* posts are "only me" and the account must be private until TikTok audits the app for the Content
     Posting API.
   - *Instagram:* Meta app review for the publishing permissions.
   - **The practical path is the posting-service route** (an audited third-party posting API such as Zernio, already
     wired in): the service holds the platform approvals, buyers connect their accounts there, and Pro's
     "post from the app" works on day one. Budget for the service's per-account pricing or let buyers bring their own
     service key.
4. **Campaign platform terms:** confirm the campaign sites allow clips made with a tool when a person reviews and
   approves each one (the app never auto-approves), and keep the "no generated media / no model-written captions" rule.
5. **Legal basics:** terms of sale, privacy policy (the only data sent is the licence key; the store holds the buyer's
   email), and a support email.
6. **Release channel:** GitHub releases on the `update_repo` repo (the in-app update check reads the latest release). If
   the source repo stays private, publish releases from a separate public repo and set `update_repo` to it.
