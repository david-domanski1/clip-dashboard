# PyInstaller spec for Clip Dashboard: one-folder build ("onedir"), plus a .app bundle on macOS.
# Build with scripts/build_mac.sh or scripts\build_win.bat (they run: pyinstaller --noconfirm --clean packaging/clipdash.spec).
# Output: dist/Clip Dashboard/ (Windows folder with "Clip Dashboard.exe") and dist/Clip Dashboard.app (macOS).
# See packaging/README.md for signing, notarisation and pitfalls.
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # SPECPATH = folder holding this spec (packaging/)
APP_NAME = "Clip Dashboard"
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "clipdash" / "__init__.py").read_text()).group(1)

datas = [(str(ROOT / "clipdash" / "web"), "clipdash/web")]      # config.WEB = <bundle>/clipdash/web
binaries = []
hiddenimports = []

# ffmpeg: imageio-ffmpeg ships the binary inside its package (imageio_ffmpeg/binaries/ffmpeg-*).
datas += collect_data_files("imageio_ffmpeg", includes=["binaries/*"])
# Transcription: faster-whisper's VAD model + ctranslate2's native libraries; onnxruntime is used by the VAD and piper.
datas += collect_data_files("faster_whisper")
binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("onnxruntime")
datas += collect_data_files("onnxruntime")
# Time zones (zoneinfo falls back to tzdata on Windows), certificates for HTTPS, yt-dlp's extractor data.
datas += collect_data_files("tzdata")
datas += collect_data_files("certifi")
datas += collect_data_files("yt_dlp")
# Narration voices (piper) and the Google client's discovery documents.
datas += collect_data_files("piper")
datas += collect_data_files("googleapiclient")

# Imported by name at runtime: uvicorn loads "clipdash.app:app" from a string, feature modules are found with
# pkgutil.iter_modules, yt-dlp / piper run through packaging/launch.py's "-m" dispatch.
hiddenimports += collect_submodules("clipdash")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("piper", filter=lambda n: not n.startswith("piper.train"))   # training code needs torch
hiddenimports += ["tzdata", "zoneinfo"]

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "PyInstaller", "matplotlib", "IPython", "torch", "piper.train"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=True,                 # the window is how a person stops the server ("close this window to stop")
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,             # build on the architecture you ship (arm64 Mac, x64 Windows); see README
    codesign_identity=None,       # signing happens after the build (scripts/build_mac.sh), so every nested binary is covered
    entitlements_file=None,
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="com.clipdashboard.app",
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
        },
    )
