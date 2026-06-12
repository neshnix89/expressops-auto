"""
DISCOVERY PROBE — read-only scratchpad.

CURRENT PROBE: find (a) the existing Windows scheduled task that runs the MR
report — its name, schedule, and exact command — and (b) where EDMAdmin.exe
lives, so the migrated task can run with the same EDM access. All read-only
(schtasks /query + filesystem existence checks; no writes, no live systems).
"""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEYWORDS = ("dmr", "pilot", "mr status", "mr_report", "mr report", "edmadmin",
            "pilot_dmr")
FIELDS = ("TaskName", "Status", "Next Run Time", "Schedule Type", "Start Time",
          "Start Date", "Repeat: Every", "Task To Run", "Start In", "Run As User",
          "Author")

EDM_CANDIDATES = [
    r"C:\Users\tmoghanan\EDMAdmin.exe",
    r"C:\Users\tmoghanan\Documents\AI\EDMAdmin.exe",
    r"C:\Users\tmoghanan\Documents\AI\MR Status Report\EDMAdmin.exe",
    r"C:\Users\tmoghanan\Documents\AI\expressops-auto\EDMAdmin.exe",
]
EDM_SEARCH_ROOTS = [
    r"C:\Users\tmoghanan\Documents\AI",
    r"C:\Users\tmoghanan\AppData\Local\Programs\Python",
]


def dump_scheduled_tasks() -> None:
    print("=== SCHEDULED TASKS matching MR / DMR / EDMAdmin ===")
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/fo", "CSV", "/v"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception as e:
        print(f"  schtasks failed: {e}")
        return

    reader = csv.DictReader(io.StringIO(out))
    seen = 0
    for row in reader:
        if not row or row.get("TaskName", "").startswith("TaskName"):
            continue  # repeated header rows
        blob = " ".join(str(v) for v in row.values()).lower()
        if not any(k in blob for k in KEYWORDS):
            continue
        seen += 1
        print(f"\n  --- match #{seen} ---")
        for f in FIELDS:
            if f in row and row[f] not in ("", "N/A"):
                print(f"    {f:14}: {row[f]}")
    if not seen:
        print("  (no scheduled task matched the keywords — it may be named "
              "differently; re-run with the real name if you know it)")


def find_edmadmin() -> None:
    print("\n=== EDMAdmin.exe location ===")
    found = []
    for c in EDM_CANDIDATES:
        if os.path.isfile(c):
            print(f"  FOUND (candidate): {c}")
            found.append(c)
    for rootdir in EDM_SEARCH_ROOTS:
        if not os.path.isdir(rootdir):
            continue
        for dirpath, _dirs, files in os.walk(rootdir):
            for fn in files:
                if fn.lower() == "edmadmin.exe":
                    p = os.path.join(dirpath, fn)
                    if p not in found:
                        print(f"  FOUND (search): {p}")
                        found.append(p)
    if not found:
        print("  EDMAdmin.exe NOT found in the candidate paths / search roots.")
        print("  (It is a renamed copy of python.exe used to bypass the EDM "
              "logon trigger — tell me where it is, or how the daily job runs.)")


def main() -> None:
    dump_scheduled_tasks()
    find_edmadmin()


if __name__ == "__main__":
    main()
