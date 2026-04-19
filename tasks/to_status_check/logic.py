"""
Pure business logic for to_status_check.

No I/O here — functions take plain dicts/lists and return results.
This keeps the logic unit-testable without JIRA or M3 access.

Phase A: Extract TO numbers from JIRA comments.
Phase B: Enrich rows with M3 XDRX800 TO status data.
"""

import re
from datetime import datetime
from typing import Any

TO_PATTERN = re.compile(r"TO:\s*(\d+)", re.IGNORECASE)

# ── Phase A: JIRA TO extraction ────────────────────────────────────────


def extract_to_from_text(text: str) -> str | None:
    """Return the first TO number found in a block of text, or None."""
    if not text:
        return None
    match = TO_PATTERN.search(text)
    return match.group(1) if match else None


def parse_comment_timestamp(ts: str) -> datetime:
    """
    Parse a JIRA comment 'created' timestamp.
    Strips milliseconds and timezone offsets — same quirks as core.jira_client.
    Falls back to datetime.min if unparseable so sorting remains stable.
    """
    if not ts:
        return datetime.min
    cleaned = re.sub(r"\.\d+", "", ts)
    cleaned = re.sub(r"[+-]\d{2}:?\d{2}$", "", cleaned)
    try:
        return datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return datetime.min


def latest_to_from_comments(comments: list[dict[str, Any]]) -> str | None:
    """
    Given a list of JIRA comment dicts, return the TO number from the LATEST
    comment that contains one. Returns None if no comment has a TO number.

    Each comment is expected to have 'body' and 'created' keys.
    """
    candidates = []
    for comment in comments:
        body = comment.get("body") or ""
        to_number = extract_to_from_text(body)
        if to_number:
            candidates.append((parse_comment_timestamp(comment.get("created", "")), to_number))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def build_container_row(issue: dict[str, Any]) -> dict[str, Any]:
    """
    Build a result row for a single JIRA Work Container issue.

    Expects issue['fields']['comment']['comments'] to be present (from expand=comment
    or fields=comment). Missing comments are treated as 'No TO'.
    """
    key = issue.get("key", "?")
    fields = issue.get("fields", {}) or {}
    summary = fields.get("summary", "") or ""
    status = (fields.get("status") or {}).get("name", "") or ""
    comments = ((fields.get("comment") or {}).get("comments")) or []

    to_number = latest_to_from_comments(comments)

    return {
        "key": key,
        "summary": summary,
        "status": status,
        "to_number": to_number,
        "has_to": to_number is not None,
        # Phase B fields — populated by enrich_rows_with_to_status()
        "to_status": None,
        "to_status_code": None,
        "to_sending_site": None,
        "to_receiving_site": None,
        "to_receiver": None,
        "to_creation_date": None,
        "to_arrived_date": None,
        "ready_to_close": _compute_ready_to_close(None),
    }


def _compute_ready_to_close(to_status_code: Any) -> bool:
    """True when the M3 TO status code is >= 90 (terminal states)."""
    if to_status_code is None:
        return False
    try:
        return int(to_status_code) >= 90
    except (TypeError, ValueError):
        return False


# ── Phase B: M3 TO status enrichment ──────────────────────────────────

# Status codes from XDRX800. Not exhaustive — derived from observed data.
# See M3_CONNECTIVITY_REFERENCE.md for column map.
TO_STATUS_LABELS = {
    "10": "Created",
    "20": "TO note printed",
    "30": "Arrived at logistics",
    "40": "Released for transport",
    "44": "Shipped from sending site",
    "50": "Arrived at forwarding site",
    "54": "Shipped from forwarding site 1",
    "60": "Arrived at receiving site",
    "70": "TO received",
    "80": "Closed",
    "89": "Deleted",
}


def enrich_rows_with_to_status(
    rows: list[dict[str, Any]],
    to_statuses: dict[str, dict[str, Any] | None],
) -> list[dict[str, Any]]:
    """
    Merge M3 XDRX800 TO status data into container rows.

    Args:
        rows: Container rows from build_container_row() (Phase A output).
        to_statuses: {to_number: status_dict_or_None} from M3H5Client.

    Returns:
        The same rows list, mutated in place with Phase B fields populated.
    """
    for row in rows:
        to_num = row.get("to_number")
        if not to_num or to_num not in to_statuses:
            continue

        m3_data = to_statuses[to_num]
        if not m3_data:
            continue

        row["to_status"] = m3_data.get("status", "")
        row["to_status_code"] = m3_data.get("status_code", "")
        row["to_sending_site"] = m3_data.get("sending_site", "")
        row["to_receiving_site"] = m3_data.get("receiving_site", "")
        row["to_receiver"] = m3_data.get("receiver", "")
        row["to_creation_date"] = m3_data.get("creation_date", "")
        row["to_arrived_date"] = m3_data.get("arrived_at_logistics", "")
        row["ready_to_close"] = _compute_ready_to_close(row["to_status_code"])

    return rows


# ── Output formatting ─────────────────────────────────────────────────


def format_table(rows: list[dict[str, Any]], include_m3: bool = False) -> str:
    """
    Format result rows as a plain-text console table.

    When include_m3 is True, adds the TO Status column from Phase B.
    """
    if not rows:
        return "(no containers)"

    if include_m3:
        header = ("Container", "Status", "TO Number", "TO Status", "Summary")
        data_rows = [
            (
                r["key"],
                r["status"],
                r["to_number"] or "-",
                r.get("to_status") or "-",
                (r["summary"] or "")[:50],
            )
            for r in rows
        ]
    else:
        header = ("Container", "Status", "TO Number", "Summary")
        data_rows = [
            (
                r["key"],
                r["status"],
                r["to_number"] or "-",
                (r["summary"] or "")[:60],
            )
            for r in rows
        ]

    widths = [
        max(len(header[i]), max((len(cells[i]) for cells in data_rows), default=0))
        for i in range(len(header))
    ]

    def fmt_row(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines = [fmt_row(header), fmt_row(tuple("-" * w for w in widths))]
    for cells in data_rows:
        lines.append(fmt_row(cells))
    return "\n".join(lines)


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Return counts of containers with/without TO numbers and M3 status."""
    with_to = sum(1 for r in rows if r["has_to"])
    with_m3 = sum(1 for r in rows if r.get("to_status"))
    return {
        "total": len(rows),
        "with_to": with_to,
        "without_to": len(rows) - with_to,
        "with_m3_status": with_m3,
    }
