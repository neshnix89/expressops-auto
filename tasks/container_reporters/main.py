"""
container_reporters — who reported each Work Container, and when it was resolved.

A read-only JIRA pull: the same container population the KPI overlay draws its
pills for (issuetype "Work Container", Product Type "SMT PCBA", NPI Location
Singapore or Trutnov), exported as Reporter + Resolved date to CSV.

Writes nothing back to JIRA and publishes nothing to Confluence.

Usage:
    python -m tasks.container_reporters.main --mock                  # VPS testing
    python -m tasks.container_reporters.main --live                  # company laptop
    python -m tasks.container_reporters.main --live --since 2026-01-01
    python -m tasks.container_reporters.main --live --scope all
    python -m tasks.container_reporters.main --live --show-jql --verbose

Scopes:
    resolved  (default) containers that have a resolution — these have a resolved date
    open                the KPI overlay's exact set (resolution is EMPTY)
    all                 both; resolved date is blank for the open ones

Output:
    outputs/container_reporters.csv   — one row per container
    logs/container_reporters.log      — audit log
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger
from tasks.container_reporters.logic import (
    CSV_COLUMNS, FIELDS, SCOPES,
    build_jql, build_rows, check_date, count_by, filter_rows,
)

TASK_NAME = "container_reporters"
MOCK_DIR = TASK_DIR / "mock_data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CSV_FILE = OUTPUT_DIR / "container_reporters.csv"


def fetch_containers(jira: JiraClient, jql: str, logger) -> list[dict]:
    """Containers from live JIRA, or from mock_data/containers.json."""
    if jira.config.is_mock:
        path = MOCK_DIR / "containers.json"
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("issues", [])
    return jira.search_all(jql, fields=FIELDS)


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel on the company laptop opens a plain utf-8 CSV as mojibake.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def run(mode: str, scope: str = "resolved", since: str | None = None,
        until: str | None = None, show_jql: bool = False,
        verbose: bool = False) -> int:
    config = load_config(mode_override=mode)
    logger = get_logger(TASK_NAME, log_dir=config.log_dir,
                        level="DEBUG" if verbose else "INFO")

    try:
        since = check_date(since, "--since")
        until = check_date(until, "--until")
        jql = build_jql(scope, since=since, until=until)
    except ValueError as exc:
        raise FriendlyError(str(exc)) from exc

    if scope == "open" and (since or until):
        logger.warning("--since/--until ignored: open containers have no resolved date")

    logger.info("=" * 60)
    logger.info("Container reporter export starting (%s mode, scope=%s)",
                config.mode, scope)
    if since or until:
        logger.info("  Resolved between %s and %s", since or "(any)", until or "(any)")
    if show_jql or verbose:
        logger.info("  JQL: %s", jql)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    issues = fetch_containers(jira, jql, logger)
    logger.info("  JIRA returned %d container(s)", len(issues))

    rows = build_rows(issues)
    if config.is_mock:
        # The fixture is the full population; the scope/date window is applied
        # here so --mock previews the same shape a live run would produce.
        rows = filter_rows(rows, scope, since=since, until=until)
        logger.info("  %d after applying scope/date filter to the fixture", len(rows))

    missing = [r["issueKey"] for r in rows if not r["reporter"]]
    if missing:
        logger.warning("  %d container(s) have no Reporter: %s",
                       len(missing), ", ".join(missing[:10]))

    write_csv(rows, CSV_FILE)
    logger.info("CSV written: %s (%d row(s))", CSV_FILE, len(rows))

    # ─── Console summary ───
    by_reporter = count_by(rows, "reporter")
    logger.info("  By reporter (%d distinct):", len(by_reporter))
    for name, count in by_reporter[:15]:
        logger.info("    %-30s %d", name, count)
    if len(by_reporter) > 15:
        logger.info("    ... and %d more", len(by_reporter) - 15)

    logger.info("  By location: %s", ", ".join(
        f"{loc}={n}" for loc, n in count_by(rows, "location")) or "none")

    resolved = [r["resolvedDate"] for r in rows if r["resolvedDate"]]
    if resolved:
        logger.info("  Resolved dates span %s to %s (%d resolved, %d still open)",
                    min(resolved), max(resolved), len(resolved),
                    len(rows) - len(resolved))
    else:
        logger.info("  No resolved containers in this result")

    logger.info("Done. %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Reporter + Resolved date for NPI Work Containers "
                    "(same filter as the KPI overlay)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_const", const="mock", dest="mode",
                       help="Read from mock_data/ (default)")
    group.add_argument("--live", action="store_const", const="live", dest="mode",
                       help="Hit live JIRA (company laptop only)")
    parser.add_argument("--scope", choices=SCOPES, default="resolved",
                        help="Which containers to export (default: resolved)")
    parser.add_argument("--since", default=None,
                        help="Only containers resolved on/after this date (YYYY-MM-DD)")
    parser.add_argument("--until", default=None,
                        help="Only containers resolved on/before this date (YYYY-MM-DD)")
    parser.add_argument("--show-jql", action="store_true",
                        help="Log the JQL that will be run")
    parser.add_argument("--verbose", action="store_true", help="Debug-level logging")
    parser.set_defaults(mode="mock")
    args = parser.parse_args()
    try:
        return run(args.mode, scope=args.scope, since=args.since, until=args.until,
                   show_jql=args.show_jql, verbose=args.verbose)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
