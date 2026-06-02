"""
Strip a UTF-8 BOM from config.yaml if present (local file hygiene).

The utf-8-sig fix in core/config_loader.py already makes a BOM harmless to the
tasks, but a BOM still trips other tools and editors, so we remove it. This is
byte-level and idempotent: it only rewrites when a BOM is actually present and
changes nothing else in the file (no line-ending translation, values untouched).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_loader import CONFIG_PATH

BOM = b"\xef\xbb\xbf"


def main() -> None:
    p = Path(CONFIG_PATH)
    if not p.exists():
        print(f"[clean_config] {p} not found; skipping.")
        return
    raw = p.read_bytes()
    if raw.startswith(BOM):
        p.write_bytes(raw[len(BOM):])
        print("[clean_config] removed UTF-8 BOM from config.yaml")
    else:
        print("[clean_config] no BOM present; config.yaml unchanged")


if __name__ == "__main__":
    main()
