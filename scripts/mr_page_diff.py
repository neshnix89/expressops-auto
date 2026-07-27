#!/usr/bin/env python3
"""
MR Status Report — compare a historical page version against the current page.
==============================================================================
READ-ONLY. Issues GETs against Confluence only; never writes, never publishes.

Built to assess the 2026-07-23 incident, where a transient failure on the page
GET let the daily run rebuild page 560866215 from nothing and republish it as
v254 — dropping completed containers back into Active, clearing the
"MR in progress" ticks and blanking the "MR Week XX" remarks.

Usage (on the company laptop, from the repo root):
    python -m scripts.mr_page_diff --old 253
    python -m scripts.mr_page_diff --old 253 --new 254   # compare two history versions

Parsing is delegated to tasks.mr_status_report.main.parse_page_html so this
report reflects exactly what the daily run would see — no duplicated logic.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tasks.mr_status_report import main as M  # noqa: E402

log = logging.getLogger("MR_Report")


def _fetch(csess, version=None):
    """Fetch the current page, or a historical version. Returns (html, version)."""
    if version is None:
        url = (f"{M.CONFLUENCE_URL}/rest/api/content/{M.CONFLUENCE_PAGE_ID}"
               f"?expand=body.storage,version")
    else:
        url = (f"{M.CONFLUENCE_URL}/rest/api/content/{M.CONFLUENCE_PAGE_ID}"
               f"?status=historical&version={version}&expand=body.storage,version")
    resp = csess.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"GET v{version or 'current'} failed: HTTP {resp.status_code}")
    page = resp.json()
    html = page.get("body", {}).get("storage", {}).get("value", "") or ""
    ver = page.get("version", {}).get("number", version)
    when = page.get("version", {}).get("when", "?")
    by = (page.get("version", {}).get("by", {}) or {}).get("displayName", "?")
    print(f"  v{ver}: {len(html):,} bytes | {when} | by {by}")
    return html, ver


def _snapshot(html):
    manual, completed, completed_rows, ticked_done, mr_progress = M.parse_page_html(html)
    return {
        "active": manual,                       # key -> {MR_Status, Remarks, Handover_*}
        "completed": completed,                 # set of keys
        "completed_rows": {r["Container"]: r for r in completed_rows},
        "mr_progress": mr_progress,
        "close_ticked": ticked_done,
    }


def _fmt_set(s, limit=200):
    s = sorted(s)
    if not s:
        return "(none)"
    shown = ", ".join(s[:limit])
    return shown + (f"  … +{len(s)-limit} more" if len(s) > limit else "")


def diff(old, new, old_v, new_v):
    print()
    print("=" * 72)
    print(f"  DIFF   v{old_v} (old)  ->  v{new_v} (new)")
    print("=" * 72)

    print(f"\n  counts            v{old_v}      v{new_v}")
    print(f"    Active           {len(old['active']):>5}   {len(new['active']):>5}")
    print(f"    Completed        {len(old['completed']):>5}   {len(new['completed']):>5}")
    print(f"    MR in progress   {len(old['mr_progress']):>5}   {len(new['mr_progress']):>5}")
    print(f"    Close-ticked     {len(old['close_ticked']):>5}   {len(new['close_ticked']):>5}")

    # --- the headline damage: completed containers that fell out of history ---
    lost = old["completed"] - new["completed"]
    print(f"\n  [1] COMPLETED in v{old_v} but NOT in v{new_v}  ({len(lost)})")
    print(f"      {_fmt_set(lost)}")
    back_active = lost & set(new["active"])
    print(f"      ...of which are now sitting in Active: {len(back_active)}")
    print(f"      {_fmt_set(back_active)}")

    gained = new["completed"] - old["completed"]
    print(f"\n  [2] COMPLETED in v{new_v} but not in v{old_v}  ({len(gained)})")
    print(f"      {_fmt_set(gained)}")

    # --- tick-boxes ---
    lost_ticks = old["mr_progress"] - new["mr_progress"]
    print(f"\n  [3] 'MR in progress' ticks lost  ({len(lost_ticks)})")
    print(f"      {_fmt_set(lost_ticks)}")

    # --- manual columns wiped (this is what emptied the MR Week table) ---
    lost_remarks, lost_status = [], []
    for k, om in old["active"].items():
        nm = new["active"].get(k)
        if nm is None:
            continue
        if om.get("Remarks") and not nm.get("Remarks"):
            lost_remarks.append((k, om["Remarks"]))
        if om.get("MR_Status") and nm.get("MR_Status") != om.get("MR_Status"):
            lost_status.append((k, om["MR_Status"], nm.get("MR_Status")))

    print(f"\n  [4] Remarks present in v{old_v}, blank in v{new_v}  ({len(lost_remarks)})")
    for k, v in lost_remarks:
        flag = "  <-- MR Week tag" if M.MR_WEEK_PATTERN.search(str(v)) else ""
        print(f"      {k:<18} {v!r}{flag}")

    print(f"\n  [5] MR Status changed  ({len(lost_status)})")
    for k, o, n in lost_status:
        print(f"      {k:<18} {o!r} -> {n!r}")

    # --- what a restore would need to put back ---
    print(f"\n  [6] Restore scope: rows only v{old_v} can supply")
    only_old = {k: r for k, r in old["completed_rows"].items() if k not in new["completed"]}
    print(f"      {len(only_old)} completed row(s) recoverable from v{old_v}:")
    for k, r in sorted(only_old.items()):
        print(f"      {k:<18} {r.get('Type',''):<12} PRSG={r.get('PRSG_Status',''):<13}"
              f" done={r.get('Completion_Date','')}  remarks={r.get('Remarks','')!r}")
    print()


def main():
    ap = argparse.ArgumentParser(description="READ-ONLY diff of MR report page versions")
    ap.add_argument("--old", type=int, required=True, help="historical version to compare FROM (e.g. 253)")
    ap.add_argument("--new", type=int, default=None, help="version to compare TO (default: current live page)")
    args = ap.parse_args()

    M._load_settings("live")
    # main.py's basicConfig puts the ROOT logger at DEBUG with a StreamHandler,
    # so urllib3 connection chatter would bury the diff. Quieten everything.
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("MR_Report").setLevel(logging.WARNING)
    csess = M.conf_session()

    print(f"Reading page {M.CONFLUENCE_PAGE_ID} (read-only, no writes)...")
    old_html, old_v = _fetch(csess, args.old)
    new_html, new_v = _fetch(csess, args.new)

    diff(_snapshot(old_html), _snapshot(new_html), old_v, new_v)
    print("READ-ONLY: nothing was written to Confluence.")


if __name__ == "__main__":
    main()
