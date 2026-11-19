# Agent notes for clip-dashboard

- Python app in `clipdash/` (FastAPI + SQLite + plain JS UI in `clipdash/web`, no build step, no Node at runtime).
- Use `.venv/bin/python`; install with `pip install -r requirements-dev.txt` (or `uv pip install --python .venv/bin/python`).
- Tests: `.venv/bin/python -m pytest` (offline, dry-run publishers). Online smoke test: `CLIPDASH_E2E=1 pytest tests/test_e2e_smoke.py`.
- Run the app: `.venv/bin/python -m clipdash` (add `--no-browser` in automation). State lives in `data/` (gitignored).
- Before every push run `.venv/bin/python scripts/secret_scan.py`. Never commit anything from `data/`.
- Product rules that must not be weakened: a person approves every clip (no auto-approve), official posting routes only,
  one account per platform per clip, no generated media or model-written captions, credentials stay local.
- Platform API facts were read on 2026-11-19 and are summarised in the module docstrings under `clipdash/publish/`; re-read the
  official docs before changing an integration.
