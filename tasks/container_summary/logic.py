"""
Pure business logic for container_summary.

No I/O here — functions take plain dicts / strings and return results.
Testable with mock data fixtures, no JIRA / Confluence / Anthropic access.

Covers:
  * Identity block extraction (custom field type handling)
  * Work-package roll-up line
  * Parking-log parsing
  * Keyword timeline from comments
  * Comment analysis (attachments, non-English, last human author)
  * Staleness / age in working days (SG holidays)
  * Flags
  * Confluence storage-format HTML assembly
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from typing import Any

from core.jira_client import JiraClient


# ── Custom-field IDs ─────────────────────────────────────────────────

CF_EDM_DOC = "customfield_13300"
CF_M3_ARTICLE = "customfield_13502"
CF_PROJECT_STATUS = "customfield_13700"
CF_REQUEST_TYPE = "customfield_13903"
CF_PRODUCT_TYPE = "customfield_13904"
CF_ORDER_TYPE = "customfield_13905"
CF_NPI_LOCATION = "customfield_13906"
CF_PTXX_DOCUMENT = "customfield_13907"
CF_STATUS_LIGHT = "customfield_15009"
CF_NPI_WC_STATUS = "customfield_15400"
CF_ISSUE_PARKED_LOG = "customfield_15800"
CF_COMPONENT_PART = "customfield_15805"


# ── SG public holidays (copied from mo_trigger_comment) ──────────────

SG_HOLIDAYS: set[date] = {
    date(2025, 1, 1), date(2025, 1, 29), date(2025, 1, 30),
    date(2025, 4, 1), date(2025, 4, 18), date(2025, 5, 1),
    date(2025, 6, 17),
    date(2026, 1, 1), date(2026, 2, 17), date(2026, 2, 18),
    date(2026, 3, 23), date(2026, 4, 3), date(2026, 5, 1),
    date(2026, 5, 25), date(2026, 6, 8), date(2026, 8, 10),
    date(2026, 10, 22), date(2026, 12, 25),
}


def _is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in SG_HOLIDAYS


def _working_days_between(start: date, end: date) -> int:
    """Inclusive count of working days between two dates. Returns 0 when end < start."""
    if end < start:
        return 0
    count = 0
    d = start
    while d <= end:
        if _is_working_day(d):
            count += 1
        d += timedelta(days=1)
    return count


# ── Custom-field helpers ─────────────────────────────────────────────


def _cf_dict_value(fields: dict[str, Any], cf_id: str) -> str:
    """Extract `.value` from a dict-typed custom field, or ''."""
    raw = fields.get(cf_id)
    if isinstance(raw, dict):
        return (raw.get("value") or "").strip()
    return str(raw or "").strip()


def _cf_string(fields: dict[str, Any], cf_id: str) -> str:
    raw = fields.get(cf_id)
    if raw is None:
        return ""
    return str(raw).strip()


# ── 1. extract_identity ──────────────────────────────────────────────


def extract_identity(issue: dict[str, Any]) -> dict[str, Any]:
    """Flatten all identity / header fields from a JIRA issue dict."""
    fields = issue.get("fields") or {}

    status = (fields.get("status") or {}).get("name", "") or ""
    assignee = (fields.get("assignee") or {}).get("displayName", "") or ""
    reporter = (fields.get("reporter") or {}).get("displayName", "") or ""

    created = JiraClient.parse_timestamp(fields.get("created") or "")
    updated = JiraClient.parse_timestamp(fields.get("updated") or "")

    return {
        "key": issue.get("key", ""),
        "summary": (fields.get("summary") or "").strip(),
        "status": status.strip(),
        "assignee": assignee.strip(),
        "reporter": reporter.strip(),
        "created": created,
        "updated": updated,
        "order_type": _cf_dict_value(fields, CF_ORDER_TYPE),
        "product_type": _cf_dict_value(fields, CF_PRODUCT_TYPE),
        "npi_location": _cf_dict_value(fields, CF_NPI_LOCATION),
        "request_type": _cf_dict_value(fields, CF_REQUEST_TYPE),
        "project_status": _cf_dict_value(fields, CF_PROJECT_STATUS),
        "wc_npi_status": _cf_dict_value(fields, CF_NPI_WC_STATUS),
        "status_light": _cf_dict_value(fields, CF_STATUS_LIGHT),
        "ptxx_document": _cf_string(fields, CF_PTXX_DOCUMENT),
        "edm_doc_number": _cf_string(fields, CF_EDM_DOC),
        "m3_article_number": _cf_string(fields, CF_M3_ARTICLE),
        "component_part_number": _cf_string(fields, CF_COMPONENT_PART),
    }


# ── 2. build_wp_rollup ───────────────────────────────────────────────

_DONE_RESOLUTIONS = {"Done", "Acknowledged", "Won't Do"}


def _wp_summary(wp: dict[str, Any]) -> str:
    return ((wp.get("fields") or {}).get("summary") or "").strip()


def _wp_status_name(wp: dict[str, Any]) -> str:
    return (((wp.get("fields") or {}).get("status") or {}).get("name") or "").strip()


def _wp_resolution_name(wp: dict[str, Any]) -> str:
    return (((wp.get("fields") or {}).get("resolution") or {}).get("name") or "").strip()


def _wp_assignee_name(wp: dict[str, Any]) -> str:
    return (((wp.get("fields") or {}).get("assignee") or {}).get("displayName") or "").strip()


def _find_wp_contains(children: list[dict[str, Any]], needle: str) -> dict[str, Any] | None:
    """Return the first WP whose summary contains `needle` (case-insensitive)."""
    target = needle.lower()
    for wp in children:
        if target in _wp_summary(wp).lower():
            return wp
    return None


def build_wp_rollup(children: list[dict[str, Any]]) -> dict[str, Any]:
    """Count WPs by status + resolution and format a one-line summary."""
    total = len(children)
    done = 0
    in_progress = 0
    waiting = 0
    backlog = 0
    other = 0
    no_assignee_in_progress = False

    for wp in children:
        status = _wp_status_name(wp)
        resolution = _wp_resolution_name(wp)
        if resolution in _DONE_RESOLUTIONS:
            done += 1
            continue
        low = status.lower()
        if low == "in progress":
            in_progress += 1
            if not _wp_assignee_name(wp):
                no_assignee_in_progress = True
        elif low == "waiting":
            waiting += 1
        elif low == "backlog":
            backlog += 1
        else:
            other += 1

    smt_wp = _find_wp_contains(children, "smt build")
    smt_build_status = _wp_status_name(smt_wp) if smt_wp else ""

    blocked_wps: list[str] = []
    for wp in children:
        if "blocked" in _wp_status_name(wp).lower():
            name = _wp_summary(wp) or wp.get("key", "?")
            blocked_wps.append(name)

    parts = [f"{done}/{total} done"]
    if smt_build_status:
        parts.append(f"SMT Build: {smt_build_status}")
    routing_wp = _find_wp_contains(children, "routing")
    if routing_wp:
        status = _wp_status_name(routing_wp)
        resolution = _wp_resolution_name(routing_wp)
        if resolution not in _DONE_RESOLUTIONS and status:
            parts.append(f"Routing: {status}")
    if blocked_wps:
        parts.append(f"BLOCKED: {', '.join(blocked_wps)}")

    return {
        "total": total,
        "done": done,
        "in_progress": in_progress,
        "waiting": waiting,
        "backlog": backlog,
        "other": other,
        "summary_line": " | ".join(parts) if total else "No WPs",
        "smt_build_status": smt_build_status,
        "blocked_wps": blocked_wps,
        "no_assignee_in_progress": no_assignee_in_progress,
    }


# ── 3. parse_parking_log ─────────────────────────────────────────────


_PARK_TS_RE = re.compile(
    r"(Start|End)\s*:\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})",
    re.IGNORECASE,
)


def _parse_park_ts(raw: str) -> datetime | None:
    raw = raw.replace("T", " ").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_parking_log(raw: str | None) -> dict[str, Any]:
    """
    Parse the `Issue_parked_log` string — `Start:YYYY-MM-DD HH:MM:SS;End:...;Start:...;`.

    Missing End on the last pair means the container is currently parked.
    Total parked working days is summed across all completed pairs plus
    (today - last_start) when currently parked.
    """
    entries: list[dict[str, Any]] = []
    currently_parked = False

    if not raw:
        return {"entries": [], "currently_parked": False, "total_parked_days": 0}

    matches = list(_PARK_TS_RE.finditer(raw))
    current: dict[str, Any] | None = None
    for m in matches:
        kind = m.group(1).lower()
        ts = _parse_park_ts(m.group(2))
        if ts is None:
            continue
        if kind == "start":
            if current is not None:
                entries.append(current)
            current = {"start": ts, "end": None}
        else:
            if current is None:
                continue
            current["end"] = ts
            entries.append(current)
            current = None
    if current is not None:
        entries.append(current)
        currently_parked = current["end"] is None

    total_days = 0
    today = date.today()
    for entry in entries:
        start = entry["start"].date() if entry.get("start") else None
        end = entry["end"].date() if entry.get("end") else today
        if start is None:
            continue
        total_days += _working_days_between(start, end)

    return {
        "entries": entries,
        "currently_parked": currently_parked,
        "total_parked_days": total_days,
    }


# ── 4. build_keyword_timeline ────────────────────────────────────────


_SIGNAL_KEYWORDS = [
    # structure / routing
    "created", "released", "completed", "verified",
    # material / delivery
    "ETA", "arrived", "booked-in", "shortage", "available",
    # build
    "MO", "stencil", "PnP program", "build",
    # shipping
    "TO:", "shipped", "handed-over",
    # blockers
    "parked", "flag", "delayed", "issue", "problem", "NG",
    # test
    "AOI", "test", "908", "False Call",
]


def _match_keywords(body: str) -> str | None:
    """Return the first keyword matched in the body, case-insensitive."""
    low = body.lower()
    for kw in _SIGNAL_KEYWORDS:
        if kw.lower() in low:
            return kw
    return None


def build_keyword_timeline(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Scan each comment body for the first matching signal keyword.
    Skip auto-generated comments (containing `#Ref:`). Return at most
    8 most-recent entries, newest first.
    """
    events: list[dict[str, Any]] = []
    for c in comments or []:
        body = (c.get("body") or "").strip()
        if not body or "#Ref:" in body:
            continue
        kw = _match_keywords(body)
        if kw is None:
            continue
        date_dt = JiraClient.parse_timestamp(c.get("created") or "")
        author = ((c.get("author") or {}).get("displayName") or "").strip()
        snippet = re.sub(r"\s+", " ", body)[:80]
        events.append({
            "date": date_dt,
            "author": author,
            "keyword": kw,
            "context": snippet,
        })

    events.sort(key=lambda e: e["date"] or datetime.min, reverse=True)
    return events[:8]


# ── 5. analyse_comments ──────────────────────────────────────────────

_NON_EN_RE = re.compile(r"[一-鿿぀-ヿäöüß]")
_ATTACH_RE = re.compile(r"(?:!\S+\.[a-zA-Z0-9]{2,5}[|!])|(?:\[\^[^\]]+\])")


def analyse_comments(comments: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate comment statistics: counts, flags, last human comment."""
    comments = comments or []
    total = len(comments)
    auto_count = 0
    attach_count = 0
    has_non_en = False
    last_human: dict[str, Any] | None = None
    last_human_date: datetime | None = None
    last_comment_date: datetime | None = None

    for c in comments:
        body = c.get("body") or ""
        created_dt = JiraClient.parse_timestamp(c.get("created") or "")
        if created_dt is not None:
            if last_comment_date is None or created_dt > last_comment_date:
                last_comment_date = created_dt

        is_auto = "#Ref:" in body
        if is_auto:
            auto_count += 1
        if _ATTACH_RE.search(body):
            attach_count += 1
        if _NON_EN_RE.search(body):
            has_non_en = True

        if not is_auto and created_dt is not None:
            if last_human_date is None or created_dt > last_human_date:
                last_human_date = created_dt
                last_human = {
                    "author": ((c.get("author") or {}).get("displayName") or "").strip(),
                    "date": created_dt,
                    "snippet": re.sub(r"\s+", " ", body).strip()[:150],
                }

    return {
        "total": total,
        "auto_generated": auto_count,
        "attachments": attach_count,
        "has_non_english": has_non_en,
        "last_human": last_human,
        "last_human_date": last_human_date,
        "last_comment_date": last_comment_date,
    }


# ── 6. calculate_staleness ───────────────────────────────────────────


def calculate_staleness(
    created: datetime | None,
    updated: datetime | None,
    last_human_date: datetime | None,
    today: date,
) -> dict[str, Any]:
    """Age in working days and staleness flags."""

    def _wd_gap(from_d: date) -> int:
        gap = _working_days_between(from_d, today) - 1
        return max(gap, 0)

    age_wd = _wd_gap(created.date()) if created else 0
    days_since_update = _wd_gap(updated.date()) if updated else 0
    days_since_human = (
        _wd_gap(last_human_date.date()) if last_human_date else None
    )
    is_stale = days_since_human is not None and days_since_human > 5

    return {
        "age_wd": age_wd,
        "days_since_update": days_since_update,
        "days_since_human": days_since_human,
        "is_stale": is_stale,
    }


# ── 7. build_flags ───────────────────────────────────────────────────


def build_flags(
    identity: dict[str, Any],
    wp_rollup: dict[str, Any],
    parking: dict[str, Any],
    comments_analysis: dict[str, Any],
    staleness: dict[str, Any],
) -> list[str]:
    """Collect short uppercase flag strings in sorted order."""
    flags: set[str] = set()
    if parking.get("currently_parked"):
        flags.add("PARKED")
    if staleness.get("is_stale"):
        flags.add("STALE")
    if wp_rollup.get("no_assignee_in_progress"):
        flags.add("NO_ASSIGNEE")
    if comments_analysis.get("has_non_english"):
        flags.add("NON_EN")
    if comments_analysis.get("total", 0) > 30:
        flags.add("HIGH_COMMENTS")
    if comments_analysis.get("attachments", 0) > 0:
        flags.add("ATTACHMENTS")
    return sorted(flags)


# ── 8. summarise_container ───────────────────────────────────────────


def summarise_container(
    issue: dict[str, Any],
    children: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    """Run all Phase-1 extractors and return a single summary dict."""
    fields = issue.get("fields") or {}
    comments = ((fields.get("comment") or {}).get("comments")) or []

    identity = extract_identity(issue)
    wp_rollup = build_wp_rollup(children)
    parking = parse_parking_log(fields.get(CF_ISSUE_PARKED_LOG))
    timeline = build_keyword_timeline(comments)
    comments_analysis = analyse_comments(comments)
    staleness = calculate_staleness(
        identity.get("created"),
        identity.get("updated"),
        comments_analysis.get("last_human_date"),
        today,
    )
    flags = build_flags(identity, wp_rollup, parking, comments_analysis, staleness)

    return {
        "key": identity["key"],
        "identity": identity,
        "wp_rollup": wp_rollup,
        "parking": parking,
        "timeline": timeline,
        "comments": comments_analysis,
        "staleness": staleness,
        "flags": flags,
        "narrative": "",
        "comment_count": comments_analysis["total"],
        "updated_raw": fields.get("updated") or "",
    }


# ── 9. build_confluence_html ─────────────────────────────────────────


_FLAG_COLOUR = {
    "PARKED": "Red",
    "STALE": "Red",
    "NO_ASSIGNEE": "Red",
    "NON_EN": "Yellow",
    "HIGH_COMMENTS": "Yellow",
    "ATTACHMENTS": "Green",
}


def _status_macro(label: str, colour: str) -> str:
    return (
        '<ac:structured-macro ac:name="status">'
        f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
        f'<ac:parameter ac:name="title">{html.escape(label)}</ac:parameter>'
        "</ac:structured-macro>"
    )


def _flags_cell(flags: list[str]) -> str:
    if not flags:
        return ""
    return " ".join(_status_macro(f, _FLAG_COLOUR.get(f, "Grey")) for f in flags)


def _fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def _fmt_datetime(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


_NARRATIVE_SECTIONS = ("Purpose", "Actions", "Risks", "History")
# Match headers whether the model wrapped them in ** or emitted plain
# text (the system prompt forbids markdown, but we're defensive).
# ``^`` with re.MULTILINE anchors to line start so a stray "Actions:"
# inside a sentence cannot split the narrative mid-flight.
_SECTION_SPLIT_RE = re.compile(
    r"(?m)^\s*(?:\*\*)?(" + "|".join(_NARRATIVE_SECTIONS) + r")\s*:\s*(?:\*\*)?\s*",
    re.IGNORECASE,
)


def _parse_narrative_sections(narrative: str) -> dict[str, str]:
    """
    Split the LLM narrative by its ``**Section:**`` headers and return
    the raw body of each. Missing sections map to an empty string so
    callers can emit them in a stable order regardless of what the model
    actually wrote.
    """
    sections = {h.lower(): "" for h in _NARRATIVE_SECTIONS}
    if not narrative:
        return sections
    parts = _SECTION_SPLIT_RE.split(narrative)
    # parts = [prelude, header_1, body_1, header_2, body_2, ...]
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip().lower()
        body = parts[i + 1].strip()
        if header in sections:
            sections[header] = body
    return sections


def _extract_bullets(body: str) -> list[str]:
    """Return every ``- `` / ``* `` / ``• `` bullet line, unwrapped."""
    items: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "• ")):
            items.append(stripped[2:].strip())
    return items


def _render_bullet_section(label: str, body: str) -> str:
    """
    Render one bulleted section. A one-line body with no bullets
    (e.g. "No open actions.") renders as a plain paragraph instead of
    an empty ``<ul>``.
    """
    body = body.strip()
    if not body:
        return ""
    header = f"<p><strong>{label}:</strong></p>"
    bullets = _extract_bullets(body)
    if bullets:
        return header + "<ul>" + "".join(
            f"<li>{html.escape(item)}</li>" for item in bullets
        ) + "</ul>"
    # Fallback for responses like "No risks identified." on a single line.
    return header + f"<p>{html.escape(body)}</p>"


def _narrative_html(narrative: str) -> str:
    """
    Render the Opus narrative into Confluence storage format, split by
    the four expected sections (Purpose, Actions, Risks, History).

    When the text doesn't contain recognisable section headers (e.g.
    the LLM call returned empty or malformed output), fall back to
    plain-paragraph rendering so the dashboard is still legible.
    """
    if not narrative or not narrative.strip():
        return "<p><em>No narrative available.</em></p>"

    sections = _parse_narrative_sections(narrative)
    if not any(sections.values()):
        return f"<p>{html.escape(narrative.strip())}</p>"

    parts: list[str] = []
    purpose = sections.get("purpose", "").strip()
    if purpose:
        parts.append(f"<p><em>{html.escape(purpose)}</em></p>")

    for label, key in (("Actions", "actions"),
                       ("Risks", "risks"),
                       ("History", "history")):
        section_html = _render_bullet_section(label, sections.get(key, ""))
        if section_html:
            parts.append(section_html)

    return "".join(parts) if parts else (
        f"<p>{html.escape(narrative.strip())}</p>"
    )


def _expand_macro(title: str, body_html: str) -> str:
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{html.escape(title)}</ac:parameter>'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        "</ac:structured-macro>"
    )


def _timeline_table(timeline: list[dict[str, Any]]) -> str:
    if not timeline:
        return "<p><em>No keyword events.</em></p>"
    rows = []
    for ev in timeline:
        rows.append(
            "<tr>"
            f"<td>{_fmt_date(ev.get('date'))}</td>"
            f"<td>{html.escape(ev.get('author') or '')}</td>"
            f"<td>{html.escape(ev.get('keyword') or '')}</td>"
            f"<td>{html.escape(ev.get('context') or '')}</td>"
            "</tr>"
        )
    return (
        '<table class="confluenceTable"><tbody>'
        '<tr><th class="confluenceTh">Date</th>'
        '<th class="confluenceTh">Author</th>'
        '<th class="confluenceTh">Keyword</th>'
        '<th class="confluenceTh">Context</th></tr>'
        + "".join(rows) +
        "</tbody></table>"
    )


def _row_detail_html(summary: dict[str, Any]) -> str:
    """Expandable detail body: narrative + timeline + last comment."""
    parts: list[str] = []
    parts.append(_narrative_html(summary.get("narrative") or ""))

    last = summary.get("comments", {}).get("last_human")
    if last:
        parts.append(
            f"<p><strong>Last human comment</strong> — {html.escape(last.get('author') or '')} @ "
            f"{_fmt_datetime(last.get('date'))}<br/>"
            f"{html.escape(last.get('snippet') or '')}</p>"
        )

    parts.append("<p><strong>Keyword timeline</strong></p>")
    parts.append(_timeline_table(summary.get("timeline") or []))

    parking = summary.get("parking") or {}
    if parking.get("entries"):
        park_rows = []
        for e in parking["entries"]:
            start = e.get("start")
            end = e.get("end")
            park_rows.append(
                f"<li>{_fmt_datetime(start)} &rarr; {_fmt_datetime(end) if end else '<em>(currently parked)</em>'}</li>"
            )
        parts.append(
            "<p><strong>Parking log</strong> "
            f"(total {parking.get('total_parked_days', 0)} wd)</p>"
            "<ul>" + "".join(park_rows) + "</ul>"
        )
    return "".join(parts)


def build_confluence_html(summaries: list[dict[str, Any]]) -> str:
    """
    Build the full Confluence storage-format HTML for the dashboard page.
    One table row per container, sorted in the order passed in.
    """
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not summaries:
        return (
            '<ac:structured-macro ac:name="panel">'
            '<ac:parameter ac:name="title">SMT PCBA Singapore — Container Summary</ac:parameter>'
            '<ac:rich-text-body>'
            f"<p><em>No active containers found.</em> Generated {generated_at}.</p>"
            "</ac:rich-text-body>"
            "</ac:structured-macro>"
        )

    header = (
        '<tr>'
        '<th class="confluenceTh">Key</th>'
        '<th class="confluenceTh">Summary</th>'
        '<th class="confluenceTh">Order Type</th>'
        '<th class="confluenceTh">Status</th>'
        '<th class="confluenceTh">WP Roll-up</th>'
        '<th class="confluenceTh">Narrative</th>'
        '<th class="confluenceTh">Last Activity</th>'
        '<th class="confluenceTh">Age (wd)</th>'
        '<th class="confluenceTh">Flags</th>'
        '</tr>'
    )

    rows: list[str] = []
    for s in summaries:
        identity = s.get("identity", {})
        key = identity.get("key", "")
        key_link = f'<a href="/browse/{html.escape(key)}">{html.escape(key)}</a>'
        summary_text = html.escape(identity.get("summary", ""))
        order_type = html.escape(identity.get("order_type", ""))
        status = html.escape(identity.get("status", ""))
        rollup = html.escape(s.get("wp_rollup", {}).get("summary_line", ""))

        detail = _expand_macro("View summary", _row_detail_html(s))

        last_human = s.get("comments", {}).get("last_human")
        if last_human:
            last_activity = (
                f"{_fmt_date(last_human.get('date'))}<br/>"
                f"<em>{html.escape(last_human.get('author') or '')}</em>"
            )
        else:
            last_activity = _fmt_date(s.get("comments", {}).get("last_comment_date"))

        staleness = s.get("staleness", {})
        age_wd = staleness.get("age_wd", 0)
        flags = _flags_cell(s.get("flags") or [])

        rows.append(
            "<tr>"
            f'<td class="confluenceTd">{key_link}</td>'
            f'<td class="confluenceTd">{summary_text}</td>'
            f'<td class="confluenceTd">{order_type}</td>'
            f'<td class="confluenceTd">{status}</td>'
            f'<td class="confluenceTd">{rollup}</td>'
            f'<td class="confluenceTd">{detail}</td>'
            f'<td class="confluenceTd">{last_activity}</td>'
            f'<td class="confluenceTd">{age_wd}</td>'
            f'<td class="confluenceTd">{flags}</td>'
            "</tr>"
        )

    body = (
        f"<p>Generated {generated_at}. {len(summaries)} active container(s).</p>"
        '<table class="confluenceTable"><tbody>'
        + header
        + "".join(rows)
        + "</tbody></table>"
    )

    return (
        '<ac:structured-macro ac:name="panel">'
        '<ac:parameter ac:name="title">SMT PCBA Singapore — Container Summary</ac:parameter>'
        '<ac:rich-text-body>'
        + body +
        "</ac:rich-text-body>"
        "</ac:structured-macro>"
    )
