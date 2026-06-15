"""
NPI Container Template Audit — Batch System

Audits all active SMT PCBA Singapore containers against the NPI template rules,
publishes results to Confluence page 592255806, and supports posting draft
comments to JIRA.

Usage:
    python batch.py scan                       # audit all, publish to Confluence
    python batch.py scan --dry-run             # audit all, print results, no publish
    python batch.py scan --live                # same but hit live systems
    python batch.py comment --keys K1 K2      # post draft comments to JIRA
    python batch.py comment --keys K1 --dry-run
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    import yaml
except ImportError:
    print("Missing dependency: pip install pyyaml")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependency: pip install beautifulsoup4")
    sys.exit(1)

from core.config_loader import load_config
from core.confluence import ConfluenceClient
from core.jira_client import JiraClient
from core.logger import get_logger

from tasks.container_template_audit.main import AuditReport, Finding, NPIAuditor, Severity

AUDIT_PAGE_ID = "592255806"
GUARD_MARKER = "#Ref: AuditCheck#"
RULES_PATH = PROJECT_ROOT / "config" / "audit_rules.yaml"
MOCK_DIR = TASK_DIR / "mock_data"

# Container fields the audit depends on. get_issue() returns the full issue,
# so this list documents the contract rather than restricting the fetch.
# customfield_12401 (Cloned from Template Issue) is captured for reference;
# it is not currently used by a rule (the template-clone check was dropped in
# favour of the Product Type check — see config/audit_rules.yaml).
WC_FIELDS = [
    "summary", "status", "resolution", "issuetype", "description",
    "assignee", "reporter", "project", "parent", "components", "labels",
    "customfield_10014",  # Epic Link / Project Parent
    "customfield_13300",  # EDM Document Number
    "customfield_13903",  # Request Type
    "customfield_13904",  # Product Type
    "customfield_13905",  # Order Type
    "customfield_13906",  # NPI Location
    "customfield_13907",  # PTxx Document
    "customfield_12401",  # Cloned from Template Issue
    "customfield_15400",  # NPI WC Status
    "customfield_15800",  # Issue_parked_log
]

# Fields fetched for each child Work Package (via relation() JQL).
WP_FIELDS = ["summary", "created", "status", "resolution"]

ACTIVE_CONTAINERS_JQL = (
    'issuetype = "Work Container" '
    'AND "Product Type" = "SMT PCBA" '
    'AND "NPI Location" = "Singapore" '
    'AND resolution is EMPTY '
    'ORDER BY created ASC'
)


# ---------------------------------------------------------------------------
# Compatibility shim — lets NPIAuditor use pre-fetched issue data
# ---------------------------------------------------------------------------

class _CachedJiraClient:
    """Returns pre-fetched issue data so NPIAuditor skips a second API call."""

    def __init__(self, issue_data: dict):
        self._data = issue_data

    def get_issue(self, key: str) -> dict:
        return self._data


# ---------------------------------------------------------------------------
# YAML rule engine
# ---------------------------------------------------------------------------

def load_audit_rules() -> list[dict]:
    if not RULES_PATH.exists():
        return []
    with open(RULES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [r for r in (data.get("rules") or []) if r.get("enabled", True)]


def _desc_field_empty_html(rendered_html: str, field_label: str) -> bool:
    """Return True if the cell adjacent to field_label is blank/dash in HTML."""
    if not rendered_html or not field_label:
        return False
    soup = BeautifulSoup(rendered_html, "html.parser")
    for td in soup.find_all(["td", "th"]):
        if field_label.lower() in td.get_text().lower():
            nxt = td.find_next_sibling(["td", "th"])
            if nxt:
                val = nxt.get_text(strip=True)
                return not val or val in ("-", "–", "—")
    return False


def _field_str(fields: dict, field_id: str) -> str:
    """Stringify a JIRA custom field value (string / {value|name} / list)."""
    return (NPIAuditor._cf(fields, field_id) or "").strip()


def _fmt_date(ts: str) -> str:
    """Format a JIRA timestamp as YYYY-MM-DD; fall back to the first 10 chars."""
    if not ts:
        return "?"
    parsed = JiraClient.parse_timestamp(ts)
    if parsed:
        return parsed.strftime("%Y-%m-%d")
    return ts[:10]


def _wp_info(child: dict) -> dict:
    """Normalise a child Work Package issue into the fields the wp_* rules need."""
    f = child.get("fields") or {}
    return {
        "key": child.get("key", "") or "",
        "summary": (f.get("summary") or "").strip(),
        "created": f.get("created") or "",
        "status": ((f.get("status") or {}).get("name") or "").strip(),
        "resolution": ((f.get("resolution") or {}).get("name") or "").strip(),
    }


# Checks that operate on child Work Packages — eligible for order-type scoping.
_WP_CHECKS = {
    "wp_unrecognized", "wp_duplicate", "wp_missing_standard",
    "wp_cross_project", "wp_skipped_anchoring",
}


def _order_prefix(fields: dict, field_id: str = "customfield_13905") -> str:
    """Leading order-type code (DS/QS/PT/PR/DMR) from the Order Type field."""
    val = _field_str(fields, field_id)
    if not val:
        return ""
    m = re.match(r"\s*([A-Za-z]+)", val)
    return m.group(1).upper() if m else ""


def _looks_deployed(fields: dict, wps: list[dict] | None) -> bool:
    """A container is 'deployed' once its NPI template has produced child WPs.
    Backlog containers and those with no child WPs have not been deployed yet."""
    status = ((fields.get("status") or {}).get("name") or "").strip()
    return bool(wps) and status.lower() != "backlog"


def run_yaml_rules(
    fields: dict,
    rendered_description: str,
    rules: list[dict],
    children: list[dict] | None = None,
    container_key: str = "",
    is_manual: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []

    # Normalise child Work Packages once. None means "could not fetch" → wp_*
    # rules are skipped so we never raise false positives on missing data.
    wps = [_wp_info(c) for c in children] if children is not None else None
    order_prefix = _order_prefix(fields)

    for rule in rules:
        check = rule.get("check", "")
        sev_str = rule.get("severity", "WARNING").upper()
        severity = Severity.ERROR if sev_str == "ERROR" else Severity.WARNING
        message = rule.get("message", "")
        fix_hint = rule.get("fix_hint", "")

        # Order-type scoping: a wp_* rule can opt out of whole order types
        # (e.g. DMR, which does not follow the standard WP design).
        if check in _WP_CHECKS:
            skip_types = {str(s).strip().upper() for s in rule.get("skip_order_types", [])}
            if order_prefix and order_prefix in skip_types:
                continue

        if check == "description_count":
            keyword = rule.get("keyword", "")
            threshold = int(rule.get("threshold", 1))
            count = (rendered_description or "").lower().count(keyword.lower())
            if count > threshold:
                findings.append(Finding(
                    severity, "YAML Rule",
                    message.format(count=count),
                    fix_hint,
                ))

        elif check == "description_field_empty":
            label = rule.get("field_label", "")
            if _desc_field_empty_html(rendered_description, label):
                findings.append(Finding(severity, "YAML Rule", message, fix_hint))

        elif check == "jira_field_missing":
            field_id = rule.get("field_id", "")
            val = fields.get(field_id)
            if val is None or (isinstance(val, str) and not val.strip()):
                findings.append(Finding(severity, "YAML Rule", message, fix_hint))

        elif check == "jira_field_value_expected":
            # Flag when a field is set to something other than the expected value.
            # Emptiness is only flagged when flag_if_empty is true.
            field_id = rule.get("field_id", "")
            expected = str(rule.get("expected_value", "")).strip()
            val = _field_str(fields, field_id)
            if not val:
                if rule.get("flag_if_empty", False):
                    empty_msg = rule.get("empty_message") or message.format(value="(empty)")
                    findings.append(Finding(severity, "YAML Rule", empty_msg, fix_hint))
            elif expected.lower() not in val.lower():
                findings.append(Finding(
                    severity, "YAML Rule", message.format(value=val), fix_hint))

        elif check == "wp_unrecognized" and wps is not None:
            standard = {s.strip().lower() for s in rule.get("standard_wps", [])}
            for wp in wps:
                if wp["summary"] and wp["summary"].lower() not in standard:
                    findings.append(Finding(
                        severity, "YAML Rule",
                        message.format(wp_key=wp["key"], wp_name=wp["summary"]),
                        fix_hint,
                    ))

        elif check == "wp_duplicate" and wps is not None:
            standard = {s.strip().lower() for s in rule.get("standard_wps", [])}
            grouped: dict[str, list[dict]] = {}
            for wp in wps:
                name_l = wp["summary"].lower()
                if name_l in standard:
                    grouped.setdefault(name_l, []).append(wp)
            for group in grouped.values():
                if len(group) > 1:
                    a, b = group[0], group[1]
                    findings.append(Finding(
                        severity, "YAML Rule",
                        message.format(
                            wp_name=a["summary"],
                            wp_key_1=a["key"], date1=_fmt_date(a["created"]),
                            wp_key_2=b["key"], date2=_fmt_date(b["created"]),
                        ),
                        fix_hint,
                    ))

        elif check == "wp_missing_standard" and wps is not None:
            pt_field = rule.get("product_type_field", "customfield_13904")
            pt_value = str(rule.get("product_type_value", "")).strip().lower()
            product_type = _field_str(fields, pt_field).lower()
            # In batch mode, skip containers whose template is not deployed yet
            # (Backlog / no child WPs). A manual check_container run audits them.
            deployed_ok = is_manual or not rule.get("require_deployed", False) \
                or _looks_deployed(fields, wps)
            # Only check containers of the configured product type.
            if (not pt_value or pt_value in product_type) and deployed_ok:
                # Base required set + any order-type-specific extras (e.g. QM P+L
                # only for PR).
                required = list(rule.get("standard_wps", []))
                order_extras = (rule.get("order_type_wps") or {}).get(order_prefix, [])
                required += list(order_extras)
                present = {wp["summary"].lower() for wp in wps}
                missing = [s for s in required if s.strip().lower() not in present]
                if missing:
                    findings.append(Finding(
                        severity, "YAML Rule",
                        message.format(list=", ".join(missing)),
                        fix_hint,
                    ))

        elif check == "wp_cross_project" and wps is not None:
            wc_project = container_key.split("-")[0] if container_key else ""
            for wp in wps:
                wp_project = wp["key"].split("-")[0] if wp["key"] else ""
                if wp_project and wc_project and wp_project != wc_project:
                    findings.append(Finding(
                        severity, "YAML Rule",
                        message.format(
                            wp_key=wp["key"],
                            wp_project=wp_project,
                            wc_project=wc_project,
                        ),
                        fix_hint,
                    ))

        elif check == "wp_skipped_anchoring" and wps is not None:
            skipped_names = {
                s.strip().lower() for s in rule.get("skipped_statuses", [])
            }

            def _is_skipped(wp: dict) -> bool:
                return (
                    wp["status"].lower() in skipped_names
                    or wp["resolution"].lower() in skipped_names
                )

            active = [wp for wp in wps if wp["created"] and not _is_skipped(wp)]
            skipped = [wp for wp in wps if wp["created"] and _is_skipped(wp)]
            if active and skipped:
                earliest_active = min(active, key=lambda w: w["created"])
                for wp in skipped:
                    if wp["created"] < earliest_active["created"]:
                        findings.append(Finding(
                            severity, "YAML Rule",
                            message.format(
                                wp_key=wp["key"],
                                date=_fmt_date(wp["created"]),
                                active_date=_fmt_date(earliest_active["created"]),
                            ),
                            fix_hint,
                        ))

    return findings


# ---------------------------------------------------------------------------
# Confluence page parsing
# ---------------------------------------------------------------------------

def _cell_text(cell) -> str:
    """Extract plain text from a td, converting <br/> to newlines."""
    for br in cell.find_all("br"):
        br.replace_with("\n")
    return cell.get_text()


def _extract_key_from_cell(cell) -> str:
    """Extract a JIRA key (e.g. NPIOTHER-123) from a table cell."""
    a = cell.find("a")
    if a:
        text = a.get_text(strip=True)
        if _looks_like_key(text):
            return text
    text = cell.get_text(strip=True)
    if _looks_like_key(text):
        return text
    return ""


def _looks_like_key(text: str) -> bool:
    import re
    return bool(re.match(r'^[A-Z]+-\d+$', text.strip()))


def parse_existing_page(html_body: str) -> tuple[dict[str, str], list[dict]]:
    """
    Parse the audit Confluence page.

    Returns:
        draft_comments — {key: draft_text} from issues table (column 5)
        ignore_rows    — [{key, summary, ignored_on, reason}] from ignore table
    """
    draft_comments: dict[str, str] = {}
    ignore_rows: list[dict] = []

    if not html_body:
        return draft_comments, ignore_rows

    soup = BeautifulSoup(html_body, "html.parser")
    tables = soup.find_all("table")

    if len(tables) >= 1:
        rows = tables[0].find_all("tr")[1:]
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 5:
                key = _extract_key_from_cell(cells[0])
                if key:
                    draft_comments[key] = _cell_text(cells[4]).strip()

    if len(tables) >= 2:
        rows = tables[1].find_all("tr")[1:]
        for row in rows:
            cells = row.find_all(["td", "th"])
            if len(cells) >= 4:
                key = _extract_key_from_cell(cells[0])
                if key:
                    ignore_rows.append({
                        "key": key,
                        "summary": cells[1].get_text(strip=True),
                        "ignored_on": cells[2].get_text(strip=True),
                        "reason": cells[3].get_text(strip=True),
                    })

    return draft_comments, ignore_rows


# ---------------------------------------------------------------------------
# Draft comment
# ---------------------------------------------------------------------------

def build_draft_comment(key: str, reporter_name: str, findings: list[Finding]) -> str:
    lines = []
    for f in findings:
        if f.severity == Severity.ERROR:
            lines.append(f"❌ {f.message}")
        elif f.severity == Severity.WARNING:
            lines.append(f"⚠️ {f.message}")
    findings_text = "\n".join(lines)
    name = reporter_name or "requestor"
    return (
        f"Hi [~{name}],\n\n"
        f"We noticed the following on your NPI container *{key}* during our routine audit:\n\n"
        f"{findings_text}\n\n"
        f"Please review and update the container, or let us know if you need help.\n\n"
        f"{GUARD_MARKER}"
    )


def _already_posted(issue: dict) -> bool:
    comments = (
        ((issue.get("fields") or {}).get("comment") or {}).get("comments") or []
    )
    return any(GUARD_MARKER in (c.get("body") or "") for c in comments)


# ---------------------------------------------------------------------------
# Confluence HTML helpers
# ---------------------------------------------------------------------------

def _esc(v: Any) -> str:
    if v is None or v == "":
        return "-"
    return html.escape(str(v))


def _th(text: str) -> str:
    return f"<th>{html.escape(text)}</th>"


def _td(content: str) -> str:
    return f"<td>{content}</td>"


def _jira_link(base_url: str, key: str) -> str:
    href = f"{base_url.rstrip('/')}/browse/{key}"
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(key)}</a>'


def _issues_found_html(findings: list[Finding]) -> str:
    parts = []
    for f in findings:
        if f.severity == Severity.ERROR:
            parts.append(f"❌ {html.escape(f.message)}")
        elif f.severity == Severity.WARNING:
            parts.append(f"⚠️ {html.escape(f.message)}")
    return "<br/>".join(parts)


def _draft_to_html(draft: str) -> str:
    """Convert plain-text draft (with \n) to HTML for a Confluence cell."""
    if not draft:
        return ""
    lines = html.escape(draft).split("\n")
    return "<br/>".join(lines)


def build_page_html(
    now_str: str,
    total_checked: int,
    error_count: int,
    warning_count: int,
    clean_count: int,
    issue_rows: list[dict],
    ignore_rows: list[dict],
    jira_base_url: str,
) -> str:
    header = (
        f"<p><strong>Last run:</strong> {html.escape(now_str)} | "
        f"{total_checked} containers checked | "
        f"❌ {error_count} errors | "
        f"⚠️ {warning_count} warnings | "
        f"✅ {clean_count} clean</p>"
    )

    # Issues table — always render as a table so ignore.py can parse it
    issues_header = (
        "<tr>"
        + _th("Container") + _th("Summary") + _th("Status")
        + _th("Issues Found") + _th("Draft Comment")
        + "</tr>"
    )
    issue_body_rows = []
    for row in issue_rows:
        key = row["key"]
        cells = (
            _td(_jira_link(jira_base_url, key))
            + _td(_esc(row.get("summary")))
            + _td(_esc(row.get("status")))
            + _td(row.get("issues_html", ""))
            + _td(row.get("draft_html", ""))
        )
        issue_body_rows.append(f"<tr>{cells}</tr>")

    issues_table = (
        "<h2>Containers with Issues</h2>"
        "<table><tbody>"
        + issues_header
        + "".join(issue_body_rows)
        + "</tbody></table>"
    )

    # Ignore table — always render as a table so ignore.py can append rows
    ignore_header = (
        "<tr>"
        + _th("Container") + _th("Summary") + _th("Ignored On") + _th("Reason")
        + "</tr>"
    )
    ignore_body_rows = []
    for row in ignore_rows:
        key = row.get("key", "")
        cells = (
            _td(_jira_link(jira_base_url, key))
            + _td(_esc(row.get("summary")))
            + _td(_esc(row.get("ignored_on")))
            + _td(_esc(row.get("reason")))
        )
        ignore_body_rows.append(f"<tr>{cells}</tr>")

    ignore_table = (
        "<h2>Ignored Containers</h2>"
        "<table><tbody>"
        + ignore_header
        + "".join(ignore_body_rows)
        + "</tbody></table>"
    )

    return header + issues_table + ignore_table


# ---------------------------------------------------------------------------
# scan subcommand
# ---------------------------------------------------------------------------

def run_scan(mode: str, dry_run: bool) -> int:
    logger = get_logger("container_template_audit.batch")
    config = load_config(mode_override=mode)
    logger.info("scan: %s mode (dry_run=%s)", config.mode, dry_run)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    confluence = ConfluenceClient(config, mock_data_dir=MOCK_DIR)
    rules = load_audit_rules()
    logger.info("Loaded %d YAML rule(s) from %s", len(rules), RULES_PATH)
    logger.info(
        "Audit depends on %d container fields (incl. customfield_12401 "
        "Cloned-from-Template)", len(WC_FIELDS),
    )

    # Do any enabled rules need child Work Packages? Skip the relation() fetch
    # entirely when none do.
    wp_checks = {
        "wp_unrecognized", "wp_duplicate", "wp_missing_standard",
        "wp_cross_project", "wp_skipped_anchoring",
    }
    need_children = any(r.get("check") in wp_checks for r in rules)

    # Read existing page for ignore list and preserved drafts
    existing_html = ""
    try:
        existing_html = confluence.get_page_html(AUDIT_PAGE_ID)
    except Exception as exc:
        logger.warning("Could not read existing Confluence page %s: %s", AUDIT_PAGE_ID, exc)

    existing_draft_comments, ignore_rows = parse_existing_page(existing_html)
    ignored_keys = {row["key"] for row in ignore_rows}
    logger.info(
        "Existing page: %d staged draft(s), %d ignored container(s)",
        len(existing_draft_comments), len(ignored_keys),
    )

    # Fetch active containers
    if config.is_mock:
        container_keys = sorted(
            p.stem.removeprefix("issue_")
            for p in MOCK_DIR.glob("issue_*.json")
        )
        logger.info("Mock mode: found %d issue files in %s", len(container_keys), MOCK_DIR)
    else:
        issues = jira.search_all(ACTIVE_CONTAINERS_JQL, fields=["summary", "status"])
        container_keys = [i.get("key") for i in issues if i.get("key")]
        logger.info("JIRA: %d active SG SMT PCBA container(s)", len(container_keys))

    issue_rows: list[dict] = []
    total_checked = 0
    containers_with_errors = 0
    containers_with_warnings = 0
    clean_count = 0

    for key in container_keys:
        if key in ignored_keys:
            logger.debug("%s: in ignore list — skipping", key)
            continue

        try:
            issue = jira.get_issue(key, expand="renderedFields")
        except Exception as exc:
            logger.warning("%s: could not fetch issue: %s", key, exc)
            continue

        fields = issue.get("fields") or {}
        rendered_html = (issue.get("renderedFields") or {}).get("description") or ""
        summary = (fields.get("summary") or "").strip()
        status = (fields.get("status") or {}).get("name") or "Unknown"
        reporter = fields.get("reporter") or {}
        reporter_name = (reporter.get("name") or "").strip()

        # Standard audit via NPIAuditor (uses pre-fetched data via _CachedJiraClient)
        try:
            report: AuditReport = NPIAuditor(_CachedJiraClient(issue)).audit(key)
        except Exception as exc:
            logger.warning("%s: audit error: %s", key, exc)
            continue

        # Fetch child Work Packages for the wp_* rules. On any failure we pass
        # children=None so those rules skip rather than raise false positives.
        children: list[dict] | None = None
        if need_children:
            try:
                children = jira.get_children(key, fields=WP_FIELDS)
            except Exception as exc:
                logger.warning("%s: could not fetch child WPs: %s", key, exc)
                children = None

        # YAML rule checks on top
        yaml_findings = run_yaml_rules(
            fields, rendered_html, rules,
            children=children, container_key=key,
        )
        all_findings = report.findings + yaml_findings

        problem_findings = [
            f for f in all_findings
            if f.severity in (Severity.ERROR, Severity.WARNING)
        ]

        total_checked += 1

        if not problem_findings:
            clean_count += 1
            continue

        has_errors = any(f.severity == Severity.ERROR for f in problem_findings)
        if has_errors:
            containers_with_errors += 1
        else:
            containers_with_warnings += 1

        # Determine draft comment text
        existing_draft = existing_draft_comments.get(key, "")
        if existing_draft and existing_draft != "Already posted":
            draft_text = existing_draft  # preserve human edits
        elif _already_posted(issue):
            draft_text = "Already posted"
        else:
            draft_text = build_draft_comment(key, reporter_name, problem_findings)

        issue_rows.append({
            "key": key,
            "summary": summary,
            "status": status,
            "issues_html": _issues_found_html(problem_findings),
            "draft_html": _draft_to_html(draft_text),
            "findings": problem_findings,
        })

        logger.info("%s: %d issue(s) (%s)", key, len(problem_findings),
                    "errors" if has_errors else "warnings only")

    # Print summary
    print(f"\nScan complete:")
    print(f"  Checked   : {total_checked}")
    print(f"  Errors    : {containers_with_errors} container(s) with errors")
    print(f"  Warnings  : {containers_with_warnings} container(s) with warnings only")
    print(f"  Clean     : {clean_count}")
    print(f"  In issues table: {len(issue_rows)}")

    if dry_run:
        print("\n[dry-run] Detailed findings (nothing published):")
        if not issue_rows:
            print("  (no containers with issues)")
        for row in issue_rows:
            print(f"\n  {row['key']}  [{row['status']}]  {row['summary']}")
            for f in row["findings"]:
                print(f"      {f.severity.name:7s}  {f.message}")
        return 0

    if config.is_mock:
        print("[mock] Skipping Confluence publish.")
        return 0

    now_str = datetime.now().strftime("%d-%b-%Y %H:%M")
    page_html = build_page_html(
        now_str=now_str,
        total_checked=total_checked,
        error_count=containers_with_errors,
        warning_count=containers_with_warnings,
        clean_count=clean_count,
        issue_rows=issue_rows,
        ignore_rows=ignore_rows,
        jira_base_url=config.jira_base_url,
    )

    current_page = confluence.get_page(AUDIT_PAGE_ID)
    title = (current_page.get("title") or "NPI Container Audit Dashboard")
    result = confluence.update_page(AUDIT_PAGE_ID, title=title, html_body=page_html)
    version = (result.get("version") or {}).get("number", "?")
    logger.info("Published v%s to Confluence page %s", version, AUDIT_PAGE_ID)
    page_url = (
        f"{config.confluence_base_url.rstrip('/')}"
        f"/pages/viewpage.action?pageId={AUDIT_PAGE_ID}"
    )
    print(f"Published v{version} → {page_url}")
    return 0


# ---------------------------------------------------------------------------
# comment subcommand
# ---------------------------------------------------------------------------

def run_comment(mode: str, keys: list[str], dry_run: bool) -> int:
    logger = get_logger("container_template_audit.batch")
    config = load_config(mode_override=mode)
    logger.info("comment: %s mode (keys=%s, dry_run=%s)", config.mode, keys, dry_run)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    confluence = ConfluenceClient(config, mock_data_dir=MOCK_DIR)

    # Load staged drafts from Confluence
    try:
        existing_html = confluence.get_page_html(AUDIT_PAGE_ID)
    except Exception as exc:
        print(f"ERROR: Could not read Confluence page {AUDIT_PAGE_ID}: {exc}")
        return 1

    draft_comments, _ = parse_existing_page(existing_html)

    posted = 0
    skipped = 0

    for key in keys:
        draft = draft_comments.get(key, "")

        if not draft:
            print(f"{key}: no staged draft found — run `scan` first")
            skipped += 1
            continue

        if draft.strip() == "Already posted":
            print(f"{key}: already posted (shown as 'Already posted' on Confluence)")
            skipped += 1
            continue

        # Check JIRA for duplicate guard
        try:
            issue = jira.get_issue(key)
        except Exception as exc:
            print(f"{key}: could not fetch issue from JIRA: {exc}")
            skipped += 1
            continue

        comments = (
            ((issue.get("fields") or {}).get("comment") or {}).get("comments") or []
        )
        if any(GUARD_MARKER in (c.get("body") or "") for c in comments):
            print(f"{key}: {GUARD_MARKER} already in JIRA comments — skipping")
            skipped += 1
            continue

        if dry_run:
            print(f"--- dry-run comment for {key} ---")
            print(draft)
            print()
            continue

        if config.is_mock:
            print(f"{key}: [mock] would post audit comment")
            continue

        try:
            jira.add_comment(key, draft)
        except Exception as exc:
            print(f"{key}: failed to post comment: {exc}")
            skipped += 1
            continue

        logger.info("%s: posted audit comment", key)
        print(f"{key}: posted")
        posted += 1

    print(f"\nRequested: {len(keys)}   Posted: {posted}   Skipped: {skipped}")
    return 0


# ---------------------------------------------------------------------------
# check subcommand — manual one-by-one container audit (read-only)
# ---------------------------------------------------------------------------

def run_check(mode: str, keys: list[str]) -> int:
    """
    Audit one or more specific containers and print the detailed findings.
    Read-only: never writes to Confluence or JIRA. Runs in manual mode, so
    require_deployed rules audit not-yet-deployed containers too.
    """
    logger = get_logger("container_template_audit.batch")
    config = load_config(mode_override=mode)
    logger.info("check: %s mode (keys=%s)", config.mode, keys)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    rules = load_audit_rules()
    need_children = any(r.get("check") in _WP_CHECKS for r in rules)

    print(f"\n=== Manual container check ({config.mode}) — {len(keys)} container(s) ===")

    for key in keys:
        try:
            issue = jira.get_issue(key, expand="renderedFields")
        except Exception as exc:
            print(f"\n  {key}: ERROR fetching issue: {exc}")
            continue

        fields = issue.get("fields") or {}
        rendered_html = (issue.get("renderedFields") or {}).get("description") or ""
        summary = (fields.get("summary") or "").strip()
        status = (fields.get("status") or {}).get("name") or "Unknown"

        try:
            report = NPIAuditor(_CachedJiraClient(issue)).audit(key)
        except Exception as exc:
            print(f"\n  {key}: audit error: {exc}")
            continue

        children: list[dict] | None = None
        wp_note = ""
        if need_children:
            try:
                children = jira.get_children(key, fields=WP_FIELDS)
            except Exception as exc:
                logger.warning("%s: could not fetch child WPs: %s", key, exc)
                wp_note = f"  (could not fetch child WPs: {exc})"
                children = None

        yaml_findings = run_yaml_rules(
            fields, rendered_html, rules,
            children=children, container_key=key, is_manual=True,
        )
        problems = [
            f for f in (report.findings + yaml_findings)
            if f.severity in (Severity.ERROR, Severity.WARNING)
        ]

        print(f"\n  {key}  [{status}]  {summary}")
        if wp_note:
            print(wp_note)
        if not problems:
            print("      OK — no issues found.")
        for f in problems:
            print(f"      {f.severity.name:7s}  {f.message}")

    print()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_mode_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mock", action="store_const", const="mock", dest="mode",
        help="Use mock_data/ (default — safe for VPS)",
    )
    group.add_argument(
        "--live", action="store_const", const="live", dest="mode",
        help="Hit live JIRA + Confluence (company laptop only)",
    )
    parser.set_defaults(mode="mock")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print results but do not write to Confluence or post JIRA comments.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="batch.py",
        description="NPI container template audit — batch system.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Audit all containers and publish to Confluence.")
    _add_mode_args(scan_p)

    comment_p = sub.add_parser("comment", help="Post staged draft comments to JIRA.")
    _add_mode_args(comment_p)
    comment_p.add_argument(
        "--keys", nargs="+", required=True, metavar="KEY",
        help="Container keys to comment on (e.g. --keys NPIOTHER-123 POSX-456)",
    )

    check_p = sub.add_parser(
        "check",
        help="Manually audit one or more specific containers (read-only).",
    )
    check_group = check_p.add_mutually_exclusive_group()
    check_group.add_argument(
        "--mock", action="store_const", const="mock", dest="mode",
        help="Use mock_data/ (default — safe for VPS)",
    )
    check_group.add_argument(
        "--live", action="store_const", const="live", dest="mode",
        help="Hit live JIRA, read-only (company laptop)",
    )
    check_p.set_defaults(mode="mock")
    check_p.add_argument(
        "keys", nargs="+", metavar="KEY",
        help="Container key(s) to audit, e.g. NPIOTHER-5124 POSX-7007",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "scan":
        return run_scan(mode=args.mode, dry_run=args.dry_run)
    if args.command == "comment":
        return run_comment(mode=args.mode, keys=args.keys, dry_run=args.dry_run)
    if args.command == "check":
        return run_check(mode=args.mode, keys=args.keys)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
