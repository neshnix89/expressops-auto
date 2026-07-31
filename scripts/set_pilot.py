"""
Set mo_ref_order_monitor.pilot_containers in config.yaml safely.

Hand-editing YAML is where the indentation mistakes happen, and an absent
pilot_containers means the monitor runs FLEET-WIDE over every container in the
JQL — so this does the edit, backs the file up, and verifies by re-parsing.

Usage:
    python scripts\\set_pilot.py NPIOTHER-5589 NPIOTHER-5322   # restrict
    python scripts\\set_pilot.py --clear                        # fleet-wide
    python scripts\\set_pilot.py --show                         # report only
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "config" / "config.yaml"
SECTION = "mo_ref_order_monitor:"
KEY = "pilot_containers"


def parsed_value():
    import yaml
    data = yaml.safe_load(CFG.read_text(encoding="utf-8-sig")) or {}
    return (data.get("mo_ref_order_monitor") or {}).get(KEY, "MISSING")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if not CFG.exists():
        print(f"[pilot] config not found: {CFG}")
        return 1

    print(f"[pilot] config: {CFG}")
    print(f"[pilot] current {KEY}: {parsed_value()!r}")
    if "--show" in args:
        return 0

    clear = "--clear" in args
    keys = [a.strip().upper() for a in args if not a.startswith("--") and a.strip()]
    if not clear and not keys:
        print("[pilot] give container key(s), or --clear for fleet-wide. Nothing changed.")
        return 2

    value = "[]" if clear else "[" + ", ".join(f'"{k}"' for k in keys) + "]"
    new_line = f"  {KEY}: {value}"

    text = CFG.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    # Locate the section; edit only within it so another task's keys are safe.
    sec_idx = next((i for i, l in enumerate(lines) if l.strip() == SECTION), None)
    if sec_idx is None:
        print(f"[pilot] ERROR: no '{SECTION}' block found.")
        return 3

    end_idx = len(lines)
    for j in range(sec_idx + 1, len(lines)):
        s = lines[j]
        if s.strip() and not s.startswith((" ", "\t")):   # next top-level key
            end_idx = j
            break

    existing = next((j for j in range(sec_idx + 1, end_idx)
                     if re.match(rf"\s{{2}}{KEY}\s*:", lines[j])), None)

    backup = CFG.with_name(f"config.{datetime.now():%Y%m%d_%H%M%S}.bak")
    shutil.copy2(CFG, backup)
    print(f"[pilot] backup: {backup.name}")

    if existing is not None:
        lines[existing] = new_line
        print("[pilot] replaced existing line")
    else:
        lines.insert(sec_idx + 1, new_line)
        print("[pilot] inserted new line")

    CFG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Verify against a real parse, then roll back if it didn't take.
    try:
        got = parsed_value()
    except Exception as exc:  # noqa: BLE001
        shutil.copy2(backup, CFG)
        print(f"[pilot] ERROR: YAML broke ({exc}) — backup restored.")
        return 4

    expected = [] if clear else keys
    if got != expected:
        shutil.copy2(backup, CFG)
        print(f"[pilot] ERROR: verification failed (got {got!r}) — backup restored.")
        return 5

    print(f"[pilot] OK — {KEY} is now {got!r}")
    if not got:
        print("[pilot] NOTE: empty list = FLEET-WIDE (every container in the JQL).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
