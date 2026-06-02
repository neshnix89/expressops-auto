"""
Interactively set the JIRA + Confluence PATs in config.yaml.

Prompts for each token with hidden input (getpass — not echoed to console or
logs), then replaces the `pat:` line under the `jira:` and `confluence:`
sections in place, preserving the rest of the file and writing UTF-8 without a
BOM. Run on the laptop (where the real config.yaml lives).
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_loader import CONFIG_PATH

_TOP_RE = re.compile(r"^([A-Za-z_][\w-]*):")
_PAT_RE = re.compile(r"^(\s+)pat:\s*.*$")


def main() -> None:
    p = Path(CONFIG_PATH)
    if not p.exists():
        print(f"[set_tokens] config not found: {p}")
        return

    # utf-8-sig read also strips any stray BOM.
    lines = p.read_text(encoding="utf-8-sig").splitlines()

    jira = getpass.getpass("Enter NEW JIRA PAT (input hidden): ").strip()
    conf = getpass.getpass("Enter NEW Confluence PAT (input hidden): ").strip()
    if not jira or not conf:
        print("[set_tokens] both tokens required — nothing changed.")
        return

    targets = {"jira": jira, "confluence": conf}
    section: str | None = None
    changed: list[str] = []

    for i, ln in enumerate(lines):
        top = _TOP_RE.match(ln)
        if top and not ln[:1].isspace():
            section = top.group(1)
            continue
        pat = _PAT_RE.match(ln)
        if pat and section in targets and section not in changed:
            lines[i] = f'{pat.group(1)}pat: "{targets[section]}"'
            changed.append(section)

    for s in targets:
        if s not in changed:
            print(f"[set_tokens] WARNING: no 'pat:' line under '{s}:' — not updated.")

    if not changed:
        print("[set_tokens] no changes written.")
        return

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")  # UTF-8, no BOM
    print(f"[set_tokens] updated pat for: {', '.join(sorted(set(changed)))}")
    print("[set_tokens] done (tokens were not echoed).")


if __name__ == "__main__":
    main()
