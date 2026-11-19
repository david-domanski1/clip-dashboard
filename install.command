#!/bin/bash
# Clip Dashboard installer for macOS. Double-click this file (or run: bash install.command).
# Creates a private Python environment in .venv and installs everything, including ffmpeg (via the imageio-ffmpeg package).
cd "$(dirname "$0")" || exit 1
echo "== Clip Dashboard installer (macOS) =="

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then PY="$cand"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found."
  echo "Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then run this installer again."
  read -r -p "Press Enter to close." _; exit 1
fi
echo "Using $($PY --version) at $(command -v $PY)"

if [ ! -x .venv/bin/python ]; then
  echo "Creating private environment (.venv)…"
  "$PY" -m venv .venv || { echo "Could not create .venv"; read -r -p "Press Enter to close." _; exit 1; }
fi
echo "Installing packages (a few minutes the first time)…"
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q || { echo "Package install failed. Check your internet connection and run again."; read -r -p "Press Enter to close." _; exit 1; }
.venv/bin/python -c "import imageio_ffmpeg; print('ffmpeg:', imageio_ffmpeg.get_ffmpeg_exe())"
chmod +x Launch.command install.command 2>/dev/null
xattr -d com.apple.quarantine Launch.command install.command 2>/dev/null
echo
echo "Installed. Double-click Launch.command to start Clip Dashboard."
read -r -p "Press Enter to close." _
