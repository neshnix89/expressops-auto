"""
DISCOVERY PROBE — read-only scratchpad.

CURRENT PROBE: recover ticked Status checkboxes from the MR Confluence page's
version history. The daily (old) job wiped the Status column; every Confluence
save is retained, so a recent historical version still holds the ticks. This
lists, for the last ~15 versions, which containers had a ticked checkbox so we
know what to restore.

All read-only — GET requests only (safe under scripts/readonly_guard.py).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()

from core.config_loader import load_config  # noqa: E402

TASK_COMPLETE = re.compile(r'<ac:task-status>\s*complete\s*</ac:task-status>', re.IGNORECASE)
TR = re.compile(r'<tr\b.*?</tr>', re.DOTALL | re.IGNORECASE)
KEY = re.compile(r'/browse/([A-Za-z][A-Za-z0-9]*-\d+)')


def ticked(html: str) -> set[str]:
    out: set[str] = set()
    for chunk in TR.findall(html or ""):
        if TASK_COMPLETE.search(chunk):
            m = KEY.search(chunk)
            if m:
                out.add(m.group(1).strip())
    return out


def main() -> None:
    cfg = load_config("live")
    base = cfg.confluence_base_url
    pid = str(cfg.pages.get("mr_status_report") or 560866215)
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {cfg.confluence_pat}", "Accept": "application/json"})
    s.verify = False

    r = s.get(f"{base}/rest/api/content/{pid}?expand=version", timeout=15)
    if r.status_code != 200:
        print(f"Confluence read failed: HTTP {r.status_code}")
        return
    cur = r.json()["version"]["number"]
    print(f"MR page {pid}: current version v{cur}")
    print("Scanning recent versions for ticked Status checkboxes...\n")

    found_any = False
    for v in range(cur, max(cur - 15, 0), -1):
        url = (f"{base}/rest/api/content/{pid}"
               f"?status=historical&version={v}&expand=body.storage,version")
        rr = s.get(url, timeout=30)
        if rr.status_code != 200:
            print(f"  v{v}: HTTP {rr.status_code} (skip)")
            continue
        j = rr.json()
        html = j.get("body", {}).get("storage", {}).get("value", "")
        when = j.get("version", {}).get("when", "")
        who = j.get("version", {}).get("by", {}).get("displayName", "")
        has_col = "ac:task-list" in html
        tk = sorted(ticked(html))
        flag = "   <<< HAS TICKS" if tk else ""
        print(f"  v{v} | {when} | {who} | checkbox_col={'yes' if has_col else 'no'} | ticked={tk}{flag}")
        if tk:
            found_any = True

    if not found_any:
        print("\nNo ticked checkboxes found in the last 15 versions.")
    else:
        print("\n^ Tell Claude which version's 'ticked=[...]' list to restore.")


if __name__ == "__main__":
    main()
