"""
Audit runner — applies the read-only guard, runs the container_template_audit
scan against live JIRA in dry-run mode, and tees the output to
console + outputs/_audit_latest.txt (plus a timestamped copy).

Invoked by run_audit.bat; not meant to be run by hand.

Safety: scripts.readonly_guard is imported FIRST, so every write to
JIRA/Confluence is hard-blocked at the transport layer. The scan is also run
with dry_run=True, so it never even attempts a Confluence publish. The result
is a pure read-only test of the audit rules against live data.
"""

from __future__ import annotations

import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# IMPORTANT: install the read-only guard BEFORE importing/running the audit.
import scripts.readonly_guard  # noqa: E402,F401

OUTPUTS = ROOT / "outputs"


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            st.write(s)

    def flush(self):
        for st in self._streams:
            st.flush()


def main() -> int:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest = OUTPUTS / "_audit_latest.txt"

    rc = 0
    console = sys.__stdout__
    with open(latest, "w", encoding="utf-8") as fh:
        tee = _Tee(console, fh)
        old_stdout = sys.stdout
        sys.stdout = tee
        try:
            print(f"=== container_template_audit scan {stamp}  (LIVE read, dry-run) ===")
            # Import after stdout is teed so the logger's console handler also
            # writes into the output file.
            from tasks.container_template_audit.batch import run_scan
            rc = run_scan(mode="live", dry_run=True)
        except Exception:
            rc = 1
            print("\n[audit ERROR]")
            traceback.print_exc(file=tee)
        finally:
            print(f"\n=== done  rc={rc} ===")
            sys.stdout = old_stdout

    # keep a timestamped history copy alongside the stable latest file
    shutil.copy2(latest, OUTPUTS / f"audit_{stamp}.txt")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
