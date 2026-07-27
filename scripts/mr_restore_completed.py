#!/usr/bin/env python3
"""
MR Status Report — restore COMPLETED MR rows lost by the 2026-07-23 incident.
============================================================================
DRY-RUN BY DEFAULT. Without --publish this only reads and prints; it never
touches the live page. Publishing requires the explicit --publish flag.

Why a merge and not a revert
----------------------------
Reverting page 560866215 to v253 would also throw away the containers that
legitimately completed after 23 Jul and every container created since. So this
takes the CURRENT page as the base and only puts back the completed rows that
v253 has and the current page has lost:

    * for each container completed in <from-version> but not completed now:
        - re-add its original COMPLETED row (Type, PT, PRSG, dates, Remarks,
          Completion Date) exactly as v253 recorded it
        - drop it from the Active table
    * everything else is left alone — newer completions, newer containers,
      current tick-boxes, current Remarks

The page is rebuilt from ITSELF (parse_active_rows / parse_page_html), so no
Jira or EDM round trip is involved and no field is re-derived.

Usage (company laptop, from the repo root):
    python -m scripts.mr_restore_completed                    # dry-run, prints the plan
    python -m scripts.mr_restore_completed --save-html out.html   # + dump the page it would publish
    python -m scripts.mr_restore_completed --publish          # WRITES to Confluence
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
    print(f"  v{ver}: {len(html):,} bytes | {when}")
    return html, ver


def main():
    ap = argparse.ArgumentParser(
        description="Restore COMPLETED MR rows lost on 2026-07-23 (dry-run by default)")
    ap.add_argument("--from-version", type=int, default=253,
                    help="page version to recover completed rows from (default 253)")
    ap.add_argument("--publish", action="store_true",
                    help="actually WRITE the merged page to Confluence "
                         "(without this the script only reads and prints)")
    ap.add_argument("--save-html", metavar="PATH",
                    help="write the page that would be published to a local file for review")
    args = ap.parse_args()

    M._load_settings("live")
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    csess = M.conf_session()

    print(f"Reading page {M.CONFLUENCE_PAGE_ID}...")
    old_html, old_v = _fetch(csess, args.from_version)
    cur_html, cur_v = _fetch(csess, None)

    _, old_completed_keys, old_completed_rows, _, _ = M.parse_page_html(old_html)
    cur_manual, cur_completed_keys, cur_completed_rows, cur_close, cur_mrp = M.parse_page_html(cur_html)
    cur_active = M.parse_active_rows(cur_html)

    # ---- work out exactly what to move -------------------------------------
    to_restore = [r for r in old_completed_rows
                  if r["Container"] not in cur_completed_keys]
    restore_keys = {r["Container"] for r in to_restore}

    new_active = [r for r in cur_active if r["Container"] not in restore_keys]
    new_completed = list(cur_completed_rows) + to_restore

    dropped_from_active = sorted(restore_keys & {r["Container"] for r in cur_active})
    not_in_active = sorted(restore_keys - set(dropped_from_active))

    print()
    print("=" * 72)
    print(f"  RESTORE PLAN   v{old_v} completed rows  ->  current page (v{cur_v})")
    print("=" * 72)
    print(f"\n  counts                 now      after")
    print(f"    Active              {len(cur_active):>5}      {len(new_active):>5}")
    print(f"    Completed           {len(cur_completed_rows):>5}      {len(new_completed):>5}")
    print(f"    MR in progress      {len(cur_mrp):>5}      {len(cur_mrp):>5}   (unchanged)")

    print(f"\n  Restoring {len(to_restore)} completed row(s) from v{old_v}:")
    for r in to_restore:
        mark = "active now" if r["Container"] in set(dropped_from_active) else "not on page"
        print(f"    {r['Container']:<18} {r['Type']:<12} done={r['Completion_Date']:<12}"
              f" [{mark}]  {r['Remarks'][:60]!r}")

    if not_in_active:
        print(f"\n  Note: {len(not_in_active)} of these are not currently in Active either "
              f"(Jira no longer returns them) — restoring puts them back on the record:")
        print(f"    {', '.join(not_in_active)}")

    print(f"\n  Left untouched:")
    print(f"    {len(cur_completed_rows)} completed row(s) already on the page")
    print(f"    {len(new_active)} active row(s)")
    print(f"    {len(cur_mrp)} 'MR in progress' tick(s): {sorted(cur_mrp) or '(none)'}")

    # ---- safety checks ------------------------------------------------------
    problems = []
    if not to_restore:
        problems.append("nothing to restore — the current page is already complete")
    overlap = restore_keys & {r["Container"] for r in new_active}
    if overlap:
        problems.append(f"container(s) would be in BOTH tables: {sorted(overlap)}")
    if len(new_completed) != len(cur_completed_rows) + len(to_restore):
        problems.append("completed row count does not add up")
    dupes = [k for k in {r["Container"] for r in new_completed}
             if [r["Container"] for r in new_completed].count(k) > 1]
    if dupes:
        problems.append(f"duplicate completed rows: {sorted(set(dupes))}")
    if cur_close:
        problems.append(f"'Close container without MR' is ticked for {sorted(cur_close)} — "
                        "resolve that with a normal report run first")

    if problems:
        print("\n  ⚠ NOT SAFE TO PUBLISH:")
        for p in problems:
            print(f"    - {p}")
        print()
        return 1
    print("\n  Safety checks: OK (no duplicates, no container in both tables)")

    html = M.build_html(new_active, new_completed, mr_progress=cur_mrp)

    if args.save_html:
        Path(args.save_html).write_text(html, encoding="utf-8")
        print(f"  Page that would be published written to: {args.save_html}")

    if not args.publish:
        print(f"\n  DRY-RUN — nothing written. Page stays at v{cur_v}.")
        print("  Re-run with --publish to apply this plan.")
        return 0

    print(f"\n  PUBLISHING as v{cur_v + 1}...")
    ok = M.conf_update(csess, html, cur_v)
    print("  DONE — page updated." if ok else "  FAILED — page NOT updated (see log).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
