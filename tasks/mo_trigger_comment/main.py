"""
mo_trigger_comment — Phase 1 (gather + assemble).

When prerequisite Work Packages are done and SMT Build hasn't started,
stitch together the MO-trigger comment a planner needs (item table,
dates, E5 status, routing checks, delivery info) and save it as
`outputs/mo_trigger_{KEY}.txt` for human review. Phase 2 (Playwright
XECX450) and Phase 3 (Confluence/JIRA push) are explicitly out of scope.

Usage:
    python -m tasks.mo_trigger_comment.main --mock
    python -m tasks.mo_trigger_comment.main --live
    python -m tasks.mo_trigger_comment.main --live --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger
from core.m3 import M3Client

from tasks.bom_scanner.logic import extract_article_numbers
from tasks.mo_trigger_comment import m3_checks
from tasks.mo_trigger_comment.logic import (
    add_working_days,
    assemble_comment,
    build_fyi_list,
    check_readiness,
    detect_pilot_run,
    detect_programme_ic,
    extract_order_type,
    find_wp_by_name,
    format_date,
    get_wp_assignee,
    has_duplicate_marker,
    next_working_day,
    parse_delivery_info,
    parse_item_table,
)

TASK_NAME = "mo_trigger_comment"
MOCK_DIR = TASK_DIR / "mock_data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

# Reuse the exact JQL the other SG SMT PCBA tasks use. Any future update
# to the filter belongs in one place.
ACTIVE_CONTAINERS_JQL = (
    'issuetype = "Work Container" '
    'AND "Product Type" = "SMT PCBA" '
    'AND "NPI Location" = "Singapore" '
    'AND resolution is EMPTY '
    'ORDER BY created ASC'
)

_WP_FIELDS = ["summary", "status", "resolution", "resolutiondate", "assignee"]


# ── JIRA fetch helpers ───────────────────────────────────────────────


def _load_mock_wps(container_key: str, logger) -> list[dict[str, Any]]:
    """Read `wps_{KEY}.json` directly in mock mode."""
    path = MOCK_DIR / f"wps_{container_key}.json"
    if not path.exists():
        logger.debug("%s: no wps_%s.json mock", container_key, container_key)
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("issues", []) or []
    if isinstance(data, list):
        return data
    return []


def fetch_child_wps(
    jira: JiraClient, container_key: str, logger,
) -> list[dict[str, Any]]:
    """
    Return child Work Packages of a container via a level1 relation()
    search. Mock mode bypasses the JQL-sanitised filename convention and
    reads `wps_{KEY}.json` directly — matches the TASK.md fixture layout.
    """
    if jira.config.is_mock:
        return _load_mock_wps(container_key, logger)

    jql = f'issue in relation("{container_key}", "Project Children", level1)'
    result = jira.search(jql, fields=_WP_FIELDS, max_results=100)
    return result.get("issues", []) or []


def fetch_container_keys(jira: JiraClient, logger) -> list[str]:
    result = jira.search(
        ACTIVE_CONTAINERS_JQL, fields=["summary", "status"], max_results=500,
    )
    issues = result.get("issues", []) or []
    keys = [i.get("key") for i in issues if i.get("key")]
    logger.info("JIRA: %d open SG SMT PCBA container(s)", len(keys))
    return keys


def resolve_container(
    jira: JiraClient, key: str, logger,
) -> dict[str, Any] | None:
    """Fetch with renderedFields + comments. None when mock is missing."""
    if jira.config.is_mock and not (MOCK_DIR / f"issue_{key}.json").exists():
        logger.debug("Skipping %s \u2014 no issue_%s.json", key, key)
        return None
    try:
        return jira.get_issue(key, expand="renderedFields")
    except FriendlyError as exc:
        logger.warning("Failed to fetch %s: %s", key, exc.message)
        return None


# ── Per-container pipeline ───────────────────────────────────────────


def _rendered_description(issue: dict[str, Any]) -> str:
    """
    Prefer the HTML from renderedFields (has proper <table>/<div>
    markup), fall back to raw description text.
    """
    rendered = (issue.get("renderedFields") or {}).get("description") or ""
    if rendered:
        return rendered
    return (issue.get("fields") or {}).get("description") or ""


def _reporter_display_name(issue: dict[str, Any]) -> str:
    reporter = (issue.get("fields") or {}).get("reporter") or {}
    return (reporter.get("displayName") or "").strip()


def _compute_mo_dates(duration_days: int) -> tuple[date, date]:
    today = date.today()
    start = next_working_day(today)
    end = add_working_days(start, max(duration_days - 1, 0))
    return start, end


def _consolidate(articles: list[str], by_article: dict[str, str]) -> str:
    """
    Collapse per-article M3 status lines for the comment body.

    * No articles → empty string (caller substitutes a fallback).
    * Single article → its line verbatim.
    * Multiple articles, all identical → the shared line once, no prefix.
    * Multiple articles, differing → "article: line" pairs joined by ", ".

    `articles` drives both the presence check and the emission order so
    the assembled output is deterministic even when the dict is not.
    """
    if not articles:
        return ""
    ordered = [by_article.get(a, "") for a in articles]
    if len(articles) == 1:
        return ordered[0]
    if len(set(ordered)) == 1:
        return ordered[0]
    return ", ".join(f"{article}: {by_article.get(article, '')}" for article in articles)


def _assignee_or_placeholder(
    wps: list[dict[str, Any]], wp_name: str, logger, container_key: str,
) -> str:
    wp = find_wp_by_name(wps, wp_name)
    name = get_wp_assignee(wp)
    if not name:
        logger.warning(
            "%s: %s WP unassigned \u2014 using [UNASSIGNED]",
            container_key, wp_name,
        )
        return "[UNASSIGNED]"
    return name


def process_container(
    *,
    issue: dict[str, Any],
    wps: list[dict[str, Any]],
    m3: M3Client,
    mo_task_config: dict[str, Any],
    logger,
) -> dict[str, Any]:
    """
    Run the full Phase-1 pipeline on a single container and return a
    result record with either an assembled comment body or a skip reason.
    """
    key = issue.get("key", "?")
    fields = issue.get("fields") or {}
    comments = ((fields.get("comment") or {}).get("comments")) or []
    summary = (fields.get("summary") or "").strip()
    order_type_raw = extract_order_type(issue)

    base_record: dict[str, Any] = {
        "key": key,
        "summary": summary,
        "order_type": order_type_raw,
    }

    ready, reasons = check_readiness(wps)
    if not ready:
        logger.info("%s: not ready \u2014 %s", key, "; ".join(reasons))
        return {**base_record, "ready": False, "skip_reason": "; ".join(reasons)}

    marker = str(mo_task_config.get("duplicate_marker") or "").strip()
    if marker and has_duplicate_marker(comments, marker):
        logger.info("%s: duplicate marker %s present \u2014 skipping", key, marker)
        return {**base_record, "ready": True, "skip_reason": "duplicate marker"}

    description_html = _rendered_description(issue)
    items = parse_item_table(description_html)
    if not items:
        logger.warning("%s: no NPI Built Type table parsed \u2014 skipping", key)
        return {**base_record, "ready": True, "skip_reason": "no item table"}

    delivery_info = parse_delivery_info(description_html) or "(not specified)"

    is_pilot, pilot_warnings = detect_pilot_run(issue, wps)
    for w in pilot_warnings:
        logger.warning("%s: %s", key, w)
    is_programme_ic = detect_programme_ic(issue, wps)

    pe_assignee = _assignee_or_placeholder(wps, "pe - technprep", logger, key)
    te_assignee = _assignee_or_placeholder(wps, "te - technprep", logger, key)

    qm_wp = find_wp_by_name(wps, "qm p+l")
    qm_assignee = get_wp_assignee(qm_wp) or str(
        mo_task_config.get("qm_default_assignee") or "Chern JR Daniel"
    )

    smt_line = str(mo_task_config.get("smt_line") or "Line 5")
    duration_days = int(mo_task_config.get("mo_duration_days") or 4)
    mo_start, mo_end = _compute_mo_dates(duration_days)

    # M3 enrichment — per article. When multiple articles share a
    # container we consolidate identical lines and only break them out
    # with an article-number prefix when they genuinely differ.
    articles = sorted({
        art for item in items
        for art in extract_article_numbers(item.get("part_number", ""))
    })
    if not articles:
        logger.warning(
            "%s: no article numbers extractable from item table \u2014 "
            "M3 checks skipped", key,
        )

    e5_by_article: dict[str, str] = {}
    breaking_by_article: dict[str, str] = {}
    aoi_test_by_article: dict[str, str] = {}
    pkg_by_article: dict[str, str] = {}

    for article in articles:
        e5_by_article[article] = m3_checks.check_partial_e5(m3, article, logger)
        breaking, aoi_test = m3_checks.check_routing(m3, article, te_assignee, logger)
        breaking_by_article[article] = breaking
        # Strip the TE-assignee prefix so identical routings across
        # articles collapse cleanly; it's re-applied by the assembler.
        aoi_test_by_article[article] = aoi_test.removeprefix(f"{te_assignee} ").strip()
        pkg_by_article[article] = m3_checks.check_bom_packaging(m3, article, logger)

    e5_status_line = _consolidate(articles, e5_by_article) or "(no article # \u2014 check manually)"
    breaking_status_line = _consolidate(articles, breaking_by_article) or \
        "\u26a0 Breaking check skipped (no article)"
    packaging_material_status_line = _consolidate(articles, pkg_by_article) or \
        "\u26a0 Packaging check skipped (no article)"
    aoi_test_status = _consolidate(articles, aoi_test_by_article) or \
        "\u26a0 AOI/Test check skipped (no article)"

    fyi_list = build_fyi_list(
        mo_task_config.get("default_fyi") or [],
        _reporter_display_name(issue),
        wps,
    )

    addressee = str(mo_task_config.get("addressee") or "Ng Ker Cheng Hazel")

    body = assemble_comment(
        addressee=addressee,
        order_type_raw=order_type_raw,
        items=items,
        mo_start=mo_start,
        mo_end=mo_end,
        smt_line=smt_line,
        pe_assignee=pe_assignee,
        te_assignee=te_assignee,
        qm_assignee=qm_assignee,
        is_pilot=is_pilot,
        is_programme_ic=is_programme_ic,
        e5_status_line=e5_status_line,
        breaking_status_line=breaking_status_line,
        packaging_material_status_line=packaging_material_status_line,
        aoi_test_status=aoi_test_status,
        delivery_info=delivery_info,
        fyi_list=fyi_list,
    )

    logger.info(
        "%s: assembled comment (pilot=%s, programme_ic=%s, articles=%d)",
        key, is_pilot, is_programme_ic, len(articles),
    )
    return {
        **base_record,
        "ready": True,
        "body": body,
        "articles": articles,
        "mo_start": format_date(mo_start),
        "mo_end": format_date(mo_end),
        "is_pilot": is_pilot,
        "is_programme_ic": is_programme_ic,
    }


# ── run subcommand ───────────────────────────────────────────────────


def run_assemble(mode: str, dry_run: bool, publish: bool) -> int:
    """
    Gather + assemble MO-trigger comments. Prints each comment to the
    console, saves it to ``outputs/mo_trigger_{KEY}.txt``, and (when
    ``--publish`` is set and we're not in dry-run) refreshes the
    Confluence staging page.
    """
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    logger.info(
        "run: %s mode (dry_run=%s, publish=%s)",
        config.mode, dry_run, publish,
    )

    mo_task_config = (config.get(TASK_NAME) or {})

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    m3 = M3Client(config, mock_data_dir=MOCK_DIR)

    results: list[dict[str, Any]] = []
    try:
        keys = fetch_container_keys(jira, logger)
        if not keys:
            logger.info("No active containers to evaluate")
            return 0

        if not dry_run:
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        ready_count = 0
        written_count = 0
        for key in keys:
            issue = resolve_container(jira, key, logger)
            if issue is None:
                continue
            wps = fetch_child_wps(jira, key, logger)

            result = process_container(
                issue=issue,
                wps=wps,
                m3=m3,
                mo_task_config=mo_task_config,
                logger=logger,
            )
            results.append(result)

            body = result.get("body")
            if not body:
                continue

            ready_count += 1
            print("=" * 72)
            print(f"{key}  \u2014  MO start {result['mo_start']} \u2014 MO end {result['mo_end']}")
            if result.get("articles"):
                print(f"Articles: {', '.join(result['articles'])}")
            print("-" * 72)
            print(body)
            print()

            if not dry_run:
                out_path = OUTPUT_DIR / f"mo_trigger_{key}.txt"
                out_path.write_text(body, encoding="utf-8")
                logger.info("%s: wrote %s", key, out_path)
                written_count += 1

        logger.info(
            "Summary: scanned=%d ready=%d written=%d",
            len(keys), ready_count, written_count,
        )
        print(
            f"\nScanned: {len(keys)}   Ready: {ready_count}   "
            f"{'Dry-run (no files written)' if dry_run else f'Written: {written_count}'}"
        )
    finally:
        m3.close()

    if publish:
        if dry_run:
            logger.info("Dry-run: skipping Confluence publish")
        else:
            from tasks.mo_trigger_comment.publish import publish_results

            publish_results(config, results)

    return 0


# ── comment subcommand ───────────────────────────────────────────────


def _read_staged_body(key: str, logger) -> str | None:
    """Load the assembled comment body saved by the `run` subcommand."""
    path = OUTPUT_DIR / f"mo_trigger_{key}.txt"
    if not path.exists():
        logger.error(
            "%s: %s not found \u2014 run `run --live` first to stage it",
            key, path,
        )
        return None
    return path.read_text(encoding="utf-8").rstrip()


def run_comment(mode: str, keys: list[str], dry_run: bool) -> int:
    """
    Post each staged MO-trigger comment (from outputs/) to the matching
    JIRA container. The duplicate marker from config is appended to
    every posted body and is also the dedupe key used against existing
    comments so re-runs are idempotent.
    """
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    logger.info(
        "comment: %s mode (keys=%s, dry_run=%s)",
        config.mode, keys, dry_run,
    )

    mo_task_config = (config.get(TASK_NAME) or {})
    marker = str(mo_task_config.get("duplicate_marker") or "").strip()
    if not marker:
        logger.warning(
            "duplicate_marker not configured \u2014 comments will post "
            "without a dedupe footer"
        )

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    posted = 0
    skipped = 0
    for key in keys:
        body = _read_staged_body(key, logger)
        if body is None:
            skipped += 1
            continue

        issue = resolve_container(jira, key, logger)
        if issue is None:
            logger.warning("%s: could not resolve issue \u2014 skipping", key)
            skipped += 1
            continue

        existing = ((issue.get("fields") or {}).get("comment") or {}).get("comments") or []
        if marker and has_duplicate_marker(existing, marker):
            logger.info(
                "%s: marker %s already present in comments \u2014 skipping",
                key, marker,
            )
            skipped += 1
            continue

        final_body = f"{body}\n\n{marker}" if marker else body

        if dry_run:
            logger.info(
                "%s: [dry-run] would post MO-trigger comment (%d chars)",
                key, len(final_body),
            )
            print(f"--- dry-run comment for {key} ---")
            print(final_body)
            print()
            continue

        if config.is_mock:
            logger.info("%s: [mock] would post MO-trigger comment", key)
            continue

        try:
            jira.add_comment(key, final_body)
        except FriendlyError as exc:
            logger.error("%s: failed to post comment: %s", key, exc.message)
            skipped += 1
            continue

        logger.info("%s: posted MO-trigger comment", key)
        posted += 1

    logger.info(
        "comment summary: requested=%d posted=%d skipped=%d",
        len(keys), posted, skipped,
    )
    print(
        f"\nRequested: {len(keys)}   Posted: {posted}   Skipped: {skipped}"
    )
    return 0


# ── CLI ──────────────────────────────────────────────────────────────

SUBCOMMANDS = ("run", "comment")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tasks.mo_trigger_comment.main",
        description=(
            "Assemble MO-trigger comments. `run` gathers containers, "
            "writes outputs/mo_trigger_{KEY}.txt, and (with --publish) "
            "refreshes the Confluence staging page. `comment` posts a "
            "previously-staged comment onto specific JIRA containers."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_common(sp: argparse.ArgumentParser) -> None:
        group = sp.add_mutually_exclusive_group()
        group.add_argument(
            "--mock", action="store_const", const="mock", dest="mode",
            help="Read from mock_data/ (default)",
        )
        group.add_argument(
            "--live", action="store_const", const="live", dest="mode",
            help="Hit live JIRA + M3 + Confluence (company laptop only)",
        )
        sp.set_defaults(mode="mock")
        sp.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen but do not write files, "
                 "post JIRA comments, or update Confluence.",
        )

    run_p = subparsers.add_parser(
        "run",
        help="Assemble comments + save to outputs/ (+ --publish to Confluence).",
    )
    _add_common(run_p)
    run_p.add_argument(
        "--publish",
        action="store_true",
        help="Also refresh the Confluence staging page "
             "(pages.mo_trigger_comment). Live + non-dry-run only.",
    )

    comment_p = subparsers.add_parser(
        "comment",
        help="Post previously-staged comments to specific JIRA containers.",
    )
    _add_common(comment_p)
    comment_p.add_argument(
        "--keys",
        nargs="+",
        required=True,
        metavar="KEY",
        help="Container keys to comment on (e.g. --keys NPIOTHER-4566 ACDC-1041)",
    )

    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """
    Back-compat shim: if the first argument is a flag, inject the
    default `run` subcommand. Bare `-h`/`--help` falls through so
    argparse can show the top-level help.
    """
    if not argv:
        return argv
    first = argv[0]
    if first in SUBCOMMANDS or first in ("-h", "--help"):
        return argv
    if first.startswith("-"):
        return ["run"] + argv
    return argv


def main() -> int:
    argv = _normalize_argv(sys.argv[1:])
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return run_assemble(
                mode=args.mode,
                dry_run=args.dry_run,
                publish=args.publish,
            )
        if args.command == "comment":
            return run_comment(
                mode=args.mode,
                keys=args.keys,
                dry_run=args.dry_run,
            )
        parser.error(f"unknown command: {args.command}")
        return 2
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
