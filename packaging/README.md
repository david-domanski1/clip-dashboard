# Packaging Clip Dashboard

Turns the source checkout into a double-clickable app so buyers do not need Python:

| Platform | Output | Script |
|---|---|---|
| macOS | `dist/Clip Dashboard.app` + `dist/Clip-Dashboard-<version>-mac-<arch>.zip` | `bash scripts/build_mac.sh` |
| Windows | `dist\Clip Dashboard\Clip Dashboard.exe` + `dist\Clip-Dashboard-<version>-win-x64.zip` | `scripts\build_win.bat` |

Files here: `clipdash.spec` (PyInstaller one-folder build), `launch.py` (entry point), `entitlements.plist`
(hardened-runtime exceptions for signing on macOS).

## Build

1. Install the dev requirements into the project's environment (PyInstaller is only in `requirements-dev.txt`):
   `.venv/bin/python -m pip install -r requirements-dev.txt` (Windows: `.venv\Scripts\python -m pip install -r requirements-dev.txt`).
2. Run the script for the platform you are on. PyInstaller cannot cross-compile: build the Mac app on a Mac and the
   Windows app on Windows (a Windows VM or a GitHub Actions `windows-latest` runner works).
3. Bump `clipdash/__version__` first, then attach the zip(s) to a GitHub release tagged `v<version>` on the repo in the
   `update_repo` setting. The in-app update check (`/api/updates`) compares against that tag and links to the release page.

What gets bundled (see the spec): `clipdash/web` (the UI), the ffmpeg binary from `imageio-ffmpeg`, `faster-whisper`
data (VAD model) and `ctranslate2` / `onnxruntime` native libraries, `tzdata`, `certifi`, `yt-dlp`, `piper` and the
Google API client's discovery files. Whisper models and piper voices are not bundled: they download on first use into
the data folder, as in the source install.

The packaged app keeps its data in the per-user app-data folder, not next to the program (`config.default_data_dir()`):
macOS `~/Library/Application Support/Clip Dashboard`, Windows `%APPDATA%\Clip Dashboard`. `CLIPDASH_DATA` still overrides it.
The licence file (`license.json`, mode 0600) lives there too, so reinstalling or updating the app keeps the licence.

Checked on 2026-11-21 (macOS arm64, Python 3.13, PyInstaller 6, unsigned): a clean build takes about 2.5 minutes
including dependency install and gives a 417 MB `.app`; the app starts, serves the UI and feature scripts, its setup
checks find ffmpeg, yt-dlp and faster-whisper, `-m yt_dlp` / `-m piper` run the bundled modules, the free-plan gate answers
402, and `DEV-` keys are refused. Not yet checked: a full scan + render + transcription inside the packaged app, the
Windows build, and signing/notarisation.

## Signing and notarisation (macOS)

Unsigned apps downloaded from the internet are blocked by Gatekeeper ("cannot be opened because the developer cannot
be verified"). Selling the app needs an Apple Developer Program membership (USD 99/year) and a *Developer ID
Application* certificate in the login keychain.

```bash
# once: find the identity and store notary credentials (app-specific password from appleid.apple.com)
security find-identity -v -p codesigning            # -> "Developer ID Application: David Domanski (TEAMID)"
xcrun notarytool store-credentials clipdash-notary --apple-id "<apple id email>" --team-id TEAMID --password "<app-specific password>"

# every release: build, sign every nested binary with hardened runtime, notarise, staple, re-zip
CLIPDASH_SIGN_ID="Developer ID Application: David Domanski (TEAMID)" \
CLIPDASH_NOTARY_PROFILE=clipdash-notary \
bash scripts/build_mac.sh
```

What the script does with those set, if you need to run it by hand:

```bash
codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist \
  --sign "Developer ID Application: David Domanski (TEAMID)" <each .dylib/.so/Mach-O inside the app>
codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist \
  --sign "Developer ID Application: David Domanski (TEAMID)" "dist/Clip Dashboard.app"
ditto -c -k --sequesterRsrc --keepParent "dist/Clip Dashboard.app" dist/Clip-Dashboard.zip
xcrun notarytool submit dist/Clip-Dashboard.zip --keychain-profile clipdash-notary --wait
xcrun notarytool log <submission id> --keychain-profile clipdash-notary     # when it says "Invalid": lists each unsigned file
xcrun stapler staple "dist/Clip Dashboard.app"
spctl --assess --type execute --verbose "dist/Clip Dashboard.app"           # expect: accepted, source=Notarized Developer ID
```

## Signing (Windows)

Without a signature, SmartScreen warns "Windows protected your PC" until the file has a download reputation. An OV or
EV code-signing certificate (or Azure Trusted Signing) removes or shortens that. With the certificate installed in the
user store and the Windows SDK's `signtool` on PATH:

```bat
set CLIPDASH_SIGN_CERT=<certificate SHA-1 thumbprint>
scripts\build_win.bat
rem which runs:
signtool sign /sha1 %CLIPDASH_SIGN_CERT% /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Clip Dashboard\Clip Dashboard.exe"
signtool verify /pa "dist\Clip Dashboard\Clip Dashboard.exe"
```

For an installer instead of a zip, point Inno Setup or NSIS at `dist\Clip Dashboard\` and sign the installer `.exe` the
same way.

## Pitfalls

- **`sys.executable` is the app.** The core runs `sys.executable -m yt_dlp …` and `sys.executable -m piper …`. In a
  frozen build that would start a second dashboard, so `launch.py` treats `-m <module> args` as "run that bundled
  module". Keep that dispatch if you change the entry point, and add new `-m` helpers to `hiddenimports` in the spec.
- **Hidden imports.** uvicorn imports `clipdash.app:app` from a string and feature modules are discovered with
  `pkgutil.iter_modules`; the spec collects every `clipdash` submodule. A new dependency imported lazily (inside a
  function) may need adding to `hiddenimports`; the symptom is a `ModuleNotFoundError` in the console window.
- **Notarisation rejects unsigned nested binaries** (ffmpeg, ctranslate2, onnxruntime dylibs). Sign inside-out, never
  rely on `codesign --deep` for signing (only for verifying). `xcrun notarytool log` names any file that was missed.
- **Architecture.** A build made on Apple Silicon runs only on Apple Silicon (and vice versa for Intel). Ship an arm64
  build; build an x86_64 one on an Intel Mac or a `macos-13` CI runner if Intel buyers matter. `target_arch="universal2"`
  only works when every wheel (numpy, ctranslate2, onnxruntime) is universal2, which they are not today.
- **Quitting.** The app is a local web server with a console window: closing that window stops it. The macOS `.app`
  launched from Finder has no console window; quit it from the Dock (Quit / Force Quit). A menu-bar Quit item would be
  a good follow-up.
- **Size.** Expect roughly 300 to 500 MB unpacked, mostly onnxruntime, ctranslate2, numpy and ffmpeg. Do not enable
  UPX: it breaks codesigning on macOS and triggers antivirus false positives on Windows.
- **Antivirus false positives** on unsigned PyInstaller executables are common on Windows; signing is the fix.
- **Data folder moved.** A buyer upgrading from the source install keeps their old state in `<checkout>/data`; move it
  to the app-data folder above or start the app once with `CLIPDASH_DATA=<checkout>/data`.
- **Licence settings.** `DEV-` keys are refused in a packaged build (the `license_dev_keys` default follows
  `sys.frozen`). Before a release, set the real `license_store_id`, `license_product_id` and `license_checkout_url`
  defaults in `clipdash/features/plan.py` so a key bought for another Lemon Squeezy product cannot unlock Pro.
