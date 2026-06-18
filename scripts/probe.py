"""
DISCOVERY PROBE — read-only scratchpad.

PASS 3: find where the pending/approved workflow STATE lives. It is NOT in the
page body (storage/view) — it is almost certainly held by a workflow plugin
(Comala Document Management). This pass tries the likely Comala REST endpoints
and the Confluence content-properties API for a few sample pages, and prints
status code + body so we can see exactly what returns the state name.

Sample content IDs:
  595863161 = TE Wk23/26 PTDE-AY55 (v3 — likely been through review)
  595875590 = TE Wk24/26 PTDE-AZ15 (DMR) (v1)
  572629336 = PE WK18/2026 weekly table (v5)
  572625450 = PE parent
  572625454 = TE parent

All read-only (GET only).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
import urllib3  # noqa: E402

urllib3.disable_warnings()

from core.config_loader import load_config  # noqa: E402

IDS = {
    "595863161": "TE PTDE-AY55 (v3)",
    "595875590": "TE PTDE-AZ15 (v1)",
    "572629336": "PE WK18 weekly (v5)",
}


def _try(s, base, path, cap=2000):
    url = f"{base}{path}"
    try:
        r = s.get(url, timeout=25)
    except Exception as e:
        print(f"    GET {path}\n      ERROR: {e}")
        return
    body = r.text or ""
    # Pretty-print JSON if possible
    try:
        body = json.dumps(r.json(), indent=2)
    except Exception:
        pass
    note = " — TRUNCATED" if len(body) > cap else ""
    print(f"    GET {path}  -> HTTP {r.status_code} ({len(body)} chars{note})")
    if r.status_code == 200 or len(body) < 1500:
        print("      " + body[:cap].replace("\n", "\n      "))


def main() -> None:
    cfg = load_config("live")
    base = cfg.confluence_base_url
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {cfg.confluence_pat}", "Accept": "application/json"})
    s.verify = False

    for cid, name in IDS.items():
        print("\n" + "=" * 78)
        print(f"CONTENT {cid}  ({name})")
        print("=" * 78)

        # 1) Comala Document Management (server/DC) REST API
        print("  [Comala /rest/cw/1/]")
        _try(s, base, f"/rest/cw/1/content/{cid}/status")
        _try(s, base, f"/rest/cw/1/content/{cid}/states")
        _try(s, base, f"/rest/cw/1/content/{cid}/approvals")
        _try(s, base, f"/rest/cw/1/content/{cid}/workflow")

        # 2) Confluence content properties (list all keys, then common ones)
        print("  [Confluence content properties]")
        _try(s, base, f"/rest/api/content/{cid}/property?limit=50", cap=3000)


if __name__ == "__main__":
    main()
