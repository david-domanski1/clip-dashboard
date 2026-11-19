"""Scan tracked and staged files for credentials before a push. Exit 1 if anything looks like a secret.
Run: .venv/bin/python scripts/secret_scan.py   (also runs as a pre-push hook if you install it: see README)."""
import re
import subprocess
import sys

PATTERNS = [
    (r"AIza[0-9A-Za-z_-]{35}", "Google API key"),
    (r"ya29\.[0-9A-Za-z_-]{20,}", "Google OAuth access token"),
    (r"1//0[0-9A-Za-z_-]{20,}", "Google OAuth refresh token"),
    (r"[0-9]{12}-[0-9a-z]{32}\.apps\.googleusercontent\.com", "Google OAuth client id"),
    (r"GOCSPX-[0-9A-Za-z_-]{20,}", "Google OAuth client secret"),
    (r"sk_[0-9a-f]{64}", "Zernio API key"),
    (r"EAA[0-9A-Za-z]{40,}", "Facebook access token"),
    (r"act\.[0-9A-Za-z_-]{20,}", "TikTok access token"),
    (r"(?i)(client_secret|app_secret|api_key|access_token|refresh_token)\"?\s*[:=]\s*[\"'][0-9A-Za-z_\-\.]{16,}[\"']", "credential literal"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key"),
]
ALLOW = {".env.example", "scripts/secret_scan.py", "README.md"}


def files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], capture_output=True, text=True).stdout
    return [f for f in out.split("\n") if f and f not in ALLOW]


def main() -> int:
    bad = 0
    for f in files():
        try:
            text = open(f, encoding="utf-8", errors="ignore").read()
        except (OSError, IsADirectoryError):
            continue
        for pat, name in PATTERNS:
            for m in re.finditer(pat, text):
                line = text.count("\n", 0, m.start()) + 1
                print(f"{f}:{line}: looks like a {name}: {m.group(0)[:12]}…")
                bad += 1
    if bad:
        print(f"\n{bad} possible secret(s). Remove them (and rotate them) before pushing.")
        return 1
    print("No secrets found in tracked files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
