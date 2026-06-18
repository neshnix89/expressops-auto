"""
DISCOVERY PROBE — read-only scratchpad.

PASS 2: look INSIDE the PE/TE handover child pages to find where the PT/Project
number and the pending/approved workflow state are encoded.

Structure learned in pass 1:
  * PE parent (572625450) = template table; real data lives in WEEKLY child
    pages, each holding a table with a "Project Number" column (one row per PT).
  * TE parent (572625454) = empty; one child page PER PT number, PT in the title
    (e.g. "Wk24/26: PTDE-AZ15 (DMR) PCBA TE to MX Handover").

So here we dump a few sample children (storage + rendered view) to see:
  - where the PT number sits, and
  - how "pending"/"approved" is rendered (status macro? Comala workflow? a
    task/cell?). The rendered VIEW is most likely to show the human-visible
    state text.

Paste outputs\\_probe_latest.txt back. All read-only (GET only).
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

# Sample child pages to inspect (id -> label).
PAGES = {
    # PE weekly child pages (table with Project Number rows)
    "572629336": "PE WK18/2026 IE PE to MX (weekly table)",
    "595417305": "PE WK25/2026 IE PE to MX (weekly table)",
    # TE per-PT child pages (PT in title, page-level workflow)
    "572171106": "TE Template: PTDE-xxxx PCBA TE to MX Handover",
    "595875590": "TE Wk24/26 PTDE-AZ15 (DMR) PCBA TE to MX Handover",
    "595863161": "TE Wk23/26 PTDE-AY55 PCBA TE to MX Handover",
}

STORAGE_CAP = 30000
VIEW_CAP = 22000


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

    for pid, name in PAGES.items():
        print("\n" + "=" * 78)
        print(f"PAGE: {name}  (id {pid})")
        print("=" * 78)

        url = (f"{base}/rest/api/content/{pid}"
               f"?expand=body.storage,body.view,version,metadata.labels")
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
        print(f"  title : {j.get('title','')}")
        print(f"  version: v{j.get('version', {}).get('number')}")
        labels = [l.get("name") for l in j.get("metadata", {}).get("labels", {}).get("results", [])]
        print(f"  labels: {labels}")

        body = j.get("body", {})
        _dump_block("STORAGE HTML", body.get("storage", {}).get("value", ""), STORAGE_CAP)
        _dump_block("RENDERED VIEW HTML", body.get("view", {}).get("value", ""), VIEW_CAP)


if __name__ == "__main__":
    main()
