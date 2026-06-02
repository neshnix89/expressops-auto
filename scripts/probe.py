"""
DISCOVERY PROBE — read-only scratchpad.

CURRENT PROBE: dump the full source of the live MR Status Report script so it
can be migrated into expressops-auto. Token-like blobs are REDACTED, so the
hardcoded PATs are masked in the output (we want them in config.yaml, not here).

All read-only — local file read only, no live-system calls.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MR_FILE = Path(r"C:\Users\tmoghanan\Documents\AI\MR Status Report\Pilot_DMR_Report.py")
_REDACT = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")


def main() -> None:
    print(f"=== {MR_FILE} ===")
    if not MR_FILE.exists():
        print("NOT FOUND")
        return
    lines = MR_FILE.read_text(encoding="utf-8", errors="replace").split("\n")
    print(f"({len(lines)} lines; token-like strings redacted)\n")
    for i, ln in enumerate(lines, 1):
        print(f"{i:4} | {_REDACT.sub('***REDACTED***', ln)}")


if __name__ == "__main__":
    main()
