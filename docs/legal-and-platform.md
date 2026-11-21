# Legal and platform notes for Clip Dashboard

Research memo for the owner, written 21 November 2026 from the sources listed at the end. Every page was read on
20–21 November 2026 unless noted. This is not legal advice; section 7 lists what to ask a lawyer.

**What the app does (from `README.md` and `clipdash/publish/*` docstrings):** it downloads only the ~90-second sections
around detected moments of public Twitch VODs and YouTube videos (yt-dlp, TwitchDownloaderCLI for chat), renders a 9:16
captioned clip, a person approves each one, and it posts through the YouTube Data API v3 (user's own Google project),
TikTok Content Posting API (user's own app), Instagram Graph via Facebook Login for Business (user's own Meta app), an
audited posting service (Zernio), or an "assisted" route (file + caption, user uploads by hand). Optional narrated intro:
a model-written hook read by a synthetic voice; the app sets YouTube `containsSyntheticMedia` and TikTok `is_aigc`.
Credentials and all state stay in `data/`; there is no telemetry.

## 1. Summary and risk table

Two residual risks dominate: (a) downloading from Twitch/YouTube is against both platforms' terms as written, and only
the creator's invitation (the clipping campaign) makes it defensible; (b) the "direct" TikTok and Instagram routes cannot
post publicly for other people's accounts without audits, so a sold product should route public posting through an audited
service. The rest is housekeeping.

| Area | What the rule says | App today | Risk | What to do |
|---|---|---|---|---|
| Twitch download | Licence is "personal use or internal business use only"; no "downloading (except page caching)"; no access "by means of any robot, spider, scraper, crawler, or other automated means" [T1] | Short sections only; cache capped (3000 MB); permission tick only in Streamer-clips workspace | Medium | Require the creator-permission tick for campaign sources too; keep cache small |
| YouTube download | No downloading "except … with prior written permission from YouTube"; no "automated means"; viewing "personal, non-commercial" [Y1] | Same; download does not use the YouTube API | Medium | Same as Twitch |
| YouTube posting | Unverified projects not forced private [Y4]; 100 uploads/day default, audit for more [Y3]; Testing: 100 users, 7-day authorisations [G1] | User's own Google project | Low | Keep "bring your own project" |
| TikTok posting | Unaudited clients: private viewing only, private accounts only, 5 users/24 h [K1][K2][K3] | Enforces SELF_ONLY, no default privacy, commercial toggles | Low for user; High if sold as public posting | Recommend service route for public posts |
| Instagram posting | Serving accounts you don't manage needs Advanced Access = App Review + Business Verification [M1] | Own account only | Low for user; High for strangers | Service route for others |
| Posting service | Users must follow all platforms' terms; "Only post content you have rights to use" [Z2] | Zernio adapter; AI flags unconfirmed in its docs | Medium | Confirm audit status and AI/branded flags with Zernio |
| Synthetic content | Disclose realistic AI content (YouTube [Y6], TikTok [K4]) | Narration sets `containsSyntheticMedia` and `is_aigc` | Low | Add caption reminder for Instagram |
| Paid-post disclosure | Clipper "solely responsible"; disclosure "at the beginning of the video" [W2][W3] | TikTok toggles only | Medium | Add "#ad" nudge and YouTube paid-promotion flag [Y7] |
| Campaign breaches | Rejection, lost pay, cross-campaign or permanent bans [W2] | Rules engine, human approval, one account per platform | Low | Keep |
| Selling | Vendor needs EULA, privacy notice | None yet; no licence file | Medium | Add before first sale |

## 2. Downloading sections of VODs

**Twitch Terms of Service** (last modified 08/12/2026) [T1]. §7 grants a licence "for your personal use or internal
business use only" that excludes "(a) resale or commercial use", "(d) use of any data mining, robots, or similar data
gathering or extraction methods" and "(e) downloading (except page caching) of any portion of the Twitch Services, the
Materials…". "Materials" excludes User Content, so (e) targets Twitch's own content more than a streamer's VOD. The wider
clause is §10(xi): you will not access Twitch "by means of any robot, spider, scraper, crawler, or other automated means
for any purpose". §8 acknowledges others may have "copied or stored portions of the User Content (e.g., made a Clip)".

**Twitch Developer Services Agreement** (last modified 12/04/2024) [T2]: "Do not store copies of Twitch Content … unless
you: (a) obtain prior written authorization from Twitch …; (b) control the rights associated with such content; or (c)
cache such information for only a twenty-four hour time period". It binds registered API developers; the app registers
no Twitch app (yt-dlp/TwitchDownloaderCLI use the website's own endpoints), so it is not accepted, but nor is there any
developer-side permission.

**YouTube Terms of Service** (effective 15 December 2023) [Y1]: you may not "access, reproduce, download, distribute …
any Content except: (a) as expressly authorized by the Service; or (b) with prior written permission from YouTube", nor
use "automated means (such as robots, botnets or scrapers)", nor view Content "other than for personal, non-commercial
use". The API Developer Policies separately bar API clients from storing "copies of YouTube audiovisual content" [Y2];
the app downloads without the API, so only the ToS applies.

**Practical reality.** In a clipping campaign the copyright owner invites clipping. The Content Rewards Creator Terms make
the clipper warrant "your Clip does not infringe anyone's rights" (§17.2) and license approved clips to the brand
"perpetual, worldwide, royalty-free" (§17.3) [W2]; briefs reject footage "screen-recorded, or re-uploaded from elsewhere"
[W4]. That is a copyright licence from the creator, not permission from Twitch/YouTube to automate downloads, but it makes
a copyright claim very unlikely and leaves the platforms' remedy mostly technical (blocking yt-dlp).

**Recommendations.** (1) Require the "This creator publicly allows clipping" tick for campaign sources as well (today only
the Streamer-clips workspace enforces it, `clipdash/app.py`) and store the campaign/terms link. (2) Keep downloading only
sections, keep the cache cap low, add "delete source segments after render". (3) Present the app as a tool the user runs
on their own machine; the vendor never downloads or posts. (4) Never add whole-VOD or channel archiving.

## 3. Posting through official routes

**YouTube.** Each user creates their own Google Cloud project, so the user is the API Client owner. Default quota: "100
`search.list` calls, 100 `videos.insert` calls, and 10,000 units per day"; more requires an API Compliance Audit [Y3].
Policies require "exactly one (1) API Project for that API Client" and no sharing of credentials (III.D.1) [Y2]; each
client needs a published privacy policy (API ToS §7) [Y8]. "Videos uploaded from unverified API projects are not
restricted to private viewing mode" [Y4]. Users must be able to choose public, private or unlisted [Y5]; the app does.
OAuth Testing mode: "up to 100 test users", authorisations "expire seven days from the time of consent"; in production,
unverified apps with sensitive scopes show a warning and a 100-user cap [G1]. Verification needs a homepage on a verified
domain, privacy policy, demo video and scope justification, "up to 10 days" [G2][G3]. No page I read classifies
`youtube.upload` explicitly; assume sensitive. If you ever ship one shared client, all of this (and one 100-uploads/day
quota for every customer) lands on you; "bring your own project" avoids it.

**TikTok.** "All content posted by unaudited clients will be restricted to private viewing mode"; an audit is needed "to
lift the restrictions" [K1]; "Unaudited clients can only post to a private account" [K3]; "Unaudited API Clients can allow
up to 5 users to post in a 24 hour window" [K2]. The audit checks the Content Sharing Guidelines UX: preview, editable
title, privacy chosen from a dropdown with "no default value", commercial-content disclosure off by default, consent before
publishing [K2]. `tiktok_direct.py` follows this. Whether inbox/draft upload escapes the private rule is still undocumented.

**Instagram.** Standard Access is for "people who have roles on" the app; serving accounts "you don't own or manage" needs
Advanced Access, which "requires App Review and Business Verification" [M1][M3][M4]. Limit: "100 API-published posts
within a 24-hour moving period"; local-file resumable upload is "Only for apps that have implemented Facebook Login for
Business" [M2]. Meta Platform Terms (3 February 2026) require a public privacy policy [M5].

**Why the service route is the practical path.** Direct posting asks every customer to pass TikTok's audit and Meta's App
Review, which hobby clippers will not do. An audited service does it once for everyone; Zernio documents public TikTok
posts and per-24-hour caps [Z3]. Trade-offs: dependency on ZERNIO SOFTWARE SL (Spain), which can suspend accounts "without
prior notice" [Z1]; media passes through its servers; its docs do not state that its TikTok client is audited or show an
`is_aigc` field [Z3]. Keep direct for the user's own accounts and recommend the service for public TikTok/Instagram posts.

## 4. Synthetic content disclosure

**YouTube** [Y6]: "We require creators to disclose AI-generated or altered content that seems realistic"; exempt are
non-realistic content and minor edits such as "caption creation, and voice or audio repair". Repeated non-disclosure risks
a forced label, "removal of content or suspension from the YouTube Partner Program". API field:
`status.containsSyntheticMedia` [Y4].

**TikTok** [K4] (Community Guidelines effective 24 September 2026): "we require creators to label AI-generated or
significantly edited content that shows realistic-looking scenes or people"; unlabelled content may be removed or
labelled. The 2023 announcement covers "realistic images, audio or video" [K5]. API field `is_aigc` adds "Creator labeled
as AI-generated" [K3]. TikTok's help-centre AI article could not be read.

**The narrated intro** is a synthetic voice reading a model-written line over real footage, so disclosure is the safe
reading. The app sets both flags automatically, shows "AI voice" in rule results and adds a reminder to assisted captions.
Gaps: no Instagram reminder; and README's "there is no model-written text" is untrue once narration is on; fix the wording.

## 5. Campaign rules (Whop Content Rewards and similar)

Two sets of terms exist. Campaigns joined inside Whop use the older Whop Content Rewards terms [W1]; the newer Content
Rewards Inc. Creator Terms (last updated 17 November 2026) [W2] apply on the standalone platform. Key rules:

| Rule | Source |
|---|---|
| 18+ only | W1; W2 §2.1 "You must be at least 18 years old" |
| Submit "within 30 minutes of publishing it" | W2 §7.12; example brief rule 12 [W4] |
| Brand/Organization decides approval; rejection only if the brief is not followed, fraud suspected, or after end date/max payout | W1 ("Sellers may reject the submission ONLY if…"); W2 §7.2 |
| "all Deliverables will be wholly original"; "Duplicate Deliverables are prohibited" | W1 |
| Views exclude "bots, script, macro or other automated means"; buying engagement "results in a permanent ban"; "org-wide (cross-campaign) ban" possible | W1; W2 §12.2, §13.1, §13.2 |
| FTC disclosure: "#Sponsored" (W1); "must appear at the beginning of the video", clipper "solely responsible" (W2 §12.9); "#Ad" / "Paid partnership" in first 10 s, else "lost payment or removal" [W3] | W1, W2, W3 |
| Rights: clipper warrants no infringement and clears third-party rights; perpetual licence to CR and the Brand | W2 §17.2, §17.3 |
| 10% fee on CPM/pool payouts; "an open fraud flag pauses settlement" | W1; W2 §5.1, §7.3 |
| No paid promotion/boosting; keep post live 30 days; one campaign per video; use only campaign source footage; "Which AI tools you may use" is set per campaign | example brief rules 3, 8, 9, 10 [W4]; Whop docs: owners set "Video length or format specifications" [W5] |

The "61 s minimum", required name/hashtag and "no AI" rules are campaign-level, not platform-wide: Whop's docs say owners
define them per campaign [W5], and I found no Whop page stating 61 seconds or a blanket AI ban. The app treats them as
data pasted from the brief (campaign importer, rules engine) and locks narration off when a brief bans AI, which matches
how enforcement works: the organisation rejects or bans, Whop/CR arbitrates payment, and the user, not the tool vendor,
carries it. I could not find published terms for "Reach"-style programmes; treat them as the same shape until read.

## 6. Selling the software

**Terms of Use / EULA should say:** the user operates the tool on their own computer with their own accounts and developer
credentials; the user is responsible for having the creator's permission to clip, for complying with Twitch/YouTube/TikTok/
Instagram/Meta terms and the posting service's terms, and for every campaign's rules and disclosure duties; the vendor does
not download, host, review or post content; no warranty that downloads or API routes keep working (platforms change and
block tools); limitation of liability to the subscription paid; the user may not use the app to bulk-archive, view-bot or
evade platform restrictions; licence is per person, non-transferable; the Pro subscription is a licence, not ownership.

**Privacy notice:** state what is true today, which is strong: everything (database, clips, cache, `secrets.json`) stays in
`data/` on the user's machine; the app talks only to the platforms the user connects, to the model/TTS providers the user
enables with their own keys, and to GitHub/package hosts for tool downloads; no telemetry. If you later add crash reports
or usage metrics, the notice must change first. Google's verification rules and Meta's Platform Terms both require a
public privacy policy if you ever ship a shared API client [G2][M5], and Zernio's DPA/subprocessor pages show what a GDPR
notice for the service route must mention [Z1].

**yt-dlp disclaimer:** advisable. yt-dlp is public-domain (Unlicense) [D1] and its README, read in parts, contains no
statement about what may lawfully be downloaded, so nothing upstream protects you. A short sentence in the EULA and in
Setup ("Clip Dashboard uses yt-dlp and TwitchDownloaderCLI to fetch sections of public videos; downloading may be
restricted by the platforms' terms and by copyright; you confirm you have the creator's permission") makes the user's
responsibility explicit and keeps the vendor's position as a general-purpose tool. Also add a licence file to the repo
(there is none) and keep third-party notices for ffmpeg/imageio-ffmpeg, Whisper and Piper, several of which are GPL/MIT.

## 7. Questions for a lawyer

1. Does distributing a tool whose main use case involves downloading from Twitch/YouTube expose the vendor to secondary
   liability (contributory infringement, anti-circumvention, tortious interference with the platforms' terms) in your
   jurisdiction, given that the creator invites the clipping?
2. Is a campaign brief plus the Content Rewards terms a sufficient copyright licence for the clipper, and does it cover
   the rendered derivative (captions, narration) and the perpetual licence back to the brand?
3. Which consumer law applies to selling to EU/UK users (cooling-off, "as is" limits), and how must the Pro subscription
   and refunds be worded?
4. Is the EULA's "user is responsible for platform terms" allocation enforceable against consumers, and what liability cap
   survives?
5. Does the vendor need a privacy policy/DPA even with no telemetry (e.g. support emails, licence keys)?
6. Are the FTC/UK CMA/EU influencer-disclosure rules the clipper's alone, or could a tool that drafts captions share
   responsibility if it omits "#ad"?
7. If TikTok or Meta later require the vendor to be the audited API client, what contract is needed with the posting
   service, and who is the data controller for media passing through it?
8. Name/branding: "Clip Dashboard" plus references to Twitch, YouTube, TikTok, Instagram, Whop in marketing — trademark
   use guidelines to follow.

## Sources (all read 20–21 November 2026)

- [T1] Twitch Terms of Service, last modified 08/12/2026 — https://legal.twitch.com/en/legal/terms-of-service/ (fetched
  with curl; the page is script-rendered). Prior-version copy used to cross-check §7/§10:
  https://code.europa.eu/p2b/contrib-versions/-/raw/main/Twitch/Terms%20of%20Service.md
- [T2] Twitch Developer Services Agreement, last modified 12/04/2024 — https://legal.twitch.com/en/legal/developer-agreement/
- [Y1] YouTube Terms of Service, effective 15 Dec 2023 — https://www.youtube.com/t/terms
- [Y2] YouTube API Services Developer Policies — https://developers.google.com/youtube/terms/developer-policies
- [Y3] Quota and compliance audits — https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
- [Y4] Videos resource / `videos.insert` — https://developers.google.com/youtube/v3/docs/videos and
  https://developers.google.com/youtube/v3/docs/videos/insert
- [Y5] Required Minimum Functionality — https://developers.google.com/youtube/terms/required-minimum-functionality
- [Y6] Disclosing altered or synthetic content — https://support.google.com/youtube/answer/14328491
- [Y7] Paid product placements and endorsements — https://support.google.com/youtube/answer/154235
- [Y8] YouTube API Services Terms of Service — https://developers.google.com/youtube/terms/api-services-terms-of-service;
  revision history https://developers.google.com/youtube/terms/revision-history (no 2025–26 entry about the private-upload
  change; the change is stated on [Y4] only)
- [G1] Google Cloud "Manage App Audience" — https://support.google.com/cloud/answer/15549945
- [G2] Sensitive scope verification — https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
- [G3] OAuth app verification requirements — https://support.google.com/cloud/answer/13464321
- [K1] TikTok Content Posting API, get started (last updated 4 Aug 2026) — https://developers.tiktok.com/doc/content-posting-api-get-started
- [K2] TikTok Content Sharing Guidelines (4 Aug 2026) — https://developers.tiktok.com/doc/content-sharing-guidelines
- [K3] TikTok Direct Post reference (27 Oct 2026) — https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
- [K4] TikTok Community Guidelines, Integrity and Authenticity (effective 24 Sep 2026) — https://www.tiktok.com/community-guidelines/en/integrity-authenticity
- [K5] TikTok newsroom, AI-generated content labels (19 Sep 2023) — https://newsroom.tiktok.com/en-us/new-labels-for-disclosing-ai-generated-content
- [M1] Instagram Platform overview — https://developers.facebook.com/docs/instagram-platform/overview
- [M2] Instagram content publishing — https://developers.facebook.com/docs/instagram-platform/content-publishing
- [M3] Business Verification — https://developers.facebook.com/docs/development/release/business-verification
- [M4] App Review — https://developers.facebook.com/docs/resp-plat-initiatives/individual-processes/app-review
- [M5] Meta Platform Terms (last updated 3 Feb 2026) — https://developers.facebook.com/terms/
- [Z1] Zernio Terms of Service (7 Nov 2026) — https://zernio.com/tos; DPA https://zernio.com/dpa
- [Z2] Zernio Content Guidelines (9 Oct 2026) — https://zernio.com/content-guidelines
- [Z3] Zernio TikTok docs — https://docs.zernio.com/platforms/tiktok
- [W1] Whop Content Rewards Terms of Service (no date on page) — https://whop.com/content-rewards-terms-of-service/
- [W2] Content Rewards Inc. Creator Terms (17 Nov 2026) — https://b4e0vdqv6zgqeqj4pfgm.apps.whop.com/terms
- [W3] Whop FTC guidelines (effective 15 Apr 2025) — https://whop.com/ftc/
- [W4] Example campaign brief with rules — https://whop.com/scroll-c828/
- [W5] Whop docs, Content Rewards — https://docs.whop.com/memberships-and-access/third-party-apps/content-rewards
- [D1] yt-dlp README (Unlicense; no download disclaimer found) — https://github.com/yt-dlp/yt-dlp

**Could not read (said so above where it matters):** TikTok help-centre article on AI-generated content
(https://www.tiktok.com/support/faq_detail?id=7636670084747893268, script-only); TikTok Developer Terms of Service
(https://www.tiktok.com/legal/page/global/tiktok-developer-terms-of-service/en, script-only); Whop help article
11430668 (redirects to a generic support page); Whop campaign-owner docs at docs.whop.com/manage-your-business (login
wall); any published terms for "Reach"; confirmation that Zernio's TikTok client is audited; an explicit Google page
classifying `youtube.upload` as a sensitive scope.
