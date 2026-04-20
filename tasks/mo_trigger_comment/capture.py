"""
Mock-data capture for mo_trigger_comment.

Invoked by `scripts/capture_mock_data.py` (or `ops capture mo_trigger_comment`)
on the company laptop. Saves every fixture the Phase-1 pipeline reads:

    search_<sanitized JQL>.json   JIRA: open SG SMT PCBA container list
    issue_<KEY>.json              JIRA: full issue with renderedFields+comments
    wps_<KEY>.json                JIRA: child Work Packages (level1)
    routing_<article>.json        M3 MPDOPE rows
    bom_pkg_<article>.json        M3 MPDMAT Dwgpos 5000 rows
    item_<mmitno>.json            M3 MITMAS_AP (article + packaging component)
    prodstatus_<article>.json     M3 MPDHED row

Article numbers are parsed out of the sampled container descriptions, so
the fixture set tracks whatever is currently active in SG NPI.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.jira_client import JiraClient
from core.m3 import M3Client

from tasks.bom_scanner.logic import extract_article_numbers
from tasks.mo_trigger_comment.logic import parse_item_table
from tasks.mo_trigger_comment.m3_checks import (
    BOM_PACKAGING_SQL,
    ITEM_STATUS_SQL,
    PROD_STATUS_SQL,
    ROUTING_SQL,
)
from tasks.mo_trigger_comment.main import ACTIVE_CONTAINERS_JQL, _WP_FIELDS

SAMPLE_CONTAINER_COUNT = 5


def _mock_search_filename(jql: str) -> str:
    """Mirror JiraClient.search()'s mock filename convention."""
    safe_name = re.sub(r"[^\w]", "_", jql)[:80]
    return f"search_{safe_name}.json"


def _capture_search(jira: JiraClient, mock_dir: Path, logger) -> list[dict]:
    logger.info("Capturing active-containers search")
    result = jira.search(
        ACTIVE_CONTAINERS_JQL, fields=["summary", "status"], max_results=500,
    )
    filename = _mock_search_filename(ACTIVE_CONTAINERS_JQL)
    jira.save_mock(result, filename, mock_dir)
    issues = result.get("issues", []) or []
    logger.info("  Saved %d containers \u2014 %s", len(issues), filename)
    return issues


def _capture_issue(
    jira: JiraClient, key: str, mock_dir: Path, logger,
) -> dict | None:
    try:
        full = jira.get_issue(key, expand="renderedFields")
    except Exception as exc:
        logger.error("  %s: issue fetch failed \u2014 %s", key, exc)
        return None
    jira.save_mock(full, f"issue_{key}.json", mock_dir)

    fields = full.get("fields") or {}
    rendered = (full.get("renderedFields") or {}).get("description") or ""
    desc_len = len(fields.get("description") or "")
    logger.info(
        "  %s: desc=%d chars, rendered=%d chars, comments=%d",
        key, desc_len, len(rendered),
        len(((fields.get("comment") or {}).get("comments")) or []),
    )
    return full


def _capture_wps(
    jira: JiraClient, key: str, mock_dir: Path, logger,
) -> list[dict]:
    jql = f'issue in relation("{key}", "Project Children", level1)'
    try:
        result = jira.search(jql, fields=_WP_FIELDS, max_results=100)
    except Exception as exc:
        logger.error("  %s: WP search failed \u2014 %s", key, exc)
        return []
    jira.save_mock(result, f"wps_{key}.json", mock_dir)
    wps = result.get("issues", []) or []
    logger.info("  %s: %d child WP(s)", key, len(wps))
    return wps


def _collect_articles(sampled_issues: list[dict]) -> list[str]:
    """
    Pull article numbers from each sampled container's parsed item table
    first (the authoritative source), then fall back to whatever the
    free-text regex finds in the raw description. Dedup, preserve order.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(article: str) -> None:
        if article and article not in seen:
            seen.add(article)
            ordered.append(article)

    for issue in sampled_issues:
        rendered = (issue.get("renderedFields") or {}).get("description") or ""
        for item in parse_item_table(rendered):
            for art in extract_article_numbers(item.get("part_number", "")):
                _add(art)

        raw = (issue.get("fields") or {}).get("description") or ""
        for art in extract_article_numbers(raw):
            _add(art)

    return ordered


def _capture_m3_per_article(
    m3: M3Client, article: str, mock_dir: Path, logger,
) -> None:
    """Run all four per-article queries and save each response."""
    try:
        routing = m3.query(ROUTING_SQL, params=(article,))
    except Exception as exc:
        logger.error("  %s: routing failed \u2014 %s", article, exc)
        routing = []
    m3.save_mock(routing, f"routing_{article}.json", mock_dir)

    try:
        bom_pkg = m3.query(BOM_PACKAGING_SQL, params=(article,))
    except Exception as exc:
        logger.error("  %s: BOM packaging failed \u2014 %s", article, exc)
        bom_pkg = []
    m3.save_mock(bom_pkg, f"bom_pkg_{article}.json", mock_dir)

    try:
        item = m3.query(ITEM_STATUS_SQL, params=(article,))
    except Exception as exc:
        logger.error("  %s: item status failed \u2014 %s", article, exc)
        item = []
    m3.save_mock(item, f"item_{article}.json", mock_dir)

    try:
        prod = m3.query(PROD_STATUS_SQL, params=(article,))
    except Exception as exc:
        logger.error("  %s: prod status failed \u2014 %s", article, exc)
        prod = []
    m3.save_mock(prod, f"prodstatus_{article}.json", mock_dir)

    # If the BOM query returned a Dwgpos-5000 component, also capture its
    # MITMAS_AP row so check_bom_packaging() can resolve the description
    # in mock mode.
    for row in bom_pkg:
        if not isinstance(row, dict):
            continue
        pmmtno = (row.get("PMMTNO") or "")
        pmmtno = str(pmmtno).strip()
        if not pmmtno or pmmtno == article:
            continue
        try:
            comp = m3.query(ITEM_STATUS_SQL, params=(pmmtno,))
        except Exception as exc:
            logger.error("  %s: packaging item %s failed \u2014 %s",
                         article, pmmtno, exc)
            continue
        m3.save_mock(comp, f"item_{pmmtno}.json", mock_dir)
        logger.info("  %s: packaging component %s captured", article, pmmtno)

    logger.info(
        "  %s: routing=%d, bom_pkg=%d, item=%d, prodstatus=%d",
        article, len(routing), len(bom_pkg), len(item), len(prod),
    )


def capture(config, mock_dir: Path, logger) -> None:
    """Entry point used by scripts/capture_mock_data.py."""
    mock_dir.mkdir(parents=True, exist_ok=True)

    jira = JiraClient(config)
    issues = _capture_search(jira, mock_dir, logger)

    sample_keys: list[str] = []
    sampled_full: list[dict] = []
    logger.info("Capturing %d sample container(s) + child WPs",
                min(SAMPLE_CONTAINER_COUNT, len(issues)))
    for issue in issues[:SAMPLE_CONTAINER_COUNT]:
        key = issue.get("key")
        if not key:
            continue
        full = _capture_issue(jira, key, mock_dir, logger)
        if full is None:
            continue
        sampled_full.append(full)
        sample_keys.append(key)
        _capture_wps(jira, key, mock_dir, logger)

    articles = _collect_articles(sampled_full)
    logger.info("Capturing M3 fixtures for %d article(s): %s",
                len(articles), ", ".join(articles) or "(none)")

    m3 = M3Client(config)
    try:
        for article in articles:
            _capture_m3_per_article(m3, article, mock_dir, logger)
    finally:
        m3.close()

    logger.info("mo_trigger_comment capture complete.")
