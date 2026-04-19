"""
to_status_check — JIRA + M3 TO Status Check.

Phase A: Pulls active Work Containers from JIRA and extracts the Transfer
         Order (TO) number from the latest "TO: <digits>" comment.
Phase B: Looks up each TO number in M3 XDRX800 (via Playwright browser
         automation) to get the current shipment status.

Usage:
    python -m tasks.to_status_check.main --mock       # VPS testing (default)
    python -m tasks.to_status_check.main --live        # Company laptop
    python -m tasks.to_status_check.main --live --jira-only   # Phase A only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger

from tasks.to_status_check.logic import (
    build_container_row,
    enrich_rows_with_to_status,
    format_table,
    summarize,
)

TASK_NAME = "to_status_check"
# Scope to SMT PCBA Singapore containers via three-level ITPL template relations:
# each listed ITPL-xxx key is a template issue whose Project Children (at level4)
# are Tasks/Deviations; those get walked up a "Clone from Template" link and then
# a "Project Parent" link to land on the live Work Containers. Final filters pin
# Product Type to SMT PCBA and NPI Location to Singapore. The JQL uses three
# layers of nested quoting (double → escaped-double → single), so this lives in
# a raw triple-quoted string to avoid Python mangling the backslashes.
ACTIVE_CONTAINERS_JQL = r"""issue in relation("issue in relation(\"issue in relation('key in (ITPL-769, ITPL-760, ITPL-756, ITPL-750, ITPL-746, ITPL-742, ITPL-1036, ITPL-1027)', 'Project Children', Tasks, Deviations, level4)\", \"Project Children\", 'Clone from Template', level4) and project != 'Issue Template' and status in (Waiting, \"In Progress\", Backlog)", "Project Parent", Tasks, Deviations, level1) AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" ORDER BY created ASC"""
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


def run_phase_b(config, rows, logger) -> list[dict]:
    """
    Phase B: Look up TO numbers in M3 XDRX800 via Playwright.

    Only runs for containers that have a TO number from Phase A.
    Returns the enriched rows (mutated in place).
    """
    to_numbers = [r["to_number"] for r in rows if r["has_to"]]
    if not to_numbers:
        logger.info("No TO numbers to look up — skipping Phase B")
        return rows

    logger.info("Phase B: Looking up %d TO(s) in M3 XDRX800...", len(to_numbers))

    from clients.m3_h5_client import M3H5Client

    client = M3H5Client(config, mock_data_dir=MOCK_DIR)
    try:
        client.connect()
        to_statuses = client.get_multiple_to_status(to_numbers)
        enrich_rows_with_to_status(rows, to_statuses)

        found = sum(1 for v in to_statuses.values() if v is not None)
        logger.info(
            "Phase B complete: %d/%d TO(s) found in XDRX800",
            found,
            len(to_numbers),
        )
    finally:
        client.close()

    return rows


LOGISTICS_WP_TRANSITION_ID = "51"  # "Done" transition on Work Packages


def close_logistics_wps(jira: JiraClient, rows: list[dict], logger) -> dict[str, int]:
    """
    For each container row where ready_to_close is True, find the child
    Work Package named "Logistics" via relation() JQL and transition it
    through transition 51 (Done).

    Live mode only — in mock mode we skip with a warning because the
    close action would either be a no-op (transition) or require mock
    search fixtures we don't ship.
    """
    targets = [r for r in rows if r.get("ready_to_close")]
    summary = {"candidates": len(targets), "transitioned": 0, "skipped": 0, "errors": 0}

    if not targets:
        logger.info("Close-ready: no containers are ready_to_close")
        return summary

    if jira.config.is_mock:
        logger.warning(
            "Close-ready: skipping in mock mode (%d candidate(s) would be closed live)",
            len(targets),
        )
        summary["skipped"] = len(targets)
        return summary

    logger.info("Close-ready: %d container(s) ready for Logistics WP close", len(targets))

    for row in targets:
        container_key = row["key"]
        jql = (
            f'issue in relation("{container_key}") '
            f'AND issuetype = "Work Package" AND summary ~ "Logistics"'
        )
        try:
            result = jira.search(jql, fields=["summary", "status"], max_results=20)
            wps = result.get("issues", []) or []
        except Exception as exc:
            logger.error("%s: Logistics WP search failed: %s", container_key, exc)
            summary["errors"] += 1
            continue

        # summary ~ is approximate — filter to WPs whose name contains 'logistics'
        logistics_wps = [
            wp for wp in wps
            if "logistics" in ((wp.get("fields") or {}).get("summary") or "").lower()
        ]

        if not logistics_wps:
            logger.warning("%s: no Logistics Work Package found", container_key)
            summary["skipped"] += 1
            continue
        if len(logistics_wps) > 1:
            logger.warning(
                "%s: %d Logistics WPs matched, using first (%s)",
                container_key,
                len(logistics_wps),
                logistics_wps[0].get("key"),
            )

        wp = logistics_wps[0]
        wp_key = wp.get("key", "?")
        wp_summary = (wp.get("fields") or {}).get("summary", "")

        try:
            jira.transition_issue(wp_key, LOGISTICS_WP_TRANSITION_ID)
            logger.info(
                "%s: transitioned %s '%s' to Done (id %s)",
                container_key, wp_key, wp_summary, LOGISTICS_WP_TRANSITION_ID,
            )
            summary["transitioned"] += 1
        except Exception as exc:
            logger.error(
                "%s: failed to transition %s to Done: %s",
                container_key, wp_key, exc,
            )
            summary["errors"] += 1

    logger.info(
        "Close-ready summary: candidates=%d transitioned=%d skipped=%d errors=%d",
        summary["candidates"],
        summary["transitioned"],
        summary["skipped"],
        summary["errors"],
    )
    return summary


def run(
    mode: str,
    jira_only: bool = False,
    close_ready: bool = False,
    publish: bool = False,
) -> int:
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    logger.info("Running %s in %s mode", TASK_NAME, config.mode)

    # ── Phase A: JIRA extraction ──
    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    issues = fetch_containers_with_comments(jira, ACTIVE_CONTAINERS_JQL, logger)
    logger.info("Fetched %d active Work Containers", len(issues))

    rows = [build_container_row(issue) for issue in issues]
    rows.sort(key=lambda r: r["key"])

    # ── Phase B: M3 TO status lookup ──
    include_m3 = False
    if not jira_only:
        rows = run_phase_b(config, rows, logger)
        include_m3 = any(r.get("to_status") for r in rows)

    # ── Phase C (opt-in): close Logistics WPs for ready containers ──
    if close_ready:
        close_logistics_wps(jira, rows, logger)

    # ── Output ──
    print(format_table(rows, include_m3=include_m3))
    print()

    summary = summarize(rows)
    parts = [
        f"Total: {summary['total']}",
        f"With TO: {summary['with_to']}",
        f"Without TO: {summary['without_to']}",
    ]
    if include_m3:
        parts.append(f"With M3 Status: {summary['with_m3_status']}")
    print("   ".join(parts))

    logger.info(
        "Summary: total=%d with_to=%d without_to=%d with_m3=%d",
        summary["total"],
        summary["with_to"],
        summary["without_to"],
        summary.get("with_m3_status", 0),
    )

    # ── Publish ──
    if publish:
        from tasks.to_status_check.publish import publish_results

        publish_results(config, rows, include_m3=include_m3)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check JIRA Work Container TO numbers + M3 status"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mock",
        action="store_const",
        const="mock",
        dest="mode",
        help="Read from mock_data/ (default)",
    )
    group.add_argument(
        "--live",
        action="store_const",
        const="live",
        dest="mode",
        help="Hit live JIRA + M3 (company laptop only)",
    )
    parser.add_argument(
        "--jira-only",
        action="store_true",
        help="Phase A only — skip M3 TO status lookup",
    )
    parser.add_argument(
        "--close-ready",
        action="store_true",
        help=(
            "After Phase B, transition the Logistics Work Package to Done "
            "for every container with ready_to_close=True. Live mode only — "
            "skipped with a warning in mock mode."
        ),
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Publish the results table to Confluence. Target page is "
            "pages.to_status_check in config.yaml. Skipped in mock mode."
        ),
    )
    parser.set_defaults(mode="mock")
    args = parser.parse_args()
    try:
        return run(
            args.mode,
            jira_only=args.jira_only,
            close_ready=args.close_ready,
            publish=args.publish,
        )
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
