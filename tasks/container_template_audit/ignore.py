"""
Move a container from the issues table to the ignore table on Confluence.

Directly manipulates the stored Confluence HTML — removes the container's row
from Table 1 (issues) and appends a new row to Table 2 (ignored). Does not
require a full re-scan.

Usage:
    python ignore.py NPIOTHER-123
    python ignore.py NPIOTHER-123 --reason "Container already in SMT Build phase"
    python ignore.py NPIOTHER-123 --live --reason "Scope change — ignore until Phase 2"
"""
from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependency: pip install beautifulsoup4")
    sys.exit(1)

from core.config_loader import load_config
from core.confluence import ConfluenceClient
from core.jira_client import JiraClient
from core.logger import get_logger

from tasks.container_template_audit.batch import (
    AUDIT_PAGE_ID,
    MOCK_DIR,
    _extract_key_from_cell,
)

TASK_NAME = "container_template_audit.ignore"


def _remove_from_issues_table(soup: BeautifulSoup, key: str) -> bool:
    """Remove the row for `key` from the first table. Returns True if found."""
    tables = soup.find_all("table")
    if not tables:
        return False
    rows = tables[0].find_all("tr")[1:]
    for row in rows:
        cells = row.find_all(["td", "th"])
        if cells and _extract_key_from_cell(cells[0]) == key:
            row.decompose()
            return True
    return False


def _add_to_ignore_table(
    soup: BeautifulSoup,
    key: str,
    summary: str,
    ignored_on: str,
    reason: str,
    jira_base_url: str,
) -> None:
    """Append a new row to the second table (ignored containers)."""
    tables = soup.find_all("table")
    if len(tables) < 2:
        return
    tbody = tables[1].find("tbody") or tables[1]
    href = f"{jira_base_url.rstrip('/')}/browse/{key}"
    new_row_html = (
        "<tr>"
        f'<td><a href="{html.escape(href, quote=True)}">{html.escape(key)}</a></td>'
        f"<td>{html.escape(summary)}</td>"
        f"<td>{html.escape(ignored_on)}</td>"
        f"<td>{html.escape(reason)}</td>"
        "</tr>"
    )
    new_row = BeautifulSoup(new_row_html, "html.parser")
    tbody.append(new_row)


def _already_ignored(soup: BeautifulSoup, key: str) -> bool:
    """Return True if key is already in the ignore table."""
    tables = soup.find_all("table")
    if len(tables) < 2:
        return False
    rows = tables[1].find_all("tr")[1:]
    for row in rows:
        cells = row.find_all(["td", "th"])
        if cells and _extract_key_from_cell(cells[0]) == key:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ignore.py",
        description="Move a container to the ignore list on the audit Confluence page.",
    )
    parser.add_argument("key", help="JIRA container key, e.g. NPIOTHER-123")
    parser.add_argument(
        "--reason", default="Manually ignored",
        help="Reason for ignoring (shown in Confluence ignore table).",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--mock", action="store_const", const="mock", dest="mode",
        help="Use mock data (default)",
    )
    mode_group.add_argument(
        "--live", action="store_const", const="live", dest="mode",
        help="Hit live Confluence + JIRA (company laptop only)",
    )
    parser.set_defaults(mode="mock")
    args = parser.parse_args()

    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=args.mode)
    logger.info(
        "ignore: %s mode (key=%s, reason=%r)",
        config.mode, args.key, args.reason,
    )

    confluence = ConfluenceClient(config, mock_data_dir=MOCK_DIR)
    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    # 1. Fetch current page
    try:
        current_page = confluence.get_page(AUDIT_PAGE_ID)
    except Exception as exc:
        print(f"ERROR: Could not read Confluence page {AUDIT_PAGE_ID}: {exc}")
        return 1

    title = current_page.get("title") or "NPI Container Audit Dashboard"
    existing_html = (current_page.get("body") or {}).get("storage", {}).get("value", "") or ""

    if not existing_html:
        print(
            f"Confluence page {AUDIT_PAGE_ID} appears empty. "
            "Run `batch.py scan --live` first."
        )
        return 1

    soup = BeautifulSoup(existing_html, "html.parser")

    # 2. Check if already ignored
    if _already_ignored(soup, args.key):
        print(f"{args.key} is already in the ignore list.")
        return 0

    # 3. Fetch summary from JIRA (for the ignore row)
    summary = ""
    try:
        issue = jira.get_issue(args.key)
        summary = ((issue.get("fields") or {}).get("summary") or "").strip()
    except Exception as exc:
        logger.warning("Could not fetch %s from JIRA: %s", args.key, exc)

    # 4. Remove from issues table (may not be there if container was clean)
    removed = _remove_from_issues_table(soup, args.key)
    if removed:
        logger.info("%s: removed from issues table", args.key)

    # 5. Add to ignore table
    today_str = datetime.now().strftime("%d-%b-%Y")
    _add_to_ignore_table(
        soup=soup,
        key=args.key,
        summary=summary,
        ignored_on=today_str,
        reason=args.reason,
        jira_base_url=config.jira_base_url,
    )
    logger.info("%s: added to ignore table (reason: %s)", args.key, args.reason)

    if config.is_mock:
        print(f"{args.key}: [mock] would update Confluence page.")
        return 0

    # 6. Update Confluence page
    new_html = str(soup)
    try:
        result = confluence.update_page(AUDIT_PAGE_ID, title=title, html_body=new_html)
    except Exception as exc:
        print(f"ERROR: Could not update Confluence page {AUDIT_PAGE_ID}: {exc}")
        return 1

    version = (result.get("version") or {}).get("number", "?")
    logger.info("Updated Confluence page %s to v%s", AUDIT_PAGE_ID, version)
    print(f"{args.key}: moved to ignore list on Confluence (v{version}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
