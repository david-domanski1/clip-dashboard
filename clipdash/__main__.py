"""Launcher: python -m clipdash  → starts the server and opens the browser."""
import os
import socket
import sys
import threading
import time
import webbrowser

from . import config


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((config.HOST, port)) != 0


def main() -> None:
    import uvicorn
    config.ensure_dirs()
    url = f"http://{config.HOST}:{config.PORT}/"
    if not _port_free(config.PORT):
        print(f"Clip Dashboard is already running at {url} (or the port is busy). Opening it.")
        webbrowser.open(url)
        return
    if "--no-browser" not in sys.argv:
        def open_later():
            time.sleep(1.5)
            webbrowser.open(url)
        threading.Thread(target=open_later, daemon=True).start()
    print(f"Clip Dashboard at {url}  (close this window to stop; scheduled posts only fire while it runs)")
    uvicorn.run("clipdash.app:app", host=config.HOST, port=config.PORT, log_level=os.environ.get("CLIPDASH_LOG", "warning"))


if __name__ == "__main__":
    main()
