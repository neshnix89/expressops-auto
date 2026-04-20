"""
Mock data capture for bom_scanner.

Invoked by scripts/capture_mock_data.py (or `ops capture bom_scanner`)
on the company laptop. Saves everything the scanner needs so VPS
mock runs exercise the real code paths:

1. JIRA search response for ACTIVE_CONTAINERS_JQL.
   Filename matches JiraClient.search()'s mock convention.
2. Full issue responses (description + reporter + comments) for a
   sample of containers.
3. Confluence MR Status Report page — saved as page_<id>.json to match
   ConfluenceClient.get_page()'s mock convention.
4. M3 BOM query results (MPDMAT + MITMAS_AP join) for every article
   number found across the sampled issues, plus a couple of pinned
   references from the handoff. Target status is hard-coded to 310
   since that is what mock runs will pass.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.confluence import ConfluenceClient
from core.jira_client import JiraClient
from core.m3 import M3Client

from tasks.bom_scanner.logic import extract_article_numbers
from tasks.bom_scanner.main import (
    ACTIVE_CONTAINERS_JQL,
    BOM_FLAGGED_SQL,
    MR_STATUS_PAGE_KEY,
)

SAMPLE_ISSUE_COUNT = 10
TARGET_STATUS = "310"

# Pinned references from the handoff — guarantees the fixture set covers
# at least one known-clean article and one known-flagged article even if
# the current sample doesn't reach them.
PINNED_ARTICLES = ("70203371", "70198800", "70204501")


def _mock_search_filename(jql: str) -> str:
    """Mirror JiraClient.search()'s mock filename convention."""
    safe_name = re.sub(r"[^\w]", "_", jql)[:80]
    return f"search_{safe_name}.json"


def _capture_jira(jira: JiraClient, mock_dir: Path, logger) -> list[dict]:
    """Capture search + sample issues. Returns the list of sampled issues."""
    logger.info("Capturing active Work Container search")
    search_result = jira.search(
        ACTIVE_CONTAINERS_JQL,
        fields=["summary", "status"],
        max_results=500,
    )
    search_filename = _mock_search_filename(ACTIVE_CONTAINERS_JQL)
    jira.save_mock(search_result, search_filename, mock_dir)
    issues = search_result.get("issues", []) or []
    logger.info("  Saved %d containers -> %s", len(issues), search_filename)

    sample = issues[:SAMPLE_ISSUE_COUNT]
    logger.info("Capturing full issue (description + reporter) for %d sample(s)", len(sample))
    sampled_full: list[dict] = []
    for issue in sample:
        key = issue.get("key")
        if not key:
            continue
        try:
            full = jira.get_issue(key, expand="renderedFields")
            jira.save_mock(full, f"issue_{key}.json", mock_dir)
            fields = full.get("fields") or {}
            reporter = (fields.get("reporter") or {}).get("name") or "?"
            desc_len = len(fields.get("description") or "")
            logger.info("  %s: reporter=%s desc=%d chars", key, reporter, desc_len)
            sampled_full.append(full)
        except Exception as exc:
            logger.error("  %s: failed to capture \u2014 %s", key, exc)
    return sampled_full


def _capture_confluence(config, mock_dir: Path, logger) -> None:
    """Save the MR Status Report page as page_<id>.json."""
    page_id = config.pages.get(MR_STATUS_PAGE_KEY)
    if not page_id:
        logger.warning("Skipping Confluence capture: pages.%s not configured",
                       MR_STATUS_PAGE_KEY)
        return

    client = ConfluenceClient(config)
    logger.info("Capturing Confluence page %s (%s)", page_id, MR_STATUS_PAGE_KEY)
    try:
        page = client.get_page(page_id)
    except Exception as exc:
        logger.error("  failed: %s", exc)
        return

    client.save_mock(page, f"page_{page_id}.json", mock_dir)
    html = (page.get("body") or {}).get("storage", {}).get("value", "")
    logger.info("  Saved %d bytes of storage HTML", len(html))


def _capture_m3_boms(
    config,
    mock_dir: Path,
    sampled_issues: list[dict],
    logger,
) -> None:
    """Capture the BOM-flagged query for every article across samples."""
    articles: list[str] = list(PINNED_ARTICLES)
    for issue in sampled_issues:
        fields = issue.get("fields") or {}
        description = fields.get("description") or ""
        for art in extract_article_numbers(description):
            if art not in articles:
                articles.append(art)

    logger.info("Capturing M3 BOM results for %d article number(s)", len(articles))
    m3 = M3Client(config)
    try:
        for article in articles:
            try:
                rows = m3.query(
                    BOM_FLAGGED_SQL,
                    params=(article, TARGET_STATUS),
                )
            except Exception as exc:
                logger.error("  %s: query failed \u2014 %s", article, exc)
                continue
            m3.save_mock(rows, f"m3_bom_{article}.json", mock_dir)
            logger.info("  %s: %d flagged row(s)", article, len(rows))
    finally:
        m3.close()


def capture(config, mock_dir: Path, logger) -> None:
    """Entry point used by scripts/capture_mock_data.py."""
    mock_dir.mkdir(parents=True, exist_ok=True)

    jira = JiraClient(config)
    sampled = _capture_jira(jira, mock_dir, logger)

    _capture_confluence(config, mock_dir, logger)
    _capture_m3_boms(config, mock_dir, sampled, logger)

    logger.info("bom_scanner capture complete.")
