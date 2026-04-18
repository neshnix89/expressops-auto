"""
to_status_check — Phase A: JIRA extraction.

Pulls active NPI Work Containers from JIRA and extracts the Transfer Order
(TO) number from the latest "TO: <digits>" comment on each one.

Work Containers span many JIRA project keys (USRE, POSX, LCUSAMB, NPIOTHER,
SILED2, …) — there is no single project to filter on. We scope by issue type
plus the Order Type custom field (customfield_13905) to match the NPI
container population.

Phase B (M3 status lookup) is deferred until discovery confirms the table
and columns — see TASK.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.jira_client import JiraClient
from core.logger import get_logger

from tasks.to_status_check.logic import build_container_row, format_table, summarize

TASK_NAME = "to_status_check"
# Scope by Order Type (customfield_13905) rather than project — containers
# live across many project keys. "is not EMPTY" matches any container that has
# been classified with an Order Type, which in practice is every NPI container.
ACTIVE_CONTAINERS_JQL = (
    'issuetype = "Work Container" AND "Order Type" is not EMPTY AND status != Closed'
)
MOCK_DIR = TASK_DIR / "mock_data"


def fetch_containers_with_comments(jira: JiraClient, jql: str, logger) -> list[dict]:
    """
    Search active containers, then fetch each one with its comments.

    The search is lightweight (key only); the per-issue GET pulls the
    comment field. This matches the JIRA REST pattern documented in
    WORKLOG.md.

    In mock mode the search result typically lists far more containers
    than capture.py has snapshotted (capture saves ~10 sample issue files
    out of ~200 hits), so we skip any key that has no matching
    issue_<KEY>.json on disk.
    """
    search_result = jira.search(jql, fields=["summary", "status"], max_results=200)
    issues = search_result.get("issues", []) or []

    enriched: list[dict] = []
    skipped = 0
    for issue in issues:
        key = issue.get("key")
        if not key:
            continue
        if jira.config.is_mock and not (MOCK_DIR / f"issue_{key}.json").exists():
            logger.debug("Skipping %s — no mock file", key)
            skipped += 1
            continue
        full = jira.get_issue(key, expand="renderedFields")
        enriched.append(full)
    if skipped:
        logger.info("Skipped %d container(s) with no mock data", skipped)
    return enriched


def run(mode: str) -> int:
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    logger.info("Running %s in %s mode", TASK_NAME, config.mode)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    issues = fetch_containers_with_comments(jira, ACTIVE_CONTAINERS_JQL, logger)
    logger.info("Fetched %d active Work Containers", len(issues))

    rows = [build_container_row(issue) for issue in issues]
    rows.sort(key=lambda r: r["key"])

    summary = summarize(rows)
    print(format_table(rows))
    print()
    print(f"Total: {summary['total']}   With TO: {summary['with_to']}   Without TO: {summary['without_to']}")

    logger.info(
        "Summary: total=%d with_to=%d without_to=%d",
        summary["total"], summary["with_to"], summary["without_to"],
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check JIRA Work Container TO numbers (Phase A)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_const", const="mock", dest="mode",
                       help="Read from tasks/to_status_check/mock_data/ (default)")
    group.add_argument("--live", action="store_const", const="live", dest="mode",
                       help="Hit live JIRA (company laptop only)")
    parser.set_defaults(mode="mock")
    args = parser.parse_args()
    return run(args.mode)


if __name__ == "__main__":
    sys.exit(main())
