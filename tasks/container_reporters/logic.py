"""
container_reporters — pure logic: JQL construction and row extraction.

No I/O here, so the same code that runs against live JIRA on the company
laptop is exercised by ``--mock`` on the VPS.

The container filter is deliberately identical to the KPI overlay
(tasks/kpi_overlay/main.py OPEN_WC_JQL) apart from the resolution clause:
the overlay only wants OPEN containers, this export is normally about
RESOLVED ones. Everything else — issue type, Product Type, NPI Location —
is the same set, so a container that appears on the Kanban overlay is the
same container that appears in this CSV.
"""

from __future__ import annotations

import re
from typing import Any

from core.kpi_core import (
    CF_NPI_LOCATION, CF_ORDER_TYPE, CF_PRODUCT_TYPE, CF_PROJECT_ID,
    _get_field_value, to_date,
)

# Same three clauses the KPI overlay filters on.
BASE_CLAUSES = [
    'issuetype = "Work Container"',
    '"Product Type" = "SMT PCBA"',
    '"NPI Location" in ("Singapore", "Trutnov")',
]

SCOPES = ("resolved", "open", "all")

# JIRA fields the export needs. `reporter` is a system field, not a customfield.
FIELDS = [
    "key", "summary", "status", "resolution", "resolutiondate", "created",
    "reporter", "assignee",
    CF_NPI_LOCATION, CF_ORDER_TYPE, CF_PRODUCT_TYPE, CF_PROJECT_ID,
]

CSV_COLUMNS = [
    "issueKey", "reporter", "reporterUser", "reporterEmail",
    "resolvedDate", "resolvedTimestamp", "resolution", "status",
    "location", "orderType", "created", "ptDocument", "summary",
]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_date(value: str | None, flag: str) -> str | None:
    """Validate a --since/--until value as YYYY-MM-DD. Returns it unchanged."""
    if value is None:
        return None
    value = value.strip()
    if not DATE_RE.match(value):
        raise ValueError(f"{flag} must be a date like 2026-01-31, got {value!r}")
    return value


def build_jql(scope: str = "resolved", since: str | None = None,
              until: str | None = None) -> str:
    """Build the container JQL for a scope and optional resolved-date window.

    scope:
      resolved — resolution is not EMPTY  (the default; these have a resolved date)
      open     — resolution is EMPTY      (exactly the KPI overlay's set)
      all      — no resolution clause     (resolved date is blank for open ones)

    ``since``/``until`` are inclusive bounds on ``resolutiondate`` (YYYY-MM-DD)
    and only apply where a resolved date can exist.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} — use one of: {', '.join(SCOPES)}")

    clauses = list(BASE_CLAUSES)
    if scope == "resolved":
        clauses.append("resolution is not EMPTY")
    elif scope == "open":
        clauses.append("resolution is EMPTY")

    if scope != "open":
        if since:
            clauses.append(f'resolutiondate >= "{since}"')
        if until:
            # JQL compares against the timestamp, so "<= 2026-01-31" would drop
            # anything resolved during that day. Bound the end of the day.
            clauses.append(f'resolutiondate <= "{until} 23:59"')

    order = "created ASC" if scope == "open" else "resolutiondate ASC"
    return " AND ".join(clauses) + f" ORDER BY {order}"


def _user(field: dict[str, Any] | None) -> tuple[str, str, str]:
    """(displayName, username, email) from a JIRA user field. Blanks if unset."""
    if not isinstance(field, dict):
        return "", "", ""
    return (
        field.get("displayName") or "",
        field.get("name") or field.get("key") or "",
        field.get("emailAddress") or "",
    )


def issue_row(issue: dict[str, Any]) -> dict[str, str]:
    """Flatten one container issue into the CSV row shape."""
    fields = issue.get("fields", {}) or {}
    display, user, email = _user(fields.get("reporter"))

    resolved_ts = fields.get("resolutiondate") or ""
    resolved_d = to_date(resolved_ts)
    created_d = to_date(fields.get("created"))

    status = fields.get("status") or {}
    resolution = fields.get("resolution") or {}

    return {
        "issueKey": issue.get("key", ""),
        "reporter": display,
        "reporterUser": user,
        "reporterEmail": email,
        "resolvedDate": str(resolved_d) if resolved_d else "",
        "resolvedTimestamp": resolved_ts,
        "resolution": resolution.get("name", "") if isinstance(resolution, dict) else "",
        "status": status.get("name", "") if isinstance(status, dict) else "",
        "location": _get_field_value(fields, CF_NPI_LOCATION, "") or "",
        "orderType": _get_field_value(fields, CF_ORDER_TYPE, "") or "",
        "created": str(created_d) if created_d else "",
        "ptDocument": _get_field_value(fields, CF_PROJECT_ID, "") or "",
        "summary": (fields.get("summary") or "").replace("\n", " ").strip(),
    }


def build_rows(issues: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Rows for every container, sorted by resolved date then key.

    Unresolved containers (scope ``all``) sort last — a blank resolved date
    should not lead the file.
    """
    rows = [issue_row(i) for i in issues]
    rows.sort(key=lambda r: (r["resolvedDate"] == "", r["resolvedDate"], r["issueKey"]))
    return rows


def filter_rows(rows: list[dict[str, str]], scope: str = "resolved",
                since: str | None = None, until: str | None = None
                ) -> list[dict[str, str]]:
    """Apply the scope / date window locally.

    Live runs let JIRA do this (build_jql). Mock runs read one fixture file and
    filter here, so ``--mock`` still shows what a given scope would return.
    """
    out = []
    for row in rows:
        resolved = row["resolvedDate"]
        if scope == "resolved" and not resolved:
            continue
        if scope == "open" and resolved:
            continue
        if scope != "open" and resolved:
            if since and resolved < since:
                continue
            if until and resolved > until:
                continue
        out.append(row)
    return out


def count_by(rows: list[dict[str, str]], column: str) -> list[tuple[str, int]]:
    """Value → count for one column, most frequent first (ties alphabetical)."""
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get(column) or "(none)"
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
