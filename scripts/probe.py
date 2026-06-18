"""
DISCOVERY PROBE — read-only scratchpad.

PASS 4: crawl the FULL PE and TE handover page trees and, for every descendant
page, print its depth, id, title, and Comala workflow state
(/rest/cw/1/content/{id}/status -> state.name). This confirms where the per-PT
pages live and whether their TITLES carry the PT number (so we can match a
container's PT -> the right page -> Approved/Pending).

PE parent 572625450, TE parent 572625454. All read-only (GET only).
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

PARENTS = {"PE": "572625450", "TE": "572625454"}
PT_RE = re.compile(r'PT[A-Z]{2}-[A-Z0-9]+', re.IGNORECASE)
MAX_DEPTH = 4


def get_state(s, base, cid):
    try:
        r = s.get(f"{base}/rest/cw/1/content/{cid}/status", timeout=20)
    except Exception as e:
        return f"ERR({e})"
    if r.status_code != 200:
        return f"no-wf(HTTP {r.status_code})"
    try:
        return r.json().get("state", {}).get("name", "?")
    except Exception:
        return "?"


def children(s, base, cid):
    out = []
    start = 0
    while True:
        url = f"{base}/rest/api/content/{cid}/child/page?limit=100&start={start}"
        try:
            r = s.get(url, timeout=25)
        except Exception:
            break
        if r.status_code != 200:
            break
        j = r.json()
        out.extend(j.get("results", []))
        if len(j.get("results", [])) < 100:
            break
        start += 100
    return out


def walk(s, base, cid, depth):
    if depth > MAX_DEPTH:
        return
    for c in children(s, base, cid):
        kid = c.get("id")
        title = c.get("title", "")
        pt = PT_RE.search(title)
        state = get_state(s, base, kid)
        pad = "  " * depth
        flag = f"  <PT={pt.group(0).upper()}>" if pt else ""
        print(f"{pad}- d{depth} id={kid} | state={state}{flag} | {title}")
        walk(s, base, kid, depth + 1)


def main() -> None:
    cfg = load_config("live")
    base = cfg.confluence_base_url
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {cfg.confluence_pat}", "Accept": "application/json"})
    s.verify = False

    for label, pid in PARENTS.items():
        print("\n" + "=" * 78)
        print(f"{label} TREE (parent {pid})  state={get_state(s, base, pid)}")
        print("=" * 78)
        walk(s, base, pid, 0)


if __name__ == "__main__":
    main()
