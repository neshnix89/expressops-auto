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
    CF_REQUEST_TYPE, _get_field_value, to_date,
)

# What the container issue type is called in JIRA. Anything else coming back
# from a query that asks for exactly this is worth a warning, not a silent row.
WC_ISSUE_TYPE = "Work Container"

# ─── The three ways to say "these containers" ───
#
# template — the same NPI family the board draws from, WITHOUT the board's
#            open-status restriction: every work package cloned from the eight
#            ITPL templates, resolved back to its container. This is the only
#            source that can return a fully closed container, because filter
#            25423 requires an open work package to include one at all.
# board    — saved filter 25423 verbatim: the Project Parents of the template
#            clones that are Waiting / In Progress / Backlog. Matches the board
#            exactly, and therefore holds only containers with open work left.
# overlay  — the plain issue-type query tasks/kpi_overlay/main.py runs
#            (Work Container + SMT PCBA + SG/Trutnov). Independent of the
#            templates, so it also catches containers cloned from elsewhere.
SOURCES = ("template", "board", "overlay")
DEFAULT_SOURCE = "template"

# The eight NPI issue templates behind filter 25423. Config
# `container_reporters.template_keys` overrides the list.
TEMPLATE_KEYS = ("ITPL-769", "ITPL-760", "ITPL-756", "ITPL-750",
                 "ITPL-746", "ITPL-742", "ITPL-1036", "ITPL-1027")

# How many work-package keys go into one `key in (...)` clause. JIRA takes far
# more than this, but a shorter query is a readable query in the log.
KEY_CHUNK = 250

# Saved JIRA filter behind the board. Config `container_reporters.board_filter`
# overrides it — filter IDs change when a board is rebuilt.
BOARD_FILTER_ID = "25423"

TEMPLATE_LOCATIONS = ("Singapore",)
BOARD_LOCATIONS = ("Singapore",)
OVERLAY_LOCATIONS = ("Singapore", "Trutnov")

# The board filter selects OPEN work packages, so a container leaves the board
# once every child is finished. Callers warn about this on --scope resolved.
BOARD_IS_OPEN_WORK_ONLY = True

PRODUCT_TYPE_CLAUSE = '"Product Type" = "SMT PCBA"'

SCOPES = ("resolved", "open", "all")

# JIRA fields the export needs. `reporter` is a system field, not a customfield.
FIELDS = [
    "key", "summary", "issuetype", "parent", "status", "resolution",
    "resolutiondate", "created", "reporter", "assignee",
    CF_NPI_LOCATION, CF_ORDER_TYPE, CF_PRODUCT_TYPE, CF_REQUEST_TYPE,
    CF_PROJECT_ID,
]

# issueType/parentKey are carried so "is this row a container or a work
# package?" is answerable from the sheet instead of by argument. A Work
# Container has issueType "Work Container" and no parent; a Work Package
# would show its own type and its container's key.
CSV_COLUMNS = [
    "issueKey", "issueType", "parentKey", "reporter", "reporterUser",
    "reporterEmail", "assignee", "assigneeUser", "assigneeEmail",
    "resolvedDate", "resolvedTimestamp", "resolution",
    "status", "location", "orderType", "requestType", "created",
    "ptDocument", "summary",
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


def location_clause(locations: tuple[str, ...]) -> str:
    """`= "X"` for one location, `in ("X", "Y")` for several."""
    if len(locations) == 1:
        return f'"NPI Location" = "{locations[0]}"'
    joined = ", ".join(f'"{loc}"' for loc in locations)
    return f'"NPI Location" in ({joined})'


def base_clauses(source: str = DEFAULT_SOURCE,
                 board_filter: str = BOARD_FILTER_ID,
                 locations: tuple[str, ...] | None = None) -> list[str]:
    """The container-population clauses, before scope and dates."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r} — use one of: {', '.join(SOURCES)}")

    if source == "board":
        first = (f'issue in relation("filter={board_filter}", "Project Parent", '
                 "Tasks, Deviations, level1)")
        locations = locations or BOARD_LOCATIONS
    elif source == "template":
        # Filled in per chunk by parents_jql(); template runs as two queries.
        first = None
        locations = locations or TEMPLATE_LOCATIONS
    else:
        first = 'issuetype = "Work Container"'
        locations = locations or OVERLAY_LOCATIONS

    clauses = [PRODUCT_TYPE_CLAUSE, location_clause(locations)]
    return clauses if first is None else [first, *clauses]


def scope_and_date_clauses(scope: str = "resolved", since: str | None = None,
                           until: str | None = None) -> list[str]:
    """The resolution clause plus the resolved-date window, shared by sources.

    On ``all`` the window is OR'd with ``resolution is EMPTY``: a NULL resolved
    date fails any date comparison, so a bare bound would quietly delete every
    open container from the one scope that exists to include them.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r} — use one of: {', '.join(SCOPES)}")

    clauses = []
    if scope == "resolved":
        clauses.append("resolution is not EMPTY")
    elif scope == "open":
        clauses.append("resolution is EMPTY")

    if scope == "open":
        return clauses

    window = []
    if since:
        window.append(f'resolutiondate >= "{since}"')
    if until:
        # JQL compares against the timestamp, so "<= 2026-01-31" would drop
        # anything resolved during that day. Bound the end of the day.
        window.append(f'resolutiondate <= "{until} 23:59"')
    if window and scope == "all":
        clauses.append("(" + " AND ".join(window) + " OR resolution is EMPTY)")
    else:
        clauses.extend(window)
    return clauses


def order_by(scope: str) -> str:
    return " ORDER BY created ASC" if scope == "open" else " ORDER BY resolutiondate ASC"


def build_jql(scope: str = "resolved", since: str | None = None,
              until: str | None = None, source: str = "board",
              board_filter: str = BOARD_FILTER_ID,
              locations: tuple[str, ...] | None = None) -> str:
    """Single-query JQL for the ``board`` and ``overlay`` sources.

    ``template`` needs two queries (lineage_jql then parents_jql) and is not
    expressible here — nesting its relation() inside another relation() would
    need a third level of quoting that JQL has no character left for.
    """
    if source == "template":
        raise ValueError("the 'template' source runs lineage_jql + parents_jql, "
                         "not build_jql")
    clauses = base_clauses(source, board_filter=board_filter, locations=locations)
    clauses += scope_and_date_clauses(scope, since=since, until=until)
    return " AND ".join(clauses) + order_by(scope)


def lineage_jql(template_keys: tuple[str, ...] = TEMPLATE_KEYS) -> str:
    """Every work package cloned from the NPI templates, open or closed.

    This is saved filter 25423's own text with one clause removed — the
    ``status in (Waiting, "In Progress", Backlog)`` restriction. That clause is
    what keeps a finished container off the board, so dropping it is exactly
    what "the same filtering, but the closed ones" means.
    """
    keys = ", ".join(template_keys)
    inner = (f"issue in relation('key in ({keys})', 'Project Children', "
             "Tasks, Deviations, level4)")
    return (f'issue in relation("{inner}", "Project Children", '
            "'Clone from Template', level4) and project != 'Issue Template'")


def parents_jql(wp_keys: list[str], scope: str = "resolved",
                since: str | None = None, until: str | None = None,
                locations: tuple[str, ...] | None = None) -> str:
    """The containers of a batch of work packages, filtered to scope + dates.

    Takes the WP keys rather than nesting :func:`lineage_jql` inside another
    relation() — one level of quoting instead of three, and the log shows
    exactly which issues each container came from.
    """
    if not wp_keys:
        raise ValueError("parents_jql needs at least one work-package key")
    keys = ", ".join(wp_keys)
    clauses = [
        f'issue in relation("key in ({keys})", "Project Parent", '
        "Tasks, Deviations, level1)",
        *base_clauses("template", locations=locations),
    ]
    clauses += scope_and_date_clauses(scope, since=since, until=until)
    return " AND ".join(clauses) + order_by(scope)


def chunk(items: list[str], size: int = KEY_CHUNK) -> list[list[str]]:
    """Split a key list into JQL-sized batches."""
    return [items[i:i + size] for i in range(0, len(items), size)]


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
    a_display, a_user, a_email = _user(fields.get("assignee"))

    resolved_ts = fields.get("resolutiondate") or ""
    resolved_d = to_date(resolved_ts)
    created_d = to_date(fields.get("created"))

    status = fields.get("status") or {}
    resolution = fields.get("resolution") or {}

    issue_type = fields.get("issuetype") or {}
    parent = fields.get("parent") or {}

    return {
        "issueKey": issue.get("key", ""),
        "issueType": issue_type.get("name", "") if isinstance(issue_type, dict) else "",
        "parentKey": parent.get("key", "") if isinstance(parent, dict) else "",
        "reporter": display,
        "reporterUser": user,
        "reporterEmail": email,
        "assignee": a_display,
        "assigneeUser": a_user,
        "assigneeEmail": a_email,
        "resolvedDate": str(resolved_d) if resolved_d else "",
        "resolvedTimestamp": resolved_ts,
        "resolution": resolution.get("name", "") if isinstance(resolution, dict) else "",
        "status": status.get("name", "") if isinstance(status, dict) else "",
        "location": _get_field_value(fields, CF_NPI_LOCATION, "") or "",
        "orderType": _get_field_value(fields, CF_ORDER_TYPE, "") or "",
        "requestType": _get_field_value(fields, CF_REQUEST_TYPE, "") or "",
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


def non_containers(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Rows that are not container-level: a different issue type, or a parent.

    The JQL asks for ``issuetype = "Work Container"`` so this should always be
    empty. It is checked anyway — a renamed issue type or a JIRA-side hierarchy
    change would otherwise slide Work Packages into the export unnoticed.
    """
    return [r for r in rows
            if (r.get("issueType") and r["issueType"] != WC_ISSUE_TYPE)
            or r.get("parentKey")]


def count_by(rows: list[dict[str, str]], column: str) -> list[tuple[str, int]]:
    """Value → count for one column, most frequent first (ties alphabetical)."""
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get(column) or "(none)"
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
