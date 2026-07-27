#!/usr/bin/env python3
"""
DISCOVERY PROBE — how are PE (QD-*) and TE (906-*) reports stored in EDM?
=========================================================================
READ-ONLY. SELECTs only; no INSERT/UPDATE/DELETE, and it never touches
Confluence beyond a single GET of the report page.

The MR report wants to colour the PE Reports / TE Reports cells by release
state, the same way PRSG Status already is. The PRSG lookup works like this:

    EDM_REFERENCES.REF (= PT number) -> .DOCNUMBER (PRSG-*) -> EDM_DOCS.RELEASESTATE
    RELEASESTATE == 9  =>  Released

Whether QD-* / 906-* documents live in EDM_DOCS.DOCNUMBER at all — and in what
exact format — is NOT known, so this probe establishes it instead of guessing.

Run on the company laptop (EDMAdmin.exe preferred; plain python delegates):
    C:\\Users\\tmoghanan\\EDMAdmin.exe -m scripts.probe_edm_reports
    python -m scripts.probe_edm_reports
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tasks.mr_status_report import main as M  # noqa: E402

SAMPLE = 25   # how many real report numbers to test exactly


def show(rows, limit=15):
    if not rows:
        print("      (no rows)")
        return
    for r in rows[:limit]:
        print(f"      {r}")
    if len(rows) > limit:
        print(f"      ... +{len(rows)-limit} more")


def main():
    M._load_settings("live")
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # ---- 1. pull the real PE/TE numbers currently on the page --------------
    print("Reading the live report page for real PE/TE numbers...")
    csess = M.conf_session()
    url = (f"{M.CONFLUENCE_URL}/rest/api/content/{M.CONFLUENCE_PAGE_ID}"
           f"?expand=body.storage")
    resp = csess.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.json()["body"]["storage"]["value"]

    pe, te = set(), set()
    for r in M.parse_active_rows(html):
        for tok in re.split(r'[,\s]+', str(r.get("PE_Reports", ""))):
            if tok.strip():
                pe.add(tok.strip().upper())
        for tok in re.split(r'[,\s]+', str(r.get("TE_Reports", ""))):
            if tok.strip():
                te.add(tok.strip().upper())
    print(f"  PE numbers on page: {len(pe)}  e.g. {sorted(pe)[:5]}")
    print(f"  TE numbers on page: {len(te)}  e.g. {sorted(te)[:5]}")

    from core.edm import EDMClient
    client = EDMClient(M.CFG)

    def q(label, sql, binds=None):
        print(f"\n--- {label}")
        print(f"    {' '.join(sql.split())[:160]}")
        try:
            rows = client.query(sql, binds or {})
            print(f"    -> {len(rows)} row(s)")
            show(rows)
            return rows
        except Exception as e:
            print(f"    !! FAILED: {e}")
            return []

    # ---- 2. what do QD-/906- docnumbers actually look like? ---------------
    q("A. EDM_DOCS sample where DOCNUMBER starts 'QD'",
      "SELECT DOCNUMBER, RELEASESTATE FROM ADMEDP.EDM_DOCS "
      "WHERE DOCNUMBER LIKE 'QD%' AND ROWNUM <= 15")

    q("B. EDM_DOCS sample where DOCNUMBER starts '906'",
      "SELECT DOCNUMBER, RELEASESTATE FROM ADMEDP.EDM_DOCS "
      "WHERE DOCNUMBER LIKE '906%' AND ROWNUM <= 15")

    # ---- 3. do the page's actual numbers resolve? -------------------------
    for label, vals in (("PE (QD-*)", sorted(pe)), ("TE (906-*)", sorted(te))):
        batch = vals[:SAMPLE]
        if not batch:
            print(f"\n--- {label}: nothing on the page to test")
            continue
        binds = {f"p{i}": v for i, v in enumerate(batch)}
        ph = ",".join(f":p{i}" for i in range(len(batch)))
        rows = q(f"C. {label} exact match on EDM_DOCS.DOCNUMBER ({len(batch)} tested)",
                 f"SELECT DOCNUMBER, RELEASESTATE FROM ADMEDP.EDM_DOCS "
                 f"WHERE DOCNUMBER IN ({ph})", binds)
        found = {str(r.get("DOCNUMBER", "")).upper() for r in rows}
        missing = [v for v in batch if v not in found]
        print(f"    matched {len(found)}/{len(batch)}; unmatched: {missing[:10]}")

        if missing:
            # Maybe they hang off EDM_REFERENCES instead (like PRSG does).
            mb = {f"p{i}": v for i, v in enumerate(missing[:SAMPLE])}
            mph = ",".join(f":p{i}" for i in range(len(mb)))
            q(f"D. {label} unmatched -> try EDM_REFERENCES.REF",
              f"SELECT REF, DOCNUMBER FROM ADMEDP.EDM_REFERENCES "
              f"WHERE REF IN ({mph}) AND ROWNUM <= 20", mb)
            q(f"E. {label} unmatched -> try EDM_REFERENCES.DOCNUMBER",
              f"SELECT REF, DOCNUMBER FROM ADMEDP.EDM_REFERENCES "
              f"WHERE DOCNUMBER IN ({mph}) AND ROWNUM <= 20", mb)

    # ---- 4. confirm the release-state coding ------------------------------
    q("F. RELEASESTATE distribution for QD/906 docs (is 9 == Released here too?)",
      "SELECT RELEASESTATE, COUNT(*) AS N FROM ADMEDP.EDM_DOCS "
      "WHERE DOCNUMBER LIKE 'QD%' OR DOCNUMBER LIKE '906%' "
      "GROUP BY RELEASESTATE ORDER BY N DESC")

    print("\nREAD-ONLY: no data was modified.")


if __name__ == "__main__":
    main()
