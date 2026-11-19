# Build brief: clip dashboard for GTA VI clipping campaigns

I want you to build a local web app that turns streamers' GTA VI videos into vertical, captioned clips of 61 seconds or more for paid clipping campaigns, lets me review them, and posts or schedules them to my YouTube, Instagram and TikTok accounts. It has to end up as a GitHub repository that a friend can clone and run on their own computer with their own accounts.

This folder is the repo root. A working command-line prototype is at `~/PycharmProjects/lacy-clipper`. Read all of it before you plan, because most of the hard problems are already solved there. My friend won't have that folder, so port what you need; don't depend on it.

## How clipping campaigns work

Streamers and brands run campaigns on marketplaces such as Whop Content Rewards. A campaign pays clippers per 1,000 views (often $1 to $2) for clips of one creator, up to a cap per clip and a total budget. Each campaign publishes rules, and a person on the brand side reviews every submission. Clips that break a rule are rejected, and some campaigns remove a clipper permanently after one violation.

The campaign I'm in now is the LACY GTA VI marathon: https://reachclipping.com/LACYGTA. Its rules as I understand them (re-read the page yourself):

- Footage must come from LACY's GTA VI marathon stream that started on 18 November 2026.
- LACY must be the main focus of the clip.
- The post caption must mention LACY by name and include #LACY.
- No logos or watermarks on the clip.
- No AI-generated content.
- No botting or any other manipulation of views or engagement.
- The link to each post must be submitted within 30 minutes of posting.
- At least half of the account's audience must be in the US, Canada, Australia, the UK or New Zealand.
- One violation removes you from every campaign the agency runs.

Content Rewards also rejects clips for the wrong platform, missing tags, reused content, and editing or deleting a post after submitting it. I don't know of an API for submitting links, so submission stays manual and the app's job is to make it quick.

Two things follow from this:

1. The campaign is the central object. Sources, clips, rules, captions and posts all belong to a campaign. Clipping a streamer who has no campaign earns nothing and invites copyright strikes, so the app only processes sources attached to a campaign I've joined.
2. Rules are data. Every rule a machine can check (length, required name, required hashtag, source date window, watermark off, allowed platform) is validated before a clip can be approved. Rules a machine can't check (is the streamer the focus, does the clip make them look bad) appear as a checklist I tick in review.

## The prototype and what it taught me

The prototype has four steps: `scan` finds moments, `cut` downloads just that segment, lays it out 9:16 with the facecam on top and gameplay below, burns in word-timed captions and drafts a post caption, `review` is a local approve/reject page, and `upload` sends to YouTube and stages files for the other platforms. It has run on real footage: on a 22-hour Twitch VOD with 222,354 chat messages it found the moments chat reacted to, and from a YouTube upload it produced two finished clips.

Each of these cost me time. Keep them:

- **Twitch chat replay.** Twitch's GQL endpoint rejects plain scripts with an integrity check, and the `chat-downloader` package is broken for the same reason. TwitchDownloaderCLI works. Fetch the right release asset for the OS and architecture on first use, and clear the quarantine flag on macOS.
- **ffmpeg.** My Mac has no Homebrew, and conda spent over ten minutes solving without installing anything. The `imageio-ffmpeg` pip package ships a binary that handled everything, including burned-in ASS captions on macOS. Pass its path to yt-dlp with `--ffmpeg-location`. Confirm the Windows build can burn captions too, and watch path escaping in the `ass=` filter there.
- **Whisper.** `faster-whisper` crashes with current PyAV when given a file path. Decode with ffmpeg to 16 kHz mono float32 and pass the array.
- **Download only what's needed.** yt-dlp's `--download-sections` with `--force-keyframes-at-cuts` fetches a 60 to 90 second segment. Never download a whole VOD; they run 20 hours or more.
- **Disk.** My Mac ran out of space during setup. Delete segments and audio once a clip is rendered, cap the cache, and show disk use in the UI.
- **Highlight signals.** Chat message-rate spikes weighted by hype words ("CLIP", "LMAO", "WTF", emotes) are the best signal I have. Ignore the first two minutes of a stream, when chat floods. YouTube stream VODs have a chat replay (`--write-subs --sub-langs live_chat`). Loudness alone is weak on edited uploads: it found two low-scoring moments in one video. For regular uploads, try YouTube's "most replayed" data (I believe yt-dlp exposes it as `heatmap` in the info JSON, but I haven't tested that) and transcript cues, and combine whatever signals a video has. Show me why each moment was picked.
- **Drafted post captions.** The prototype picks a sentence from the transcript, and one came out as a profane fragment. Post text needs complete, clean hooks, a profanity filter, and to stay editable.
- **Facecam crop.** Every streamer's layout differs, and the prototype takes four numbers in a YAML file. This needs a visual picker: show a frame, drag a box, save it per source.
- **Me and terminals.** I kept running commands in the wrong folder or outside the virtual environment. After install, nothing should need a terminal.

## What the app does

- **Setup.** A first-run screen checks dependencies, downloads what's missing, connects accounts and tests each one, with plain-language fixes when something fails.
- **Campaigns.** I create a campaign by pasting its URL or rules: creator, required name and hashtags, minimum and maximum length (default minimum 61 seconds), allowed platforms, source date window, watermark allowed or not, submission deadline in minutes, pay rate, per-clip cap, and the link to its submission page.
- **Sources.** Per campaign I add YouTube channels, Twitch channels or single video URLs. The app watches for new GTA VI videos and queues them. As starting suggestions offer IShowSpeed, CaseOh, Lacy and Kai Cenat, resolving their channels by lookup instead of hardcoded IDs, and let me add or remove any channel. Many streamers keep edited highlights on one channel and full streams on another; handle both.
- **Pipeline.** Each video shows its progress: scanning, moments found, rendering. Jobs run in the background and survive a restart.
- **Review.** A grid of 9:16 players. For each clip I can trim the start and end so it opens on something worth watching, edit the title, caption and hashtags per platform, see the rule checks pass or fail, then approve or reject. Clips that fail a machine-checkable rule can't be approved. Post text is a hook plus the campaign's required tags plus a few relevant ones (game, streamer, moment), without tag stuffing. Nothing guarantees views, so aim for rule-compliant clips with a strong first second and clean captions.
- **Publish.** For an approved clip I pick accounts and choose "Post now" or "Schedule" with a date, time and time zone. A queue or calendar shows what's pending, what went out, and the link for each platform, with retry on failure.
- **Submission tracker.** When a post goes live, start the campaign's countdown (30 minutes for LACY) with one click to copy the link and open the submission page, and a "submitted" tick. Warn me before anything that would edit or delete a submitted post.
- **Stats** (after everything else works). Views per clip where the APIs provide them, and estimated earnings from the campaign's rate and cap.

## Posting: what the platforms allow

I checked the official docs in November 2026. Re-read them before building each integration, because they change.

- **YouTube.** API projects created after July 2020 that haven't passed YouTube's compliance audit can only upload videos as private. Scheduling is native: `status.publishAt` together with `privacyStatus: private`.
- **TikTok.** Content posted by an unaudited app is restricted to private viewing. The app must query creator info first and offer the privacy options it returns. Local file upload is supported. There is no scheduling parameter.
- **Instagram.** Needs a professional account. Uploading a local file (resumable upload) is only available through Facebook Login for Business; otherwise the video must sit at a public URL, which a local app doesn't have. The API has no scheduling, and there's a cap of roughly 100 API posts per 24 hours.

A personal app is unlikely to pass those audits quickly, so put posting behind one publisher interface with three routes, and show in the UI which route each connected account uses:

1. **Direct.** The official API with my own developer app. Where the platform permits public posting, do it. Where it doesn't, say exactly what state the upload is in ("uploaded as private, finish in YouTube Studio") and link there. Check whether TikTok's upload-to-inbox flow gives unaudited apps a usable draft handoff.
2. **Posting service.** An adapter for one third-party publishing API whose app the platforms have already audited. Find candidates, compare their current prices and terms, and ask me before choosing. I connect my accounts on their side and the app calls their API with my key.
3. **Assisted.** Always available. Reveal the file, copy the caption, open the platform's upload page, and let me paste back the link.

The app's own scheduler fires scheduled posts, which only works while the app is running; say so in the UI. On the direct YouTube route use YouTube's native scheduling.

## Rules the app must hold to

- **Official routes only.** Posting goes through platform APIs or an audited posting service. No browser automation, saved cookies or private endpoints: they break the platforms' terms and count as botting under campaign rules.
- **A person approves every clip.** "Post now" and "Schedule" are actions I take on clips I've watched, one at a time or as a selected batch. There is no auto-approve setting, because one bad clip can end a campaign membership.
- **No inflated numbers.** Nothing that generates views or engagement, and no posting the same clip to several accounts on one platform.
- **Real footage only.** No generated video, voices or quotes. Captions transcribe what was said. If you add model-written text such as suggested hooks, make it optional, off by default, and shown to me before use.
- **Credentials stay local.** Each user supplies their own. Tokens and keys live outside git (the OS keychain or a gitignored file), `.env.example` documents them, and nothing of mine is committed. Scan for secrets before every push.

## Running and sharing it

- macOS on Apple Silicon for me, probably Windows for my friend (ask me). No Docker, Homebrew or conda.
- One documented install step and one launcher I can double-click. It starts everything and opens the browser.
- Python for the pipeline, since the prototype is Python. Node is fine for building the UI but must not be needed to run it, so ship the built assets.
- SQLite for state.
- A README written for my friend: what this is, install, launch, connect accounts, make a first clip. Under ten minutes to read, with screenshots.

## Look and feel

Dark interface. My channel, Vice City Daily Clips, uses a neon sunset palette: teal `#1fd1d6`, pink `#ff4f9a`, yellow `#ffe066`, deep purple `#1a0b3a`. Use those as accents and keep the clips as the focus: large 9:16 previews, clear status, little chrome. It should feel like a product I'd pay for, not an admin template.

## How I'd like you to work

1. Read the prototype, then give me a short plan and ask your open questions in one batch: repo name and visibility, my friend's operating system, whether I'll pay for a posting service, and which accounts go with which campaigns.
2. Build in milestones, each ending with something I can run:
   - Pipeline and review inside the new app, proven end to end on a short public YouTube video.
   - Campaigns, sources, watching for new videos, rule checks.
   - Assisted posting and the submission tracker.
   - Account connection, direct and service posting, scheduling.
   - Packaging, README, and a test from a fresh clone.
3. Write unit tests for moment scoring, rule validation and caption building, and one end-to-end smoke test that stops before posting. Give posting adapters a dry-run mode. Tests never post to real accounts.
4. Commit as you go. After the first milestone create a private GitHub repository with `gh` and push. If `gh` isn't installed or signed in, finish locally and give me the exact steps.
5. When you can't verify something from here (an API behaviour, the Windows build), say so in your summary instead of assuming it works.

## Done means

- A fresh clone on a clean machine installs and launches from the README alone.
- Adding a YouTube URL to a campaign yields clips that are 61 seconds or longer, 1080×1920, captioned, with the campaign's required name and hashtag in the post text. Clips that fail a rule can't be approved.
- An approved clip can be posted now or scheduled on all three platforms through at least the assisted route, and through the direct or service route wherever the platform allows it, with accurate status shown.
- A live post starts the submission countdown.
- The repository history contains no credentials.
