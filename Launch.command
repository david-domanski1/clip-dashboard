#!/bin/bash
# Starts Clip Dashboard and opens it in your browser. Keep this window open while you use the app;
# scheduled posts only fire while it runs. Close the window (or press Ctrl+C) to stop.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Not installed yet. Double-click install.command first."; read -r -p "Press Enter to close." _; exit 1
fi
exec .venv/bin/python -m clipdash
