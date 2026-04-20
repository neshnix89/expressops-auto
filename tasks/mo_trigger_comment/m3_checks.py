"""
M3 ODBC enrichment queries for mo_trigger_comment.

Each public check hits M3 via :class:`core.m3.M3Client` and returns the
human-readable line that lands directly in the assembled comment. Mock
mode reads fixture JSON from `mock_data/`:

    routing_{article}.json    — MPDOPE rows
    bom_pkg_{article}.json    — MPDMAT rows (Dwgpos 5000)
    item_{mmitno}.json        — MITMAS_AP (article or packaging component)
    prodstatus_{article}.json — MPDHED row

Missing mock files are downgraded to a warning and return a safe
"CHECK REQUIRED" line so a VPS run never crashes over incomplete
fixtures.
"""

from __future__ import annotations

import re
from typing import Any

from core.errors import FriendlyError
from core.m3 import M3Client


# ── SQL ──────────────────────────────────────────────────────────────

ITEM_STATUS_SQL = """
SELECT MMITNO, MMSTAT, MMITDS FROM PFODS.MITMAS_AP
WHERE MMITNO = ?
""".strip()

PROD_STATUS_SQL = """
SELECT PHPRNO, PHSTAT FROM PFODS.MPDHED
WHERE PHPRNO = ? AND PHSTRT = 'STD' AND PHFACI = 'MF1'
""".strip()

ROUTING_SQL = """
SELECT POOPNO, POOPDS, PODOID FROM PFODS.MPDOPE
WHERE POPRNO = ? AND POSTRT = 'STD' AND POFACI = 'MF1'
ORDER BY POOPNO
""".strip()

# PMDWPO is a STRING column in M3 — must TRIM + compare as string, not int.
BOM_PACKAGING_SQL = """
SELECT PMMTNO FROM PFODS.MPDMAT
WHERE PMPRNO = ? AND PMSTRT = 'STD' AND PMFACI = 'MF1'
  AND TRIM(PMDWPO) = '5000'
""".strip()


VALID_E5_STATUSES = {"20", "30", "40"}
# Breaking-array routing doc is a 77-prefixed short code. 77-0000 and
# similar all-zero fallbacks mean the doc was never properly assigned.
_BREAKING_DOC_PATTERN = re.compile(r"^77-[A-Za-z0-9]{3,5}$")
_BREAKING_DOC_ALLZERO = re.compile(r"^77-0+$")


def _row_value(row: dict[str, Any], key: str) -> str:
    raw = row.get(key) if isinstance(row, dict) else None
    if raw is None:
        return ""
    return str(raw).strip()


def _query_or_warn(
    m3: M3Client,
    sql: str,
    params: tuple,
    mock_filename: str,
    logger,
) -> list[dict[str, Any]] | None:
    """
    Run a query. Returns rows, empty list, or None when a mock fixture
    is missing (logged as a warning — caller decides what to do).
    """
    try:
        return m3.query(sql, params=params, mock_filename=mock_filename)
    except FriendlyError as exc:
        if m3.config.is_mock and "mock data not found" in exc.message:
            logger.warning("M3 mock missing: %s", mock_filename)
            return None
        raise


# ── E5 partial status ────────────────────────────────────────────────


def check_partial_e5(m3: M3Client, article: str, logger) -> str:
    """
    Validate MMSTAT (item) and PHSTAT (prod structure) are both in
    {20, 30, 40} — the "release" range. Both pass → proceed line.
    Either fail → explicit CHECK REQUIRED warning with both values.
    """
    item_rows = _query_or_warn(
        m3, ITEM_STATUS_SQL, (article,), f"item_{article}.json", logger,
    )
    prod_rows = _query_or_warn(
        m3, PROD_STATUS_SQL, (article,), f"prodstatus_{article}.json", logger,
    )

    item_status = _row_value(item_rows[0], "MMSTAT") if item_rows else ""
    prod_status = _row_value(prod_rows[0], "PHSTAT") if prod_rows else ""

    item_ok = item_status in VALID_E5_STATUSES
    prod_ok = prod_status in VALID_E5_STATUSES

    if item_ok and prod_ok:
        return (
            "Item sts \u2713, Prod sts \u2713 \u2014 "
            "R&D/Production release: check manually"
        )
    return (
        f"\u26a0 CHECK REQUIRED \u2014 Item sts: {item_status or '(missing)'}, "
        f"Prod sts: {prod_status or '(missing)'}"
    )


# ── Routing checks ───────────────────────────────────────────────────


def _is_valid_breaking_doc(doc: str) -> bool:
    """Doc must match 77-xxx[xx] and not be all zeros."""
    if not doc:
        return False
    if _BREAKING_DOC_ALLZERO.match(doc):
        return False
    return bool(_BREAKING_DOC_PATTERN.match(doc))


def check_routing(
    m3: M3Client,
    article: str,
    te_assignee: str,
    logger,
) -> tuple[str, str]:
    """
    Scan the routing operations for an article. Returns:
        (breaking_line, aoi_test_line)

    The TE assignee is prefixed onto aoi_test_line so the caller can
    drop it straight into the comment.
    """
    rows = _query_or_warn(
        m3, ROUTING_SQL, (article,), f"routing_{article}.json", logger,
    )
    if rows is None:
        return (
            "\u26a0 Breaking check skipped (no routing mock)",
            f"{te_assignee} \u26a0 AOI/Test check skipped (no routing mock)",
        )
    if not rows:
        logger.warning("article %s: no routing ops returned", article)
        return (
            "\u26a0 No BREAKING-ARRAY in routing",
            f"{te_assignee} \u26a0 No AOI or Test in routing",
        )

    breaking_line = "\u26a0 No BREAKING-ARRAY in routing"
    has_aoi = False
    has_test_array = False

    for row in rows:
        desc = _row_value(row, "POOPDS").upper()
        doc = _row_value(row, "PODOID")
        if not desc:
            continue

        if "BREAKING-ARRAY" in desc and breaking_line.startswith("\u26a0"):
            if _is_valid_breaking_doc(doc):
                breaking_line = f"Breaking already included in routing. Doc: {doc}"
            else:
                breaking_line = (
                    f"\u26a0 Breaking in routing but doc invalid: {doc or '(blank)'}"
                )

        if desc.startswith("AOI"):
            has_aoi = True

        if "TEST" in desc and "ARRAY" in desc:
            has_test_array = True

        if "PACKING" in desc:
            # Informational only per TASK.md — no output line, just log.
            logger.debug("article %s: PACKING op seen (%s)", article, desc)

    if has_aoi and has_test_array:
        aoi_test = f"{te_assignee} AOI and Test required"
    elif has_aoi:
        aoi_test = f"{te_assignee} AOI required"
    elif has_test_array:
        aoi_test = f"{te_assignee} Test required"
    else:
        aoi_test = f"{te_assignee} \u26a0 No AOI or Test in routing"

    return breaking_line, aoi_test


# ── BOM packaging (Dwgpos 5000) ──────────────────────────────────────


def check_bom_packaging(m3: M3Client, article: str, logger) -> str:
    """
    Confirm a packaging material is present in the BOM at Dwgpos 5000
    and that its MITMAS_AP description (MMITDS) starts with "PM" \u2014
    the convention P+F uses to mark packaging-material items.

    MMITDS values from the ODBC driver can carry leading whitespace; the
    startswith check runs after .strip() so padded descriptions still
    classify correctly.
    """
    rows = _query_or_warn(
        m3, BOM_PACKAGING_SQL, (article,), f"bom_pkg_{article}.json", logger,
    )
    if rows is None:
        return "\u26a0 Packaging check skipped (no BOM mock)"
    if not rows:
        return "\u26a0 No packaging material (Dwgpos 5000) in BOM"

    pmmtno = _row_value(rows[0], "PMMTNO")
    if not pmmtno:
        return "\u26a0 Dwgpos 5000 row present but PMMTNO blank"

    item_rows = _query_or_warn(
        m3, ITEM_STATUS_SQL, (pmmtno,), f"item_{pmmtno}.json", logger,
    )
    description = _row_value(item_rows[0], "MMITDS") if item_rows else ""
    description_stripped = description.strip()

    if description_stripped.upper().startswith("PM"):
        return f"Packaging Material ({pmmtno}) already in BOM"
    return (
        f"\u26a0 Dwgpos 5000 component {pmmtno} not PM: "
        f"{description_stripped or '(no description)'}"
    )
