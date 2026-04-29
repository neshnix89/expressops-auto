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
# "Won't Do" counts as resolved for every prerequisite — planners use it
# whenever a step is legitimately skipped (e.g. Programme IC skipping PE,
# reused PCB skipping PCB, existing routing skipping Routing/TE).
_DONE_RESOLUTIONS: set[str] = {"Done", "Acknowledged", "Won't Do"}
_READINESS_RESOLUTIONS: dict[str, set[str]] = {
    "material": _DONE_RESOLUTIONS,
    "pcb": _DONE_RESOLUTIONS,
    "routing - technprep": _DONE_RESOLUTIONS,
    "pe - technprep": _DONE_RESOLUTIONS,
    "te - technprep": _DONE_RESOLUTIONS,
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


def get_wp_assignee_username(wp: dict[str, Any] | None) -> str | None:
    """
    Return assignee JIRA username (login name) from `fields.assignee.name`,
    or None when unassigned/missing. Used to build `[~username]` mentions
    that JIRA's wiki renderer expands to linked display names.
    """
    if not wp:
        return None
    assignee = (wp.get("fields") or {}).get("assignee") or {}
    name = assignee.get("name") or ""
    return name.strip() or None


def jira_mention(username: str | None, fallback: str = "[UNASSIGNED]") -> str:
    """
    Format a JIRA on-prem user mention. `[~username]` is expanded by the
    wiki renderer to a linked display name on read. When the username is
    missing, returns `fallback` so the comment still has a placeholder
    that a reviewer will notice.
    """
    if not username:
        return fallback
    return f"[~{username}]"


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


def _header_cells(tr: Tag) -> list[Tag]:
    """
    Return cells from a row if it looks like a header row.

    Format A (NPIOTHER-4566): true <th> cells.
    Format B (ACDC-1041): <td><b>...</b></td> cells acting as headers.
    Returns [] when the row is clearly a data row.
    """
    ths = tr.find_all("th", recursive=False)
    if ths:
        return ths
    tds = tr.find_all("td", recursive=False)
    if not tds:
        return []
    # Treat as a header row only when every cell contains a <b> and no
    # <a> link — a real item row has an anchor on the part-number cell.
    if any(td.find("a") for td in tds):
        return []
    if all(td.find("b") is not None for td in tds):
        return tds
    return []


def _header_text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True).lower()


def _map_columns(headers: list[Tag]) -> dict[str, int]:
    """
    Find the column index for part number / description / qty by header
    text. Order of preference follows the two confirmed production
    layouts — Format B has a "Build type" col before the part number and
    uses a literal "#PN" / "PN" label; Format A uses "Part Number PCBA".

    First match wins per role so a later "Yearly Forecast" column can't
    steal the qty slot from "Request Qty".
    """
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(headers):
        text = _header_text(cell)
        if "part_col" not in mapping and (
            "part number" in text
            or text in {"#pn", "pn"}
            or text.endswith(" pn")
            or text.startswith("pn ")
            or text.startswith("#pn")
        ):
            mapping["part_col"] = idx
            continue
        if "desc_col" not in mapping and "description" in text:
            mapping["desc_col"] = idx
            continue
        if "qty_col" not in mapping and "request" in text:
            mapping["qty_col"] = idx
            continue
    return mapping


_DIGIT_RE = re.compile(r"\d+")


def _extract_part_number(cell: Tag) -> str:
    """
    Part number lives inside an <a> tag in Format A (with leading #),
    or as plain text in Format B (often with a leading # from the "#PN"
    header column). Strip a leading # in either case.
    """
    anchor = cell.find("a")
    text = anchor.get_text(strip=True) if anchor else cell.get_text(" ", strip=True)
    return text.lstrip("#").strip()


def _extract_qty(cell: Tag) -> str:
    """
    Qty cells may contain a strikethrough old value and a bold new value
    (`<del>72</del> <b>96</b>`). Prefer the <b> content, then reduce to
    digits only so the assembled comment shows the current target qty.
    """
    bold = cell.find("b")
    source = bold.get_text(" ", strip=True) if bold else cell.get_text(" ", strip=True)
    match = _DIGIT_RE.search(source)
    return match.group(0) if match else source.strip()


def parse_item_table(description_html: str) -> list[dict[str, str]]:
    """
    Parse the "NPI Built Type & Quantities" table from the container's
    rendered description HTML. Returns a list of
    `{part_number, description, qty}` dicts — one per data row.

    Two production formats are supported (column order differs):
      * Format A (NPIOTHER-4566): <th> headers; part number in col 0
        inside an <a> tag, prefixed with "#".
      * Format B (ACDC-1041):    <td><b> headers; part number in col 1
        as plain text; qty may carry <del>old</del> <b>new</b>.

    Column roles are resolved by header text ("PN"/"Part Number",
    "Description", "Request"), never by hard-coded index.

    Empty list when the panel, table, or expected columns are missing.
    Caller treats that as a skip+warning.
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

    trs = table.find_all("tr")
    columns: dict[str, int] | None = None
    rows: list[dict[str, str]] = []

    for tr in trs:
        header_cells = _header_cells(tr)
        if header_cells:
            if columns is None:
                columns = _map_columns(header_cells)
            continue

        if columns is None or not {"part_col", "desc_col", "qty_col"}.issubset(columns):
            # Saw a data row before a usable header — skip; a later
            # header row (unusual) would still set `columns`.
            continue

        tds = tr.find_all("td", recursive=False)
        max_idx = max(columns.values())
        if len(tds) <= max_idx:
            continue

        part_text = _extract_part_number(tds[columns["part_col"]])
        if not part_text:
            continue
        description = tds[columns["desc_col"]].get_text(" ", strip=True)
        qty_text = _extract_qty(tds[columns["qty_col"]])

        # Planners sometimes leave a 0-qty row as a placeholder for
        # articles that were dropped from the build. Don't surface them
        # in the assembled comment.
        if qty_text.strip() in {"", "0"}:
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
    default_fyi: Iterable[dict[str, str]],
    reporter_username: str | None,
    reporter_display_name: str | None,
    wps: list[dict[str, Any]],
) -> list[str]:
    """
    Default people + container reporter + all WP assignees, emitted as
    JIRA ``[~username]`` mentions (the wiki renderer expands them to a
    linked display name on read). When a username is missing the plain
    display name is used as a readable fallback.

    ``default_fyi`` is a list of dicts like ``{username, display}`` —
    usernames are required for a clickable mention; display is used for
    dedupe keying and as a fallback.

    Deduplicated by username (case-insensitive) when present, else by
    display name. Order of first appearance is preserved.
    """
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(username: str | None, display: str | None) -> None:
        uname = (username or "").strip()
        disp = (display or "").strip()
        if not uname and not disp:
            return
        key = uname.lower() if uname else disp.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(jira_mention(uname) if uname else disp)

    for entry in default_fyi or []:
        if isinstance(entry, dict):
            _add(entry.get("username"), entry.get("display"))
        # Bare strings are ignored — config must be migrated to the
        # {username, display} form for clickable mentions.

    _add(reporter_username, reporter_display_name)
    for wp in wps:
        _add(get_wp_assignee_username(wp), get_wp_assignee(wp))
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
    Render the item table in JIRA wiki markup format. Header row uses
    double-pipe delimiters (||col||); data rows use single-pipe (|val|).
    Each item row shows qty with "pcs" appended, MO start/end formatted
    '21st April 2025'.
    """
    header = "||Item Number||Description||Qty||SMT Line||MO start||MO end||"
    start_str = format_date(mo_start)
    end_str = format_date(mo_end)
    lines = [header]
    for item in items:
        qty = item.get("qty", "").strip()
        qty_out = f"{qty}pcs" if qty and not qty.lower().endswith("pcs") else qty
        lines.append(
            f"|{item.get('part_number', '')}|{item.get('description', '')}"
            f"|{qty_out}|{smt_line}|{start_str}|{end_str}|"
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
    # pe_assignee is already a formatted mention (e.g. "[~jdoe]") or an
    # [UNASSIGNED] placeholder — don't prepend a literal "@".
    parts.append(f"Please trigger {pe_assignee} for the program creation.")
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
