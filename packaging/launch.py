"""Entry point of the packaged (PyInstaller) app.

The core starts helpers as `sys.executable -m yt_dlp …` and `sys.executable -m piper …`. In a frozen build
sys.executable is this app, not a Python interpreter, so without the dispatch below those calls would start a second
Clip Dashboard. `-m <module> args…` therefore runs that bundled module the way `python -m` would, and anything else
starts the dashboard."""
import multiprocessing
import runpy
import sys


def main() -> None:
    multiprocessing.freeze_support()
    if len(sys.argv) >= 3 and sys.argv[1] == "-m":
        module = sys.argv[2]
        sys.argv = [module] + sys.argv[3:]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return
    from clipdash.__main__ import main as run_dashboard
    run_dashboard()


if __name__ == "__main__":
    main()
