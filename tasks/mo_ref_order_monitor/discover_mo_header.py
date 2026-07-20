#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discovery (READ-ONLY) — locate the M3 MO-header table + the "Ref order no"
and MO-status columns for the mo_ref_order_monitor task.

Runs on the company laptop only (needs the ODSSG DSN). Pure SELECTs, no
writes. Prints clearly delimited sections so the output can be pasted back
for analysis.

Usage (via Relay, whitelisted path):
    python C:\\Users\\tmoghanan\\Documents\\AI\\expressops-auto\\tasks\\mo_ref_order_monitor\\discover_mo_header.py

Target reference (from the P1/PMS100 screenshot):
    MO number  = 7003904788
    Product    = 70209808
    Status     = 90
    Ref ord no = 0 | QM | 2902
"""

import json
import sys

DSN = "ODSSG"          # documented DSN (see CLAUDE.md) — not a secret, no password
SCHEMA = "PFODS"
TARGET_MO = "7003904788"

# Candidate MO-header tables (standard M3 = MWOHED; _AP is the ODS convention here)
CANDIDATE_TABLES = ["MWOHED_AP", "MWOHED", "MWOHEDV_AP"]
# Most-likely MO-number columns to try a targeted lookup before falling back to a sample dump
CANDIDATE_MO_COLS = ["VHMFNO", "VHMFN0", "PLMFNO", "MFNO"]

SEP = "=" * 78


def out(msg=""):
    print(msg)
    sys.stdout.flush()


def get_columns(cur, table):
    """Return column names for a table, or None if the table doesn't exist."""
    try:
        cur.execute(f"SELECT * FROM {SCHEMA}.{table} WHERE 1=0")
        return [d[0] for d in cur.description]
    except Exception as exc:  # noqa: BLE001 — discovery: report and move on
        out(f"  [table {table}] not queryable: {exc}")
        return None


def dump_rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def main():
    try:
        import pyodbc
    except ImportError:
        out("[FATAL] pyodbc not installed on this machine.")
        sys.exit(1)

    try:
        conn = pyodbc.connect(f"DSN={DSN}")
    except Exception as exc:  # noqa: BLE001
        out(f"[FATAL] Could not connect to DSN={DSN}: {exc}")
        sys.exit(1)

    cur = conn.cursor()

    out(SEP)
    out("M3 MO-HEADER DISCOVERY (read-only)")
    out(f"DSN={DSN}  SCHEMA={SCHEMA}  TARGET_MO={TARGET_MO}")
    out(SEP)

    # --- Step 1: which candidate table exists, and its columns ---
    live_table = None
    for table in CANDIDATE_TABLES:
        out(f"\n[1] Checking table {SCHEMA}.{table} ...")
        cols = get_columns(cur, table)
        if cols is None:
            continue
        out(f"  EXISTS — {len(cols)} columns:")
        out("  " + json.dumps(cols))
        if live_table is None:
            live_table = table

    if not live_table:
        out("\n[RESULT] None of the candidate MO-header tables were found.")
        out("         Next step: fall back to H5 PMS100 discovery.")
        conn.close()
        return

    out(f"\n[2] Using table: {SCHEMA}.{live_table}")

    # --- Step 2: targeted lookup of the reference MO (best case, one round) ---
    found = False
    for col in CANDIDATE_MO_COLS:
        if live_table and col not in (get_columns(cur, live_table) or []):
            continue
        try:
            out(f"\n[2a] Targeted: WHERE {col} = '{TARGET_MO}'")
            rows = dump_rows(
                cur,
                f"SELECT * FROM {SCHEMA}.{live_table} WHERE {col} = ?",
                (TARGET_MO,),
            )
            if rows:
                out(f"  HIT — {len(rows)} row(s). Full row(s):")
                out(json.dumps(rows, indent=2, default=str, ensure_ascii=False))
                found = True
                break
            else:
                out("  0 rows (column exists but no match — MO may be trimmed/padded).")
        except Exception as exc:  # noqa: BLE001
            out(f"  query failed on {col}: {exc}")

    # --- Step 3: fallback — sample rows so columns can be identified by value ---
    if not found:
        out(f"\n[3] Fallback — 30 sample rows from {SCHEMA}.{live_table}:")
        try:
            rows = dump_rows(
                cur,
                f"SELECT * FROM {SCHEMA}.{live_table} FETCH FIRST 30 ROWS ONLY",
            )
            out(f"  {len(rows)} row(s):")
            out(json.dumps(rows, indent=2, default=str, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            out(f"  sample dump failed: {exc}")

    out(f"\n{SEP}")
    out("DISCOVERY COMPLETE — paste this output back for analysis.")
    out(SEP)
    conn.close()


if __name__ == "__main__":
    main()
