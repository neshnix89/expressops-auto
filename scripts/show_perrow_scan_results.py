"""Read scripts/_perrow_scan_results.json (written by the view scan)
and print the hits in a relay-friendly compact form."""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = PROJECT_ROOT / "scripts" / "_perrow_scan_results.json"


def main():
    if not RESULTS_PATH.exists():
        print(f"NO RESULTS YET at {RESULTS_PATH}")
        sys.exit(0)
    body = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    print(f"scanned   : {body.get('scanned')}")
    print(f"hits      : {len(body.get('hits') or [])}")
    print(f"errors    : {body.get('errors')}")
    print(f"empties   : {body.get('empties')}")
    hits = body.get("hits") or []
    if not hits:
        print("\nNo views in the scanned candidate set expose an issue_key column.")
        return
    print("\nViews exposing per-row issue keys:")
    for h in hits:
        print(f"\n  workbook : {h.get('workbook_name')!r}")
        print(f"  view     : {h.get('view_name')!r}  (luid {h.get('view_luid')})")
        print(f"  rows     : {h.get('rows')}  bytes: {h.get('bytes')}")
        hdr = h.get("header") or ""
        print(f"  header   : {hdr[:500]}{'...' if len(hdr) > 500 else ''}")


if __name__ == "__main__":
    main()
