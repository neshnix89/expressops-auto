"""
Pure business logic for to_status_check.

No I/O here — functions take plain dicts/lists and return results.
This keeps the logic unit-testable without JIRA or M3 access.
"""

import re
from datetime import datetime
from typing import Any

TO_PATTERN = re.compile(r"TO:\s*(\d+)", re.IGNORECASE)


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
    }


def format_table(rows: list[dict[str, Any]]) -> str:
    """Format result rows as a plain-text console table."""
    if not rows:
        return "(no containers)"

    header = ("Container", "Status", "TO Number", "Summary")
    widths = [
        max(len(header[0]), max((len(r["key"]) for r in rows), default=0)),
        max(len(header[1]), max((len(r["status"]) for r in rows), default=0)),
        max(len(header[2]), max((len(r["to_number"] or "-") for r in rows), default=0)),
        max(len(header[3]), max((len((r["summary"] or "")[:60]) for r in rows), default=0)),
    ]

    def fmt_row(cells: tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines = [fmt_row(header), fmt_row(tuple("-" * w for w in widths))]
    for r in rows:
        lines.append(fmt_row((
            r["key"],
            r["status"],
            r["to_number"] or "-",
            (r["summary"] or "")[:60],
        )))
    return "\n".join(lines)


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Return counts of containers with/without TO numbers."""
    with_to = sum(1 for r in rows if r["has_to"])
    return {
        "total": len(rows),
        "with_to": with_to,
        "without_to": len(rows) - with_to,
    }
