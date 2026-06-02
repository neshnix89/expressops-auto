"""
DISCOVERY PROBE — read-only scratchpad. EDIT THIS each discovery iteration.

How it runs: run_probe.bat (double-click on the laptop) syncs the latest copy
of this file from GitHub, then runs it against the LIVE systems with the
read-only guard active. The guard blocks every write at the transport layer, so
you can iterate here freely — the worst case is reading a query result.

What goes here: whatever you need to LOOK AT — a JIRA issue's fields, an M3
table's columns or sample rows, a JQL count. Print what you find; the output is
captured to outputs/_probe_latest.txt and opened for you automatically.

Keep it read-only by nature (gets, searches, SELECTs). If you ever need to
write to a live system, that is a separate, deliberate step — not this loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_loader import load_config
from core.jira_client import JiraClient
from core.m3 import M3Client


def main() -> None:
    cfg = load_config(mode_override="live")
    jira = JiraClient(cfg)
    m3 = M3Client(cfg)

    # ======================================================================
    # EDIT BELOW — your read-only discovery for this iteration.
    # ======================================================================

    # --- Example A: peek at a JIRA issue's populated fields ---
    # issue = jira.get_issue("NPIOTHER-4600")
    # print("summary:", issue["fields"].get("summary"))
    # for k, v in sorted(issue["fields"].items()):
    #     if v not in (None, "", [], {}):
    #         print(f"  {k} = {v!r}")

    # --- Example B: explore an M3 table (sample rows) ---
    # for row in m3.explore_table("MITMAS_AP", limit=5):
    #     print(row)

    # --- Example C: a JQL count ---
    # res = jira.search('issuetype = "Work Container" AND "Order Type" is not EMPTY',
    #                    max_results=0)
    # print("total containers:", res.get("total"))

    print("probe.py is empty — edit scripts/probe.py with your discovery code.")

    # ======================================================================
    # EDIT ABOVE
    # ======================================================================


if __name__ == "__main__":
    main()
