"""
container_reporters — who reported each Work Container, and when it was resolved.

A read-only JIRA pull of container-level issues, exported as Reporter +
Resolved date to CSV. The population comes from ``--source``: the NPI template
family (default), the Kanban board's saved filter, or the KPI overlay's
issue-type query.

Default run = containers cloned from the NPI templates, SMT PCBA, Singapore,
FULLY CLOSED (resolution set), resolved on/after 2025-01-01.

Containers resolved before 2025-01-01 are excluded by default (--all-dates
lifts that).

Writes nothing back to JIRA and publishes nothing to Confluence.

Usage:
    python -m tasks.container_reporters.main --mock                  # VPS testing
    python -m tasks.container_reporters.main --live                  # company laptop
    python -m tasks.container_reporters.main --live --since 2026-01-01
    python -m tasks.container_reporters.main --live --scope all
    python -m tasks.container_reporters.main --live --source board --scope all
    python -m tasks.container_reporters.main --live --source overlay --all-dates
    python -m tasks.container_reporters.main --live --show-jql --verbose

Sources (--source):
    template (default) the same NPI family the board draws from, without the
             board's open-status restriction: every work package cloned from
             the eight ITPL templates, resolved back to its container. The only
             source that can return a FULLY CLOSED container.
    board    saved filter 25423 verbatim — the Project Parents of the template
             clones that are Waiting / In Progress / Backlog. Matches the board,
             and so holds only containers with open work left.
    overlay  the plain issue-type query tasks/kpi_overlay/main.py runs
             (Work Container + SMT PCBA + Singapore/Trutnov)

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
    BOARD_FILTER_ID, BOARD_IS_OPEN_WORK_ONLY, CSV_COLUMNS, DEFAULT_SOURCE,
    FIELDS, SCOPES, SOURCES, TEMPLATE_KEYS, WC_ISSUE_TYPE,
    build_jql, build_rows, check_date, chunk, count_by, filter_rows,
    lineage_jql, non_containers, parents_jql,
)

TASK_NAME = "container_reporters"
MOCK_DIR = TASK_DIR / "mock_data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CSV_FILE = OUTPUT_DIR / "container_reporters.csv"

# Containers resolved before this are out of scope for the current reporting.
# --since overrides it; --all-dates removes the floor entirely.
DEFAULT_SINCE = "2025-01-01"


def load_mock_containers() -> list[dict]:
    with open(MOCK_DIR / "containers.json", "r", encoding="utf-8") as f:
        return json.load(f).get("issues", [])


def fetch_containers(jira: JiraClient, jql: str, logger) -> list[dict]:
    """Containers from one JQL (board / overlay sources)."""
    if jira.config.is_mock:
        return load_mock_containers()
    return jira.search_all(jql, fields=FIELDS)


def fetch_template_containers(jira: JiraClient, scope: str, since: str | None,
                              until: str | None, template_keys: tuple[str, ...],
                              logger) -> list[dict]:
    """Two-step fetch for the ``template`` source.

    1. every work package cloned from the NPI templates (filter 25423 without
       its open-status clause);
    2. those work packages' containers, batched, filtered to scope + dates.

    Two queries rather than one because nesting the lineage relation() inside
    the Project-Parent relation() would need a third level of quoting. Passing
    the work-package KEYS to step 2 keeps every query one level deep, and does
    not depend on the `parent` field being populated — the WC/WP hierarchy here
    is a relation, not a subtask link.
    """
    if jira.config.is_mock:
        return load_mock_containers()

    lineage = lineage_jql(template_keys)
    logger.info("  [1/2] Work packages cloned from %d template(s)...",
                len(template_keys))
    logger.debug("        %s", lineage)
    wp_keys = sorted({i["key"] for i in jira.search_all(lineage, fields=["key"])})
    logger.info("        %d work package(s)", len(wp_keys))
    if not wp_keys:
        logger.warning("        No work packages matched — check the template "
                       "keys (container_reporters.template_keys)")
        return []

    batches = chunk(wp_keys)
    logger.info("  [2/2] Their containers, in %d batch(es) of up to %d keys...",
                len(batches), len(batches[0]))
    seen: set[str] = set()
    issues: list[dict] = []
    for n, batch in enumerate(batches, 1):
        found = jira.search_all(parents_jql(batch, scope, since=since, until=until),
                                fields=FIELDS)
        new = [i for i in found if i.get("key") and i["key"] not in seen]
        seen.update(i["key"] for i in new)
        issues.extend(new)
        logger.debug("        batch %d/%d: %d hit(s), %d new (running total %d)",
                     n, len(batches), len(found), len(new), len(issues))
    logger.info("        %d distinct container(s)", len(issues))
    return issues


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel on the company laptop opens a plain utf-8 CSV as mojibake.
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def run(mode: str, scope: str = "resolved", since: str | None = None,
        until: str | None = None, source: str = DEFAULT_SOURCE,
        all_dates: bool = False, show_jql: bool = False,
        verbose: bool = False) -> int:
    config = load_config(mode_override=mode)
    logger = get_logger(TASK_NAME, log_dir=config.log_dir,
                        level="DEBUG" if verbose else "INFO")

    if since is None and not all_dates:
        since = DEFAULT_SINCE
    if all_dates:
        since = None

    board_filter = str(config.get("container_reporters.board_filter",
                                  BOARD_FILTER_ID) or BOARD_FILTER_ID)
    template_keys = tuple(config.get("container_reporters.template_keys",
                                     TEMPLATE_KEYS) or TEMPLATE_KEYS)
    try:
        since = check_date(since, "--since")
        until = check_date(until, "--until")
        jql = None if source == "template" else build_jql(
            scope, since=since, until=until, source=source,
            board_filter=board_filter)
    except ValueError as exc:
        raise FriendlyError(str(exc)) from exc

    if scope == "open" and (since or until):
        logger.warning("--since/--until ignored: open containers have no resolved date")

    logger.info("=" * 60)
    logger.info("Container reporter export starting (%s mode, source=%s, scope=%s)",
                config.mode, source, scope)
    if source == "board":
        logger.info("  Board filter: filter=%s (Project Parent, level1)", board_filter)
    elif source == "template":
        logger.info("  Templates: %s", ", ".join(template_keys))
    if since or until:
        logger.info("  Resolved between %s and %s", since or "(any)", until or "(any)")
    if show_jql or verbose:
        if source == "template":
            logger.info("  Lineage JQL: %s", lineage_jql(template_keys))
            logger.info("  Container JQL (per batch): %s",
                        parents_jql(["<work package keys>"], scope,
                                    since=since, until=until))
        else:
            logger.info("  JQL: %s", jql)

    # The board filter selects work packages that are still Waiting / In
    # Progress / Backlog, so a container drops off the board as soon as its
    # last WP finishes. Asking that population for RESOLVED containers is a
    # narrow question by construction — say so before the row count surprises
    # anyone.
    if source == "board" and scope == "resolved" and BOARD_IS_OPEN_WORK_ONLY:
        logger.info("  NOTE: filter %s only holds containers that still have an "
                    "open work package, so a fully closed container cannot "
                    "appear here. --source template is the same NPI family "
                    "without that restriction.", board_filter)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    if source == "template":
        issues = fetch_template_containers(jira, scope, since, until,
                                           template_keys, logger)
    else:
        issues = fetch_containers(jira, jql, logger)
    logger.info("  JIRA returned %d container(s)", len(issues))

    rows = build_rows(issues)
    if config.is_mock:
        # The fixture is the full population; the scope/date window is applied
        # here so --mock previews the same shape a live run would produce. The
        # relation() clause cannot be emulated offline — mock proves the shape,
        # not the membership.
        rows = filter_rows(rows, scope, since=since, until=until)
        logger.info("  %d after applying scope/date filter to the fixture", len(rows))

    # Container level only — Work Packages are a different issue type and the
    # JQL never asks for them. Prove it in the log rather than assuming it.
    strays = non_containers(rows)
    if strays:
        logger.warning("  %d row(s) are NOT container-level (issue type is not "
                       "'%s', or they have a parent) — check the JQL:",
                       len(strays), WC_ISSUE_TYPE)
        for row in strays[:10]:
            logger.warning("    %s  type=%s  parent=%s",
                           row["issueKey"], row["issueType"] or "(none)",
                           row["parentKey"] or "(none)")
    else:
        logger.info("  All %d row(s) are '%s' with no parent — no Work Packages",
                    len(rows), WC_ISSUE_TYPE)

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

    logger.info("  By issue type: %s", ", ".join(
        f"{t}={n}" for t, n in count_by(rows, "issueType")) or "none")

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
    parser.add_argument("--source", choices=SOURCES, default=DEFAULT_SOURCE,
                        help="Which container population: 'board' (the Project "
                             f"Parents of saved filter {BOARD_FILTER_ID}, "
                             "Singapore) or 'overlay' (issue-type query, "
                             f"SG+Trutnov). Default: {DEFAULT_SOURCE}")
    parser.add_argument("--scope", choices=SCOPES, default="resolved",
                        help="Which containers to export (default: resolved)")
    parser.add_argument("--since", default=None,
                        help="Only containers resolved on/after this date "
                             f"(YYYY-MM-DD; default {DEFAULT_SINCE})")
    parser.add_argument("--all-dates", action="store_true",
                        help=f"No date floor at all (drops the {DEFAULT_SINCE} default)")
    parser.add_argument("--until", default=None,
                        help="Only containers resolved on/before this date (YYYY-MM-DD)")
    parser.add_argument("--show-jql", action="store_true",
                        help="Log the JQL that will be run")
    parser.add_argument("--verbose", action="store_true", help="Debug-level logging")
    parser.set_defaults(mode="mock")
    args = parser.parse_args()
    try:
        return run(args.mode, scope=args.scope, since=args.since, until=args.until,
                   source=args.source, all_dates=args.all_dates,
                   show_jql=args.show_jql, verbose=args.verbose)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
