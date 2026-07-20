"""
M3 MO-header reader for mo_ref_order_monitor.

Reads the fields confirmed by discover_mo_header.py from PFODS.MWOHED_AP and
maps them to a logic.Observation. Mock mode reads mock_data/mo_header_{mo}.json.
"""

from __future__ import annotations

from datetime import datetime

from core.m3 import M3Client

from .logic import Observation, parse_status

# Only the columns we need (VH-prefixed MO header). Confirmed 2026-07.
_SQL = (
    "SELECT VHMFNO, VHPRNO, VHWHST, VHWHHS, VHRORN, VHORTY, VHRESP "
    "FROM {schema}.MWOHED_AP WHERE VHMFNO = ?"
)


def fetch_mo_header(m3: M3Client, mo_no: str, at: datetime) -> Observation | None:
    """
    Fetch one MO header and map it to an Observation, or None if the MO is not
    found in M3. `at` is the poll time (stamped on the observation).
    """
    sql = _SQL.format(schema=m3.config.m3_schema)
    rows = m3.query(sql, (mo_no,), mock_filename=f"mo_header_{mo_no}.json")
    if not rows:
        return None
    r = rows[0]
    return Observation(
        mo_no=str(r.get("VHMFNO", mo_no)).strip(),
        marker=(str(r.get("VHRORN", "")) or "").strip(),
        status=parse_status(r.get("VHWHST")),
        highest_status=parse_status(r.get("VHWHHS")),
        pn=(str(r.get("VHPRNO", "")) or "").strip(),
        order_type=(str(r.get("VHORTY", "")) or "").strip(),
        responsible=(str(r.get("VHRESP", "")) or "").strip(),
        at=at,
    )
