"""
Pure business logic for mo_trigger_comment.

No I/O here — functions take plain dicts/strings and return results.
Keeps logic unit-testable without JIRA / M3 access.

Covers:
  * Readiness gate (JIRA Work Package statuses)
  * Description HTML parsing (item table, delivery info)
  * Pilot-run detection (Order Type + QM P+L WP — two-signal)
  * Programme IC detection (ICUC + PE Won't Do)
  * Order-type label mapping
  * Working-day arithmetic with SG public holidays 2025-2026
  * Assignee / FYI list assembly
  * Comment body assembly
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag


# ── JIRA custom-field IDs ────────────────────────────────────────────

ORDER_TYPE_CF = "customfield_13905"


# ── Readiness gate ───────────────────────────────────────────────────

# Maps canonical WP names → set of acceptable resolution names.
# SMT Build is handled specially (status check, not resolution).
_READINESS_RESOLUTIONS: dict[str, set[str]] = {
    "material": {"Done", "Acknowledged"},
    "pcb": {"Done", "Acknowledged"},
    "routing - technprep": {"Done", "Acknowledged"},
    # Programme IC containers skip PE with "Won't Do".
    "pe - technprep": {"Done", "Acknowledged", "Won't Do"},
    "te - technprep": {"Done", "Acknowledged"},
}

SMT_BUILD_BLOCKED_STATUSES = {"Done", "In Progress"}


def _wp_summary(wp: dict[str, Any]) -> str:
    return ((wp.get("fields") or {}).get("summary") or "").strip()


def _wp_status_name(wp: dict[str, Any]) -> str:
    return (((wp.get("fields") or {}).get("status") or {}).get("name") or "").strip()


def _wp_resolution_name(wp: dict[str, Any]) -> str:
    return (((wp.get("fields") or {}).get("resolution") or {}).get("name") or "").strip()


def find_wp_by_name(wps: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """Case-insensitive exact-match WP lookup by summary."""
    target = name.strip().lower()
    for wp in wps:
        if _wp_summary(wp).lower() == target:
            return wp
    return None


def get_wp_assignee(wp: dict[str, Any] | None) -> str | None:
    """Return assignee displayName, or None when unassigned/missing."""
    if not wp:
        return None
    assignee = (wp.get("fields") or {}).get("assignee") or {}
    name = assignee.get("displayName") or ""
    return name.strip() or None


def check_readiness(wps: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """
    Return (ready, reasons). A container is ready when every prerequisite
    WP is resolved per _READINESS_RESOLUTIONS AND the SMT Build WP is
    neither Done nor In Progress. Reasons are human-readable strings
    naming what blocked readiness; an empty list means fully ready.
    """
    reasons: list[str] = []

    for canonical, allowed in _READINESS_RESOLUTIONS.items():
        wp = find_wp_by_name(wps, canonical)
        if wp is None:
            reasons.append(f"{canonical}: missing")
            continue
        resolution = _wp_resolution_name(wp)
        if resolution not in allowed:
            reasons.append(
                f"{canonical}: resolution={resolution or '(none)'} "
                f"(need one of {sorted(allowed)})"
            )

    smt = find_wp_by_name(wps, "smt build")
    if smt is None:
        reasons.append("smt build: missing")
    else:
        status = _wp_status_name(smt)
        if status in SMT_BUILD_BLOCKED_STATUSES:
            reasons.append(f"smt build: status={status} (must not be Done/In Progress)")

    return (not reasons, reasons)


# ── Description HTML parsing ─────────────────────────────────────────


def _find_panel_header(soup: BeautifulSoup, needle: str) -> Tag | None:
    """Find the panelHeader div whose text contains `needle`."""
    for div in soup.find_all("div", class_="panelHeader"):
        if needle.lower() in div.get_text(" ", strip=True).lower():
            return div
    return None


def parse_item_table(description_html: str) -> list[dict[str, str]]:
    """
    Parse the "NPI Built Type & Quantities" table from the container's
    rendered description HTML. Returns a list of
    `{part_number, description, qty}` dicts — one per data row.

    Empty list when the panel or table is missing. Caller treats that
    as a skip+warning.
    """
    if not description_html:
        return []

    soup = BeautifulSoup(description_html, "html.parser")
    header = _find_panel_header(soup, "NPI Built Type")
    if header is None:
        return []

    table = header.find_next("table", class_="confluenceTable")
    if table is None:
        return []

    rows: list[dict[str, str]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            # header row (<th> cells) or a shorter row we don't use
            continue

        part_cell = tds[0]
        anchor = part_cell.find("a")
        part_text = (anchor.get_text(strip=True) if anchor else part_cell.get_text(strip=True))
        part_text = part_text.lstrip("#").strip()

        description = tds[1].get_text(" ", strip=True)
        qty_text = tds[2].get_text(" ", strip=True)

        if not part_text:
            continue

        rows.append({
            "part_number": part_text,
            "description": description,
            "qty": qty_text,
        })
    return rows


def parse_delivery_info(description_html: str) -> str:
    """
    Extract the "Usage of Samples" value from the Purpose of the NPI panel.
    Returns the raw text of the sibling <td>, or "" if not found.
    """
    if not description_html:
        return ""

    soup = BeautifulSoup(description_html, "html.parser")
    for td in soup.find_all("td"):
        bold = td.find("b")
        if bold and "usage of samples" in bold.get_text(strip=True).lower():
            sibling = td.find_next_sibling("td")
            if sibling is not None:
                return sibling.get_text(" ", strip=True)
    return ""


# ── Order type handling ──────────────────────────────────────────────

_ORDER_TYPE_LABELS = {
    "PR – Pilot Run": "Pilot Run",
    "DMR - Direct manufacturing release": "DMR",
    "QS – Qualification sample": "Qualification Sample",
    "DS – Development sample": "Development Sample",
}

# Pilot-run canonical value uses an EM-DASH, not a hyphen. The comparison
# normalises common dash variants so a typo in JIRA doesn't silently flip
# the pilot detection.
_PILOT_ORDER_TYPE = "PR – Pilot Run"
_DASH_VARIANTS = ("-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015")


def _normalise_dashes(value: str) -> str:
    out = value
    for variant in _DASH_VARIANTS:
        out = out.replace(variant, "-")
    return re.sub(r"\s+", " ", out).strip()


def extract_order_type(container: dict[str, Any]) -> str:
    """Return the raw Order Type value from customfield_13905, or ''."""
    fields = container.get("fields") or {}
    raw = fields.get(ORDER_TYPE_CF) or {}
    if isinstance(raw, dict):
        return (raw.get("value") or "").strip()
    return str(raw).strip()


def order_type_label(raw: str) -> str:
    """Map Order Type to a short label used in the comment header."""
    if not raw:
        return ""
    if raw in _ORDER_TYPE_LABELS:
        return _ORDER_TYPE_LABELS[raw]
    # dash-tolerant fallback match
    normalised = _normalise_dashes(raw)
    for key, label in _ORDER_TYPE_LABELS.items():
        if _normalise_dashes(key) == normalised:
            return label
    return raw


# ── Pilot run detection ──────────────────────────────────────────────


def detect_pilot_run(
    container: dict[str, Any],
    wps: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """
    Two-signal pilot check:
      * Order Type (customfield_13905) equals "PR – Pilot Run"
      * A child WP named "QM P+L" exists

    Returns (is_pilot, warnings). Mismatches flag a warning but treat the
    container as pilot (the safer assumption — it adds the MOI line rather
    than silently skipping it).
    """
    raw = extract_order_type(container)
    order_type_is_pilot = _normalise_dashes(raw) == _normalise_dashes(_PILOT_ORDER_TYPE)
    qm_wp = find_wp_by_name(wps, "qm p+l")
    qm_present = qm_wp is not None

    warnings: list[str] = []
    if order_type_is_pilot and qm_present:
        return True, warnings
    if not order_type_is_pilot and not qm_present:
        return False, warnings

    warnings.append(
        "pilot-run signal mismatch: "
        f"Order Type pilot={order_type_is_pilot}, QM P+L WP present={qm_present} — "
        "treating as pilot run"
    )
    return True, warnings


# ── Programme IC detection ───────────────────────────────────────────


def detect_programme_ic(
    container: dict[str, Any],
    wps: list[dict[str, Any]],
) -> bool:
    """
    Programme IC: container summary OR description contains "ICUC"
    AND the PE - TechnPrep WP resolution == "Won't Do".

    Effect: caller skips the "PE Please reuse buyoff Board" line.
    """
    fields = container.get("fields") or {}
    summary = (fields.get("summary") or "").lower()
    description = (fields.get("description") or "").lower()
    if "icuc" not in summary and "icuc" not in description:
        return False

    pe_wp = find_wp_by_name(wps, "pe - technprep")
    if pe_wp is None:
        return False
    return _wp_resolution_name(pe_wp) == "Won't Do"


# ── FYI list assembly ────────────────────────────────────────────────


def build_fyi_list(
    default_fyi: Iterable[str],
    reporter_display_name: str | None,
    wps: list[dict[str, Any]],
) -> list[str]:
    """
    Default names + container reporter + all WP assignees, deduplicated
    while preserving first-seen order. Empty/None names are dropped.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(name: str | None) -> None:
        if not name:
            return
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            return
        seen.add(cleaned)
        ordered.append(cleaned)

    for name in default_fyi or []:
        _add(name)
    _add(reporter_display_name)
    for wp in wps:
        _add(get_wp_assignee(wp))
    return ordered


# ── Working-day arithmetic ───────────────────────────────────────────

# Singapore public holidays — source: docs/LEGACY_REFERENCE.md (2025-2026
# hardcoded in legacy KPI scripts). Extend as official dates are published.
SG_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 4, 1),
    date(2025, 4, 18),
    date(2025, 5, 1),
    date(2025, 6, 17),
    # 2026
    date(2026, 1, 1),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 3, 23),
    date(2026, 4, 3),
    date(2026, 5, 1),
    date(2026, 5, 25),
    date(2026, 6, 8),
    date(2026, 8, 10),
    date(2026, 10, 22),
    date(2026, 12, 25),
}


def is_working_day(d: date, holidays: set[date] | None = None) -> bool:
    """Mon-Fri and not in the SG public holiday set."""
    if d.weekday() >= 5:
        return False
    return d not in (holidays if holidays is not None else SG_HOLIDAYS)


def next_working_day(after: date, holidays: set[date] | None = None) -> date:
    """Return the next strictly-after working day (skip weekends + holidays)."""
    d = after + timedelta(days=1)
    while not is_working_day(d, holidays):
        d += timedelta(days=1)
    return d


def add_working_days(start: date, n: int, holidays: set[date] | None = None) -> date:
    """Advance `n` working days from `start` (start itself is day 0)."""
    d = start
    added = 0
    while added < n:
        d += timedelta(days=1)
        if is_working_day(d, holidays):
            added += 1
    return d


def _ordinal(n: int) -> str:
    """Turn 21 into '21st', 2 into '2nd', etc."""
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def format_date(d: date) -> str:
    """Render a date as '21st April 2025'."""
    return f"{_ordinal(d.day)} {d.strftime('%B')} {d.year}"


# ── Comment assembly ─────────────────────────────────────────────────


def format_item_table(
    items: list[dict[str, str]],
    mo_start: date,
    mo_end: date,
    smt_line: str,
) -> str:
    """
    Render the item table as a wiki-style markdown block (the form shown
    in the TASK.md comment template). Each item row shows qty with "pcs"
    appended, MO start/end formatted '21st April 2025'.
    """
    header = (
        "| Item Number | Description | Qty | SMT Line | MO start | MO end |"
    )
    divider = (
        "|-------------|-------------|-----|----------|----------|--------|"
    )
    start_str = format_date(mo_start)
    end_str = format_date(mo_end)
    lines = [header, divider]
    for item in items:
        qty = item.get("qty", "").strip()
        qty_out = f"{qty}pcs" if qty and not qty.lower().endswith("pcs") else qty
        lines.append(
            f"| {item.get('part_number', '')} | {item.get('description', '')} "
            f"| {qty_out} | {smt_line} | {start_str} | {end_str} |"
        )
    return "\n".join(lines)


def assemble_comment(
    *,
    addressee: str,
    order_type_raw: str,
    items: list[dict[str, str]],
    mo_start: date,
    mo_end: date,
    smt_line: str,
    pe_assignee: str,
    te_assignee: str,
    qm_assignee: str,
    is_pilot: bool,
    is_programme_ic: bool,
    e5_status_line: str,
    breaking_status_line: str,
    packaging_material_status_line: str,
    aoi_test_status: str,
    delivery_info: str,
    fyi_list: list[str],
    imr_line: str = "IMR: [pending]",
) -> str:
    """Assemble the final MO-trigger comment body per the TASK.md spec."""
    label = order_type_label(order_type_raw) or "MO"

    table = format_item_table(items, mo_start, mo_end, smt_line)

    parts: list[str] = []
    parts.append(f"Hi {addressee},")
    parts.append("")
    parts.append(
        f"Please proceed for {label} MO planning of PCBAs shown below."
    )
    parts.append("")
    parts.append(table)
    parts.append("")
    parts.append(f"Please trigger @{pe_assignee} for the program creation.")
    parts.append("")
    if is_pilot:
        parts.append(
            "Please include in MO F6 text: Please Trigger "
            f"{qm_assignee} before packaging for MOI Check."
        )
        parts.append("")
    parts.append(f"E5: {e5_status_line}")
    parts.append("")
    parts.append("Depaneling Required")
    parts.append(breaking_status_line)
    parts.append(packaging_material_status_line)
    parts.append(f"Delivery: {delivery_info}")
    parts.append(imr_line)
    parts.append("")
    if not is_programme_ic:
        parts.append("PE: Please reuse buyoff Board")
        parts.append("")
    parts.append(f"{te_assignee} {aoi_test_status}".rstrip())
    parts.append("")
    parts.append(f"FYI: {', '.join(fyi_list)}")

    return "\n".join(parts)


# ── Duplicate-comment guard ──────────────────────────────────────────


def has_duplicate_marker(comments: list[dict[str, Any]], marker: str) -> bool:
    """True if any existing comment body contains the duplicate marker."""
    if not marker:
        return False
    for c in comments or []:
        body = c.get("body") or ""
        if marker in body:
            return True
    return False
