"""
DISCOVERY PROBE — read-only scratchpad.

CURRENT PROBE: dump the PE and TE "handover workflow" Confluence pages so we can
work out how to read each item's PT Number and its pending/approved workflow
state. We do NOT yet know the structure (single status per page, a table of PT
numbers, or one child page per PT), so this dumps, for each parent page:

  * title + current version
  * full STORAGE-format HTML (raw macros — shows how the workflow is encoded)
  * RENDERED view HTML (shows the actual "Pending"/"Approved" text a human sees)
  * the list of child pages (id + title) in case the status lives per-child

Paste the output (outputs\\_probe_latest.txt) back and we'll write the parser.

All read-only — GET requests only (safe under scripts/readonly_guard.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()

from core.config_loader import load_config  # noqa: E402

# PE / TE handover workflow pages to inspect.
PAGES = {
    "PE (Handover PE)": "572625450",
    "TE (Handover TE)": "572625454",
}

# Cap each HTML block so notepad stays usable; storage is usually the smaller of
# the two. Bump these if a block is clearly truncated mid-structure.
STORAGE_CAP = 60000
VIEW_CAP = 40000


def _dump_block(label: str, html: str, cap: int) -> None:
    html = html or ""
    print(f"\n----- {label} ({len(html)} chars{' — TRUNCATED' if len(html) > cap else ''}) -----")
    print(html[:cap])
    if len(html) > cap:
        print(f"\n...[truncated {len(html) - cap} more chars — tell Claude to raise the cap]...")


def main() -> None:
    cfg = load_config("live")
    base = cfg.confluence_base_url
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {cfg.confluence_pat}", "Accept": "application/json"})
    s.verify = False

    for name, pid in PAGES.items():
        print("\n" + "=" * 78)
        print(f"PAGE: {name}  (id {pid})")
        print("=" * 78)

        url = (f"{base}/rest/api/content/{pid}"
               f"?expand=body.storage,body.view,version,children.page")
        try:
            r = s.get(url, timeout=30)
        except Exception as e:
            print(f"  ERROR fetching page {pid}: {e}")
            continue
        if r.status_code != 200:
            print(f"  Confluence read failed: HTTP {r.status_code}")
            print(f"  {r.text[:500]}")
            continue

        j = r.json()
        ver = j.get("version", {}).get("number")
        title = j.get("title", "")
        print(f"  title : {title}")
        print(f"  version: v{ver}")

        body = j.get("body", {})
        _dump_block("STORAGE HTML", body.get("storage", {}).get("value", ""), STORAGE_CAP)
        _dump_block("RENDERED VIEW HTML", body.get("view", {}).get("value", ""), VIEW_CAP)

        # Child pages (one-per-PT layouts live here).
        children = j.get("children", {}).get("page", {}).get("results", [])
        print(f"\n----- CHILD PAGES ({len(children)}) -----")
        for c in children:
            print(f"  - id {c.get('id')} | {c.get('title')}")


if __name__ == "__main__":
    main()
