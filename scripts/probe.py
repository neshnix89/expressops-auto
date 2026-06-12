"""
DISCOVERY PROBE — read-only scratchpad.

CURRENT PROBE: full inventory of the live "MR Status Report" folder so it can be
migrated into expressops-auto. Lists every file (path + size), then dumps the
text of each source file. Token-like blobs are REDACTED, so any hardcoded PATs
are masked in the output (we want them in config.yaml, not in the dump).

All read-only — local file reads only, no live-system calls.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MR_DIR = Path(r"C:\Users\tmoghanan\Documents\AI\MR Status Report")

# Extensions we will dump inline. Anything else is listed but not dumped.
_TEXT_EXT = {
    ".py", ".txt", ".yaml", ".yml", ".json", ".bat", ".cfg", ".ini",
    ".md", ".csv", ".html", ".htm", ".ps1", ".sql",
}
# Don't dump giant or binary-ish files.
_MAX_DUMP_BYTES = 200_000
_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules"}

_REDACT = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")


def _walk(base: Path):
    for p in sorted(base.rglob("*")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        yield p


def main() -> None:
    print(f"=== INVENTORY: {MR_DIR} ===")
    if not MR_DIR.exists():
        print("NOT FOUND")
        return

    files = [p for p in _walk(MR_DIR) if p.is_file()]
    print(f"({len(files)} files)\n")

    # 1) flat listing with sizes
    print("--- FILE LIST (relative path | bytes) ---")
    for p in files:
        rel = p.relative_to(MR_DIR)
        try:
            size = p.stat().st_size
        except OSError as e:
            size = f"ERR:{e}"
        print(f"{size:>12} | {rel}")
    print()

    # 2) dump each text file (redacted)
    for p in files:
        rel = p.relative_to(MR_DIR)
        if p.suffix.lower() not in _TEXT_EXT:
            print(f"=== SKIP (binary/other): {rel} ===\n")
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        if size > _MAX_DUMP_BYTES:
            print(f"=== SKIP (too big, {size} bytes): {rel} ===\n")
            continue
        print(f"=== FILE: {rel}  ({size} bytes; token-like strings redacted) ===")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  READ ERROR: {e}\n")
            continue
        for i, ln in enumerate(text.split("\n"), 1):
            print(f"{i:4} | {_REDACT.sub('***REDACTED***', ln)}")
        print()


if __name__ == "__main__":
    main()
