"""
Patch the legacy Excel->Jira publisher so it stops deleting content below its
own table.

THE BUG
-------
excel_to_jira.py bounds "its" section by searching for the NEXT
"h2. MO BUILD STATUS - " marker. When its table is the last one in the
description, that search returns -1, so the tool treats *everything to the end
of the description* as its own section and discards it when rebuilding — which
wipes the `MO BUILD TRACKING` / `MO BUILD DWELL` tables written by
tasks/mo_ref_order_monitor.

THE FIX
-------
Bound the section at the next wiki heading of ANY kind (h1..h6), so anything
below is preserved.

This script is safe to re-run: it detects an already-patched file and does
nothing. It writes a timestamped .bak next to the original before editing.

Usage (company laptop):
    python scripts\\patch_legacy_excel_to_jira.py
    python scripts\\patch_legacy_excel_to_jira.py --path "D:\\other\\excel_to_jira.py"
    python scripts\\patch_legacy_excel_to_jira.py --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = (
    r"Y:\88-Technology-Innovation-SEA\_Public\ePMC_PCBA_NPI_Run_Sched"
    r"\e-File for NPI\NPI (PCBA) Schedule\SEV&SPI follow up\Automation"
    r"\src\backend\excel_to_jira.py"
)

OLD = (
    "        next_table_start = current_desc.find(next_marker_prefix, "
    "table_start + len(table_marker))"
)

NEW = """        # Bound this table's section at the NEXT wiki heading of any kind, not
        # just the next "MO BUILD STATUS" marker. Otherwise everything below the
        # last legacy table (e.g. the MO BUILD TRACKING / MO BUILD DWELL tables
        # written by tasks/mo_ref_order_monitor) is absorbed into this section
        # and destroyed when the table is rebuilt.
        _next = re.search(r'^h[1-6]\\.\\s', current_desc[table_start + len(table_marker):], re.M)
        next_table_start = (table_start + len(table_marker) + _next.start()) if _next else -1"""

MARKER = "Bound this table's section at the NEXT wiki heading"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", default=DEFAULT_PATH, help="path to excel_to_jira.py")
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = ap.parse_args()

    path = Path(args.path)
    print(f"[patch] target: {path}")

    if not path.exists():
        print("[patch] ERROR: file not found. Check the path / that Y: is mapped.")
        return 1

    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("[patch] already patched — nothing to do.")
        return 0

    if OLD not in text:
        print("[patch] ERROR: expected line not found. The file may have changed.")
        print("[patch] looking for:")
        print(f"        {OLD.strip()}")
        return 2

    if "import re" not in text:
        print("[patch] ERROR: 'import re' missing — the fix needs it. Aborting.")
        return 3

    if text.count(OLD) != 1:
        print(f"[patch] ERROR: expected 1 occurrence, found {text.count(OLD)}. Aborting.")
        return 4

    patched = text.replace(OLD, NEW, 1)

    if args.dry_run:
        print("[patch] dry-run: would replace 1 line with the heading-bounded version.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.{stamp}.bak")
    shutil.copy2(path, backup)
    print(f"[patch] backup written: {backup.name}")

    path.write_text(patched, encoding="utf-8")

    # Verify what actually landed on disk, not what we think we wrote.
    check = path.read_text(encoding="utf-8")
    if MARKER not in check or OLD in check:
        print("[patch] ERROR: verification failed — restoring backup.")
        shutil.copy2(backup, path)
        return 5

    print("[patch] OK — patched and verified.")
    print()
    print("[patch] NOTE: if the team runs the compiled JIRA_Publisher.exe rather")
    print("[patch]       than this .py, the exe must be REBUILT for the fix to")
    print("[patch]       take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
