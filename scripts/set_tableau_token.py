"""
Interactively set the Tableau PAT (name + secret) in config.yaml.

Tableau Server PATs auto-expire after ~15 days of inactivity, so signin starts
returning HTTP 401 (code 401001 "invalid personal access token") and this needs
to be re-run with a freshly-minted token. Prompts for the new token name
(blank = keep current) and secret (hidden input, never echoed), replaces the
`pat_name:` / `pat_secret:` lines under the `tableau:` section in place, clears
the file's read-only attribute if set, and writes UTF-8 without a BOM.

Run on the laptop, where the real config.yaml lives. Mint the new token first in
Tableau: <base>/#/users -> your user -> Settings -> Personal Access Tokens.
"""
from __future__ import annotations

import getpass
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_loader import CONFIG_PATH  # noqa: E402

_TOP_RE = re.compile(r"^([A-Za-z_][\w-]*):")
_NAME_RE = re.compile(r"^(\s+)pat_name:\s*.*$")
_SECRET_RE = re.compile(r"^(\s+)pat_secret:\s*.*$")


def _current_name(lines: list[str]) -> str | None:
    section = None
    for ln in lines:
        top = _TOP_RE.match(ln)
        if top and not ln[:1].isspace():
            section = top.group(1)
            continue
        if section == "tableau" and _NAME_RE.match(ln):
            return ln.split(":", 1)[1].strip().strip('"')
    return None


def main() -> None:
    p = Path(CONFIG_PATH)
    if not p.exists():
        print(f"[set_tableau_token] config not found: {p}")
        return

    lines = p.read_text(encoding="utf-8-sig").splitlines()
    print(f"[set_tableau_token] current tableau.pat_name = {_current_name(lines)!r}")

    new_name = input("New PAT name (blank = keep current): ").strip()
    secret = getpass.getpass("New PAT secret (input hidden): ").strip()
    if not secret:
        print("[set_tableau_token] secret required — nothing changed.")
        return

    section = None
    did_name = did_secret = False
    for i, ln in enumerate(lines):
        top = _TOP_RE.match(ln)
        if top and not ln[:1].isspace():
            section = top.group(1)
            continue
        if section != "tableau":
            continue
        if new_name and not did_name:
            m = _NAME_RE.match(ln)
            if m:
                lines[i] = f'{m.group(1)}pat_name: "{new_name}"'
                did_name = True
                continue
        if not did_secret:
            m = _SECRET_RE.match(ln)
            if m:
                lines[i] = f'{m.group(1)}pat_secret: "{secret}"'
                did_secret = True

    if not did_secret:
        print("[set_tableau_token] WARNING: no 'pat_secret:' line under 'tableau:' — not updated.")
        return

    # config.yaml is often marked read-only on the laptop; clear it before write.
    try:
        os.chmod(p, stat.S_IWRITE)
    except OSError:
        pass
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")  # UTF-8, no BOM
    who = "pat_secret" + (" + pat_name" if did_name else "")
    print(f"[set_tableau_token] updated tableau {who} (secret not echoed).")
    print("[set_tableau_token] done — now double-click run_probe.bat to retry.")


if __name__ == "__main__":
    main()
