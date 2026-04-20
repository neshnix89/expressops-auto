"""
bom_scanner — BOM PLC Check across JIRA + Confluence Work Containers.

Phase A: Gather active Work Containers
         - JIRA: ACTIVE_CONTAINERS_JQL (SMT PCBA + Singapore + open)
         - Confluence: MR Status Report page, tables 1 and 2
Phase B: Extract article numbers from each container's Description, then
         query M3 PDS (MPDMAT + MITMAS_AP) for components whose PLC is
         not the target status.
Phase C: For flagged containers, post a [~reporter] comment naming the
         offending components. Publish a results table to Confluence.

Usage:
    python -m tasks.bom_scanner.main --mock --target-status 310
    python -m tasks.bom_scanner.main --live --target-status 310
    python -m tasks.bom_scanner.main --live --target-status 310 --source jira
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
from core.errors import FriendlyError, handle_friendly, missing_mock_data
from core.jira_client import JiraClient
from core.logger import get_logger
from core.m3 import M3Client

from tasks.bom_scanner.logic import (
    already_commented,
    build_comment_body,
    dedupe_containers,
    extract_article_numbers,
    extract_confluence_container_keys,
    format_table,
    normalize_component,
    should_flag,
    summarize,
)
# Reuse the live JQL verbatim — any change there should apply here too.
from tasks.to_status_check.main import ACTIVE_CONTAINERS_JQL

TASK_NAME = "bom_scanner"
MOCK_DIR = TASK_DIR / "mock_data"
MR_STATUS_PAGE_KEY = "mr_status_report"

# Core SQL from the handoff — driver against MPDMAT joined to MITMAS_AP.
# Positional ? placeholder for the product number. STRT='STD' + FACI='MF1'
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

def query_bom_flagged(
    m3: M3Client,
    article_number: str,
    target_status: str,
    logger,
) -> list[dict[str, str]] | None:
    """
    Return the list of flagged components for one article, or None if the
    article has no BOM in M3 (no rows at all — either the product is
    unknown or there is simply no structured BOM yet).

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
        # In mock mode we commonly lack fixtures for every article — treat
        # as "no BOM data" so the scanner can still produce an output row.
        if m3.config.is_mock and "mock data not found" in exc.message:
            logger.debug("No mock for %s (%s)", article_number, mock_filename)
            return None
        raise

    if not rows:
        return []
    return [normalize_component(r) for r in rows]


# ── Phase C: JIRA comment ────────────────────────────────────────────

def post_bom_comment(
    jira: JiraClient,
    container_key: str,
    reporter_name: str,
    article_number: str,
    flagged: list[dict[str, str]],
    target_status: str,
    existing_comments: list[dict[str, Any]],
    logger,
) -> str:
    """
    Post the BOM Scanner comment to a single container. Returns the
    action label for the result row.
    """
    if already_commented(existing_comments):
        logger.info("%s: BOM Scanner comment already present — skipping", container_key)
        return "already commented"

    body = build_comment_body(reporter_name, article_number, flagged, target_status)

    if jira.config.is_mock:
        logger.info("%s: [mock] would post BOM comment (%d component(s))",
                    container_key, len(flagged))
        return "mock — comment not posted"

    try:
        jira.add_comment(container_key, body)
    except FriendlyError as exc:
        logger.error("%s: failed to post comment: %s", container_key, exc.message)
        return f"comment failed: {exc.message}"

    logger.info("%s: posted BOM comment for article %s (%d component(s))",
                container_key, article_number, len(flagged))
    return "comment posted"


# ── Orchestration ────────────────────────────────────────────────────

def run(mode: str, target_status: str, source: str, publish: bool) -> int:
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    logger.info(
        "Running %s in %s mode (target_status=%s, source=%s)",
        TASK_NAME, config.mode, target_status, source,
    )

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    # ── Phase A ──
    jira_keys: list[str] = []
    confluence_keys: set[str] = set()

    if source in ("jira", "both"):
        jira_keys = fetch_jira_container_keys(jira, logger)
    if source in ("confluence", "both"):
        confluence_keys = fetch_confluence_container_keys(config, logger)

    containers = dedupe_containers(jira_keys, confluence_keys)
    logger.info("Total unique containers: %d", len(containers))

    # ── Phase B ──
    m3 = M3Client(config, mock_data_dir=MOCK_DIR)
    try:
        results: list[dict[str, Any]] = []
        for entry in containers:
            key = entry["key"]
            issue = resolve_issue(jira, key, logger)
            if issue is None:
                continue

            fields = issue.get("fields") or {}
            description = fields.get("description") or ""
            reporter_name = ((fields.get("reporter") or {}).get("name")) or ""
            comments = ((fields.get("comment") or {}).get("comments")) or []

            articles = extract_article_numbers(description)
            record: dict[str, Any] = {
                "key": key,
                "sources": entry["sources"],
                "reporter": reporter_name,
                "articles": [],
                "action_taken": "",
            }

            if not articles:
                logger.info("%s: no article # in description", key)
                record["action_taken"] = "no article #"
                results.append(record)
                continue

            container_flagged_any = False
            for article in articles:
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
                if should_flag(flagged):
                    container_flagged_any = True

            # ── Phase C (per-container) ──
            if not container_flagged_any:
                record["action_taken"] = "no flags"
                results.append(record)
                continue

            if not reporter_name:
                logger.warning("%s: has flagged components but no reporter — manual follow-up", key)
                record["action_taken"] = "no reporter — manual follow-up"
                results.append(record)
                continue

            # Aggregate: one comment per article so the [~reporter] tag
            # fires once per offending article, matching the handoff's
            # "group flagged components by article" rule.
            actions: list[str] = []
            for art in record["articles"]:
                if not art["flagged"]:
                    continue
                action = post_bom_comment(
                    jira,
                    key,
                    reporter_name,
                    art["article_number"],
                    art["flagged"],
                    target_status,
                    comments,
                    logger,
                )
                actions.append(action)
                # Refresh existing_comments in-memory so a second article
                # in the same run still sees the marker — we only want
                # one "Automated by BOM Scanner" per container per run.
                if action == "comment posted":
                    comments = comments + [{"body": build_comment_body(
                        reporter_name, art["article_number"], art["flagged"],
                        target_status,
                    )}]

            record["action_taken"] = "; ".join(sorted(set(actions))) or "no flags"
            results.append(record)

    finally:
        m3.close()

    # ── Output ──
    print(format_table(results))
    print()

    summary = summarize(results)
    print(
        f"Total: {summary['total']}   "
        f"With article #: {summary['with_articles']}   "
        f"No article #: {summary['no_articles']}   "
        f"Flagged: {summary['flagged_containers']}   "
        f"Commented: {summary['commented']}"
    )

    logger.info(
        "Summary: total=%d with_articles=%d flagged=%d commented=%d",
        summary["total"],
        summary["with_articles"],
        summary["flagged_containers"],
        summary["commented"],
    )

    # ── Publish ──
    if publish:
        from tasks.bom_scanner.publish import publish_results

        publish_results(config, results, target_status=target_status)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="BOM PLC Scanner — flag Work Containers with components outside target PLC"
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
        help="Hit live JIRA + M3 + Confluence (company laptop only)",
    )
    parser.add_argument(
        "--target-status",
        required=True,
        help="Target PLC code (e.g. 310). Components with any other PLC are flagged.",
    )
    parser.add_argument(
        "--source",
        choices=("jira", "confluence", "both"),
        default="both",
        help="Container source (default: both)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Publish the results table to Confluence. Target page is "
            "pages.bom_scanner in config.yaml. Skipped in mock mode."
        ),
    )
    parser.set_defaults(mode="mock")
    args = parser.parse_args()
    try:
        return run(
            mode=args.mode,
            target_status=args.target_status,
            source=args.source,
            publish=args.publish,
        )
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
