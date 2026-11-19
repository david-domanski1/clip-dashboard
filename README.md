# Clip Dashboard

Turns streamers' GTA VI videos into vertical, captioned clips for paid clipping campaigns, lets you review
each one, and posts or schedules the approved ones to YouTube, TikTok and Instagram. Runs on your own
computer with your own accounts. Nothing is automatic past the point where a person watches the clip:
every post is something you approved.

![Review](docs/screenshots/review.png)

## What it does

1. **Campaign** – you paste the campaign's rules (creator, required name and hashtag, minimum length,
   platforms, footage dates, submission deadline, pay). Rules are data: the app checks the ones a machine
   can check and shows the rest as a checklist.
2. **Sources** – YouTube/Twitch channels or single videos attached to the campaign. Channels are watched for
   new GTA VI videos. A visual picker sets where the streamer's facecam sits.
3. **Pipeline** – each video is scanned for moments (chat spikes, YouTube "most replayed", transcript cues,
   loudness), only the 90-second segments around the best moments are downloaded, Whisper transcribes them,
   captions are burned in and the clip is laid out 9:16 (facecam on top, gameplay below).
4. **Review** – a grid of 9:16 players. Trim, pick a hook sentence from the transcript, edit the per-platform
   post text, see rule checks pass or fail, tick the checklist, approve or reject. A clip that fails a rule
   cannot be approved.
5. **Publish** – post now or schedule (date, time, time zone) to one account per platform through one of
   three routes: *direct* (your own developer app on the official API), *service* (an audited third-party
   posting API), *assisted* (the app reveals the file, copies the caption and opens the upload page).
6. **Submissions** – when a post goes live, a countdown starts (30 minutes for LACY). One click copies the
   link and opens the campaign's submission page; tick *Submitted* when done.
7. **Stats** – views where the APIs give them, and estimated earnings from the campaign's rate and cap.

## Install (once, about 5 minutes)

Requirements: **Python 3.10 or newer** from [python.org](https://www.python.org/downloads/) (on Windows tick
*Add python.exe to PATH*). No Docker, Homebrew, conda or Node.

1. Download this repository (green **Code** button → *Download ZIP*, or `git clone …`) and unzip it somewhere
   permanent, e.g. your Documents folder.
2. **macOS:** double-click `install.command`. If macOS refuses to open it, right-click → *Open*.
   **Windows:** double-click `install.bat`.
3. Wait for *Installed*. The installer creates a private `.venv` folder and downloads everything, including
   ffmpeg (bundled in the `imageio-ffmpeg` package, no separate install).

Need about 2 GB of free disk: 400 MB of packages, 500 MB for the caption model, and room for the clips.

## Launch

**macOS:** double-click `Launch.command`. **Windows:** double-click `Launch.bat`.

A terminal window opens, then your browser at `http://127.0.0.1:8765`. Keep the window open while you use the
app. Scheduled posts (except YouTube direct and the posting service, which schedule on their side) only fire
while it runs. Close the window to stop.

First launch shows **Setup**:

![Setup](docs/screenshots/setup.png)

- The dependency list should be green. Click *Download* next to the Whisper caption model (once, ~500 MB).
  TwitchDownloaderCLI is only needed for Twitch chat replays; download it when you add a Twitch source.
- Credentials are optional at this point: assisted posting works without any.

## Make your first clip (about 10 minutes)

1. **Overview → New campaign.** Paste the campaign URL and click *Read page*, or paste the rules text and
   *Pre-fill*. Check every field against the campaign page, especially the required hashtag, minimum
   length (61 s by default), footage start date, submission URL and deadline. Save.
2. **Pipeline → paste a video URL → Scan.** Pick a scan window first: *Auto* scans the last 2 hours of any VOD
   longer than 4 hours (a 2-hour window of Twitch chat downloads in seconds, so clips are reviewable in about
   2 minutes) and the whole video otherwise; *Last 6 hours*, *Whole video* and *Custom start/end* are there too.
   A stream that is still live works: the latest window of the growing VOD is scanned. After a windowed scan,
   **Scan the rest** continues in 2-hour chunks in the background, merging moments and skipping ranges already
   scanned. Videos published before the campaign's footage start date are marked *outside campaign window* and
   not scanned (channel watching does the same); *Scan anyway* overrides for one video. The first scan also loads
   the Whisper model, which takes a minute.
   After a scan the video shows its **ranked moments**: timestamp, score, why it was picked, the top chat words
   and a thumbnail. Only the single best moment is rendered automatically (Settings → *auto-render top N*,
   default 1); every other moment has a **Render** button, plus **Render next best**. Moments where the streamer
   is barely talking (Silero VAD on 40 s of audio) or where chat is one repeated message are labelled *low speech*
   / *chat spam* and scored down, so an asleep streamer with a busy chat no longer wins. Queued or running renders
   can be cancelled from the same list. On an Apple Silicon Mac a 2-hour window scans in about 30 s and the first
   clip is reviewable in about 2 minutes (hardware H.264 encoding; software fallback elsewhere).
   For a channel instead of a single video, go to **Sources** and add `youtube.com/@name` or `twitch.tv/name`;
   the suggestion chips resolve IShowSpeed, CaseOh, LACY and Kai Cenat by lookup.
3. **Sources → Set facecam crop** (once per channel). Grab a frame, drag a box around the webcam, save. Clips
   rendered before that use the gameplay-only layout; use *Re-render* on a clip to apply the crop.
4. **Review.** Open a clip. Drag the start/end sliders so it opens on something worth watching (the length
   must stay inside the campaign's limits; the number turns green). Pick a hook from the transcript chips or
   type one. Check the post text per platform (the required name and tag are added for you). Tick the
   checklist. **Approve.**
5. **Accounts → Add account.** Start with route *Assisted* for each platform you use. Add *Direct* or
   *Service* accounts later (see below).
6. **Publish → Post / schedule.** Tick accounts, choose *Post now* or *Schedule* with a time zone, confirm.
   Assisted posts appear in the queue with *Open upload page*, *Copy caption* and *Reveal file*; upload in the
   browser, paste the post link back and click *Live*.
7. **Submissions.** The countdown runs from the moment the post is live. *Copy + open submission page*, paste
   the link there, tick *Submitted*. The app warns before anything that would edit or delete a submitted post.

![Publish](docs/screenshots/publish.png)

## Streamer clips (no campaign)

The campaign dropdown also offers **Streamer clips (no campaign)**: a workspace for clipping big streamers you
have no campaign with (Speed, CaseOh, Kai Cenat…). Sources are their channels or video URLs, watched and
scanned exactly like campaign sources, but:

- Before adding a source you must tick *This creator publicly allows clipping* (a `!clipping` program or
  clip-friendly terms). Paste a link to their terms and the source card shows it.
- Clips have no campaign rules (only the 9:16 format and clean-text checks) and no submission tracker.
- Captions open with a credit line that names the streamer and their handle. The template is editable per
  source: `{hook} — clip from {creator}'s stream ({handle})`.
- Clips go through Review and Publish like everything else and can use your connected accounts.
- If a streamer also has an active campaign in the app, adding them here is blocked and you are pointed to
  the campaign, so its rules and submissions apply.

## Connecting accounts

Each account uses one route. The Accounts page shows what every route can and cannot do.

| Route | YouTube | TikTok | Instagram |
|---|---|---|---|
| **Assisted** | always works | always works | always works |
| **Direct** (your own developer app) | public uploads, native scheduling via `publishAt` | private ("only me") until TikTok audits your app; account must be private; or send to inbox as a draft | Reels upload via Facebook Login for Business; no scheduling on Instagram's side |
| **Service** (Zernio) | public, scheduled on their side | public, scheduled on their side | public, scheduled on their side |

### YouTube direct
1. [console.cloud.google.com](https://console.cloud.google.com) → new project → enable **YouTube Data API v3**.
2. OAuth consent screen → External → add your Google account as a test user.
3. Credentials → Create credentials → OAuth client ID → **Desktop app** → download the JSON.
4. Setup → paste the JSON → Save. Accounts → Add account → YouTube / Direct → **Connect** (a Google sign-in
   tab opens) → **Test**.
A consent screen left in *Testing* expires its tokens every 7 days; publish it to avoid reconnecting.
Default quota is 100 uploads per day.

### TikTok direct
1. [developers.tiktok.com](https://developers.tiktok.com) → create an app → add **Login Kit** and
   **Content Posting API**; scopes `user.info.basic`, `video.publish`, `video.upload`; platform **Desktop**.
2. Redirect URI: `http://127.0.0.1:8765/oauth/callback/tiktok/`.
3. Setup → client key + secret → Save. Accounts → TikTok / Direct → Connect → Test.
Until TikTok audits the app, posts can only be *Only me* and the TikTok account must be private; you flip the
video to *Everyone* in the TikTok app and paste its link. *Send to inbox as a draft* hands the video to the
TikTok app to finish there.

### Instagram direct
1. [developers.facebook.com](https://developers.facebook.com) → create a **Business** app → add Facebook
   Login for Business. Your Instagram must be a professional account linked to a Facebook Page you manage,
   and your account must have a role on the app (then no App Review is needed for your own account).
2. Valid OAuth redirect URI: `http://localhost:8765/oauth/callback/instagram/`.
3. Setup → App ID + secret → Save. Accounts → Instagram / Direct → Connect → Test.

### Posting service
Zernio (formerly Late) is wired in as the service adapter: first two connected accounts are free, posts are
unlimited, uploads go through a presigned URL and the API returns the post link. Create an API key on their
dashboard, connect your social accounts there, paste the key in Setup, then add accounts here with route
*Service*. Other services (Post Bridge, Blotato, Upload-Post) have similar APIs; the adapter lives in
`clipdash/publish/service.py`.

## Rules the app holds to

- Official routes only: platform APIs or an audited posting service. No browser automation, cookies or
  private endpoints.
- A person approves every clip; there is no auto-approve.
- No inflated numbers: nothing generates views, and a clip goes to at most one account per platform.
- Real footage only. Captions transcribe what was said. Hooks are sentences from the transcript; there is no
  model-written text.
- Credentials stay local in `data/secrets.json` (gitignored, user-only permissions). `.env.example` lists them.

## Where things live

```
data/            everything the app writes (gitignored)
  clipdash.db    SQLite state: campaigns, sources, videos, moments, clips, accounts, posts, jobs
  clips/         rendered clips and final trimmed exports
  cache/         downloaded segments, chat replays (pruned automatically, cap in Settings)
  frames/        stills for the facecam picker and thumbnails
  tools/         TwitchDownloaderCLI
  outbox/        files staged for assisted posting
  secrets.json   your credentials
clipdash/        the Python app (FastAPI server, pipeline, publishers) and the UI in clipdash/web
tests/           unit tests (moment scoring, rules, captions, API) and an online end-to-end smoke test
```

## Running the tests

```
.venv/bin/python -m pytest                       # offline unit + API tests, dry-run publishers
CLIPDASH_E2E=1 .venv/bin/python -m pytest tests/test_e2e_smoke.py   # scans and renders one real clip (network)
```
Tests never post to real accounts: publishers run in dry-run mode under test, and *Dry run* can also be
switched on in Settings for manual testing.

Before pushing, `.venv/bin/python scripts/secret_scan.py` checks the tree for credentials. To run it on every
push automatically: `printf '#!/bin/sh\n.venv/bin/python scripts/secret_scan.py\n' > .git/hooks/pre-push && chmod +x .git/hooks/pre-push`.

## Troubleshooting

- **"Python not found"** – install from python.org, tick *Add to PATH*, run the installer again.
- **Download fails / "Sign in to confirm you're not a bot"** – yt-dlp needs updating; run the installer
  again (it upgrades packages).
- **Captions missing on Windows** – the bundled ffmpeg includes libass; if a font error appears, install
  the Arial font or change the font name in `clipdash/cut.py`.
- **Disk full** – Settings → *Clear cache now*, lower the cache cap, or move `data/` with `CLIPDASH_DATA`.
- **Twitch chat fails** – Setup → download TwitchDownloaderCLI; on macOS the quarantine flag is removed
  automatically.
- **Port 8765 busy** – set `CLIPDASH_PORT` before launching.

## What was not verified

Built on macOS (Apple Silicon). Things that could not be exercised from here:

- The Windows installer and launcher, and caption burn-in with the Windows ffmpeg build (its library list
  includes libass, so it should work).
- Live posting through the TikTok, Instagram and Zernio adapters (written from the official docs read on
  19 November 2026, tested only in dry-run mode). TikTok's desktop PKCE docs describe a hex-encoded code
  challenge; a setting switches to the standard base64url form if the hex one is rejected.
- Whether TikTok's inbox/draft flow is exempt from the "private only" rule for unaudited apps (docs are silent).
