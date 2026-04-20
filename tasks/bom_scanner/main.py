"""
bom_scanner — BOM PLC Check across JIRA + Confluence Work Containers.

Two-phase operator workflow (driven by subcommands):

    scan     — Phase A (gather) + Phase B (BOM PLC check). Publishes
               results to Confluence. Strictly read-only against JIRA
               (search + get_issue). NEVER posts JIRA comments.

    comment  — Re-scans the explicit --keys list and posts ONE
               aggregated JIRA comment per container, grouping every
               flagged article into a single `[~reporter]` mention.

Backward compat: running with bare flags (no subcommand) is treated as
`scan`, so existing `python -m tasks.bom_scanner.main --live
--target-status 310` invocations keep working.

Usage:
    python -m tasks.bom_scanner.main scan --live --target-status 310
    python -m tasks.bom_scanner.main scan --mock --target-status 310 --dry-run
    python -m tasks.bom_scanner.main comment --live --target-status 310 \\
        --keys POSX-6558 NPIOTHER-3673
    python -m tasks.bom_scanner.main comment --live --target-status 310 \\
        --keys POSX-6558 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.confluence import ConfluenceClient
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger
from core.m3 import M3Client

from tasks.bom_scanner.logic import (
    already_commented,
    build_aggregated_comment_body,
    dedupe_containers,
    extract_article_numbers,
    extract_confluence_container_keys,
    format_table,
    normalize_component,
    summarize,
)
# Reuse the live JQL verbatim — any change there should apply here too.
from tasks.to_status_check.main import ACTIVE_CONTAINERS_JQL

TASK_NAME = "bom_scanner"
MOCK_DIR = TASK_DIR / "mock_data"
MR_STATUS_PAGE_KEY = "mr_status_report"

SUBCOMMANDS = ("scan", "comment")

# Core BOM query — MPDMAT joined to MITMAS_AP. STRT='STD' + FACI='MF1'
# scope to the standard Singapore BOM. TRIM() on MMCFI3 handles padded
# string columns returned by the Oracle DSN.
BOM_FLAGGED_SQL = """
SELECT DISTINCT m.PMMTNO, i.MMCFI3, i.MMSTAT, i.MMITDS
FROM PFODS.MPDMAT m
JOIN PFODS.MITMAS_AP i ON i.MMITNO = m.PMMTNO
WHERE m.PMPRNO = ?
  AND m.PMSTRT = 'STD'
  AND m.PMFACI = 'MF1'
  AND TRIM(i.MMCFI3) != ?
""".strip()

# Product-structure existence gate — the regex that extracts article
# numbers from the free-text Description also catches cost centres and
# unrelated reference numbers. This filters to real M3 products with a
# STD structure in MF1 before we run the heavier BOM query.
MPDHED_STD_EXISTS_SQL = """
SELECT COUNT(*) AS CNT
FROM PFODS.MPDHED
WHERE PHPRNO = ? AND PHSTRT = 'STD' AND PHFACI = 'MF1'
""".strip()


# ── Phase A: Gather containers ───────────────────────────────────────

def fetch_jira_container_keys(jira: JiraClient, logger) -> list[str]:
    """Run the active-containers JQL and return raw keys in search order."""
    result = jira.search(
        ACTIVE_CONTAINERS_JQL,
        fields=["summary", "status"],
        max_results=500,
    )
    issues = result.get("issues", []) or []
    keys = [i.get("key") for i in issues if i.get("key")]
    logger.info("JIRA: %d active Work Container(s)", len(keys))
    return keys


def fetch_confluence_container_keys(config, logger) -> set[str]:
    """Pull the MR Status Report HTML and parse tables 1+2 for keys."""
    page_id = config.pages.get(MR_STATUS_PAGE_KEY)
    if not page_id:
        logger.warning(
            "Confluence source skipped: pages.%s not configured",
            MR_STATUS_PAGE_KEY,
        )
        return set()

    client = ConfluenceClient(config, mock_data_dir=MOCK_DIR)
    html = client.get_page_html(page_id)
    keys = extract_confluence_container_keys(html)
    if not keys:
        logger.error(
            "Confluence: zero container keys extracted from page %s — "
            "layout may have changed",
            page_id,
        )
    else:
        logger.info("Confluence: %d container key(s) from MR Status Report", len(keys))
    return keys


def resolve_issue(jira: JiraClient, key: str, logger) -> dict[str, Any] | None:
    """
    Fetch a full issue including description + reporter.

    In mock mode we only have fixtures for a handful of keys, so missing
    files are downgraded to a debug skip rather than an error — matches
    the pattern in tasks/to_status_check/main.py.
    """
    if jira.config.is_mock and not (MOCK_DIR / f"issue_{key}.json").exists():
        logger.debug("Skipping %s — no mock file", key)
        return None
    try:
        return jira.get_issue(key, expand="renderedFields")
    except FriendlyError as exc:
        logger.warning("Failed to fetch %s: %s", key, exc.message)
        return None


# ── Phase B: BOM PLC check ───────────────────────────────────────────

def product_structure_exists(m3: M3Client, article: str, logger) -> bool:
    """
    True when the article has a STD product structure in MF1.

    Filters out the regex's false positives — cost centres and random
    reference numbers that happen to match `70\\d{5,6}` but are not
    actual M3 products.

    Mock-mode quirk: if there's no `m3_hed_<article>.json` fixture we
    treat the structure as *existing* so the existing `m3_bom_<article>`
    fixtures still exercise the BOM-query path. In live mode a missing
    row is authoritative — we skip.
    """
    mock_filename = f"m3_hed_{article}.json"
    try:
        rows = m3.query(
            MPDHED_STD_EXISTS_SQL,
            params=(article,),
            mock_filename=mock_filename,
        )
    except FriendlyError as exc:
        if m3.config.is_mock and "mock data not found" in exc.message:
            logger.debug(
                "No MPDHED mock for %s — assuming structure exists for test purposes",
                article,
            )
            return True
        raise

    if not rows:
        return False
    cnt = rows[0].get("CNT") if isinstance(rows[0], dict) else None
    try:
        return int(cnt or 0) > 0
    except (TypeError, ValueError):
        return False


def query_bom_flagged(
    m3: M3Client,
    article_number: str,
    target_status: str,
    logger,
) -> list[dict[str, str]] | None:
    """
    Return the list of flagged components for one article, or None if the
    article has no BOM data at all in mock mode.

    An empty list means the BOM exists but every component is at the
    target PLC.
    """
    mock_filename = f"m3_bom_{article_number}.json"
    try:
        rows = m3.query(
            BOM_FLAGGED_SQL,
            params=(article_number, target_status),
            mock_filename=mock_filename,
        )
    except FriendlyError as exc:
        if m3.config.is_mock and "mock data not found" in exc.message:
            logger.debug("No mock for %s (%s)", article_number, mock_filename)
            return None
        raise

    if not rows:
        return []
    return [normalize_component(r) for r in rows]


def scan_single_container(
    jira: JiraClient,
    m3: M3Client,
    key: str,
    sources: list[str],
    target_status: str,
    logger,
) -> dict[str, Any] | None:
    """
    Full per-container scan: resolve issue, extract articles, gate via
    MPDHED, run BOM query. Returns a result record or None if the issue
    cannot be resolved (missing mock fixture in mock mode).

    The returned record carries the raw `comments` list so the caller
    (comment command) can run the already-commented check without
    re-fetching the issue.
    """
    issue = resolve_issue(jira, key, logger)
    if issue is None:
        return None

    fields = issue.get("fields") or {}
    description = fields.get("description") or ""
    reporter_name = ((fields.get("reporter") or {}).get("name")) or ""
    comments = ((fields.get("comment") or {}).get("comments")) or []

    record: dict[str, Any] = {
        "key": key,
        "sources": sources,
        "reporter": reporter_name,
        "comments": comments,
        "articles": [],
        "action_taken": "",
    }

    articles = extract_article_numbers(description)
    if not articles:
        logger.info("%s: no article # in description", key)
        record["action_taken"] = "no article #"
        return record

    for article in articles:
        if not product_structure_exists(m3, article, logger):
            logger.info("%s: article %s: no STD product structure, skipping",
                        key, article)
            record["articles"].append({
                "article_number": article,
                "flagged": [],
                "note": "no STD product structure",
            })
            continue

        flagged = query_bom_flagged(m3, article, target_status, logger)
        if flagged is None:
            record["articles"].append({
                "article_number": article,
                "flagged": [],
                "note": "no BOM in M3",
            })
            continue

        record["articles"].append({
            "article_number": article,
            "flagged": flagged,
            "note": "clean" if not flagged else "",
        })

    return record


# ── scan command ─────────────────────────────────────────────────────

def run_scan(
    mode: str,
    target_status: str,
    source: str,
    dry_run: bool,
) -> int:
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    logger.info(
        "scan: %s mode (target_status=%s, source=%s, dry_run=%s)",
        config.mode, target_status, source, dry_run,
    )

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    jira_keys: list[str] = []
    confluence_keys: set[str] = set()
    if source in ("jira", "both"):
        jira_keys = fetch_jira_container_keys(jira, logger)
    if source in ("confluence", "both"):
        confluence_keys = fetch_confluence_container_keys(config, logger)

    containers = dedupe_containers(jira_keys, confluence_keys)
    logger.info("Total unique containers: %d", len(containers))

    m3 = M3Client(config, mock_data_dir=MOCK_DIR)
    try:
        results: list[dict[str, Any]] = []
        for entry in containers:
            record = scan_single_container(
                jira, m3, entry["key"], entry["sources"], target_status, logger,
            )
            if record is None:
                continue

            if record["action_taken"]:
                # already set by scan_single_container (e.g. "no article #")
                pass
            elif any(a["flagged"] for a in record["articles"]):
                record["action_taken"] = "flagged — review on Confluence"
            else:
                record["action_taken"] = "no flags"

            results.append(record)
    finally:
        m3.close()

    print(format_table(results))
    print()
    _print_summary(results)

    if dry_run:
        logger.info("Dry-run: skipping Confluence publish")
    else:
        from tasks.bom_scanner.publish import publish_results

        publish_results(config, results, target_status=target_status)

    return 0


# ── comment command ──────────────────────────────────────────────────

def run_comment(
    mode: str,
    target_status: str,
    keys: list[str],
    dry_run: bool,
) -> int:
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    logger.info(
        "comment: %s mode (target_status=%s, keys=%s, dry_run=%s)",
        config.mode, target_status, keys, dry_run,
    )

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    m3 = M3Client(config, mock_data_dir=MOCK_DIR)

    posted = 0
    skipped = 0
    try:
        for key in keys:
            record = scan_single_container(
                jira, m3, key, ["manual"], target_status, logger,
            )
            if record is None:
                logger.warning("%s: could not resolve issue — skipping", key)
                skipped += 1
                continue

            articles_with_flags = [a for a in record["articles"] if a["flagged"]]
            if not articles_with_flags:
                logger.info("%s: no flagged components — nothing to comment", key)
                skipped += 1
                continue

            reporter_name = record["reporter"]
            if not reporter_name:
                logger.warning("%s: has flags but no reporter — manual follow-up", key)
                skipped += 1
                continue

            if already_commented(record["comments"]):
                logger.info("%s: BOM Scanner comment already present — skipping", key)
                skipped += 1
                continue

            body = build_aggregated_comment_body(
                reporter_name, articles_with_flags, target_status,
            )
            component_total = sum(len(a["flagged"]) for a in articles_with_flags)

            if dry_run:
                logger.info(
                    "%s: [dry-run] would post 1 comment (%d article(s), %d component(s))",
                    key, len(articles_with_flags), component_total,
                )
                print(f"--- dry-run comment for {key} ---")
                print(body)
                print()
                continue

            if config.is_mock:
                logger.info(
                    "%s: [mock] would post 1 comment (%d article(s), %d component(s))",
                    key, len(articles_with_flags), component_total,
                )
                continue

            try:
                jira.add_comment(key, body)
            except FriendlyError as exc:
                logger.error("%s: failed to post comment: %s", key, exc.message)
                skipped += 1
                continue

            logger.info(
                "%s: posted aggregated BOM comment (%d article(s), %d component(s))",
                key, len(articles_with_flags), component_total,
            )
            posted += 1
    finally:
        m3.close()

    logger.info(
        "comment summary: requested=%d posted=%d skipped=%d",
        len(keys), posted, skipped,
    )
    print(f"\nRequested: {len(keys)}   Posted: {posted}   Skipped: {skipped}")
    return 0


# ── Shared output helpers ────────────────────────────────────────────

def _print_summary(results: list[dict[str, Any]]) -> None:
    summary = summarize(results)
    print(
        f"Total: {summary['total']}   "
        f"With article #: {summary['with_articles']}   "
        f"No article #: {summary['no_articles']}   "
        f"Flagged: {summary['flagged_containers']}"
    )


# ── CLI ──────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tasks.bom_scanner.main",
        description=(
            "BOM PLC Scanner — two-phase workflow. `scan` publishes to "
            "Confluence (read-only JIRA). `comment` posts one aggregated "
            "JIRA comment per --keys container."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_common(sp: argparse.ArgumentParser) -> None:
        group = sp.add_mutually_exclusive_group()
        group.add_argument(
            "--mock",
            action="store_const", const="mock", dest="mode",
            help="Read from mock_data/ (default)",
        )
        group.add_argument(
            "--live",
            action="store_const", const="live", dest="mode",
            help="Hit live JIRA + M3 + Confluence (company laptop only)",
        )
        sp.set_defaults(mode="mock")
        sp.add_argument(
            "--target-status",
            required=True,
            help="Target PLC code (e.g. 310). Components with any other PLC are flagged.",
        )
        sp.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done but do not write to JIRA or Confluence.",
        )

    scan_p = subparsers.add_parser(
        "scan",
        help="Scan containers and publish results to Confluence (no JIRA writes).",
    )
    _add_common(scan_p)
    scan_p.add_argument(
        "--source",
        choices=("jira", "confluence", "both"),
        default="both",
        help="Container source (default: both)",
    )

    comment_p = subparsers.add_parser(
        "comment",
        help="Post one aggregated BOM Scanner comment per container in --keys.",
    )
    _add_common(comment_p)
    comment_p.add_argument(
        "--keys",
        nargs="+",
        required=True,
        metavar="KEY",
        help="Container keys to comment on (e.g. --keys POSX-6558 NPIOTHER-3673)",
    )

    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """
    Backward-compat: if the first argument is a flag (e.g. `--live
    --target-status 310`), inject the `scan` subcommand. This preserves
    the pre-subcommand invocation style.

    Bare `-h` / `--help` at the top level falls through to argparse so
    the global help lists both subcommands.
    """
    if not argv:
        return argv
    first = argv[0]
    if first in SUBCOMMANDS or first in ("-h", "--help"):
        return argv
    if first.startswith("-"):
        return ["scan"] + argv
    return argv


def main() -> int:
    argv = _normalize_argv(sys.argv[1:])
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "scan":
            return run_scan(
                mode=args.mode,
                target_status=args.target_status,
                source=args.source,
                dry_run=args.dry_run,
            )
        if args.command == "comment":
            return run_comment(
                mode=args.mode,
                target_status=args.target_status,
                keys=args.keys,
                dry_run=args.dry_run,
            )
        parser.error(f"unknown command: {args.command}")
        return 2
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
