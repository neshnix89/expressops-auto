"""
Mock data capture for to_status_check.

Invoked by scripts/capture_mock_data.py (or `ops capture to_status_check`)
on the company laptop. Saves:

1. The JIRA search response for the active-containers JQL (filename must
   match what JiraClient.search() looks up in mock mode).
2. A handful of full issue responses with comments expanded, used to
   populate the TO-number extraction path.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.jira_client import JiraClient

from tasks.to_status_check.main import ACTIVE_CONTAINERS_JQL

SAMPLE_ISSUE_COUNT = 10


def _mock_search_filename(jql: str) -> str:
    """Mirror JiraClient.search()'s mock filename convention."""
    safe_name = re.sub(r"[^\w]", "_", jql)[:80]
    return f"search_{safe_name}.json"


def capture(config, mock_dir: Path, logger) -> None:
    jira = JiraClient(config)

    logger.info("Capturing active Work Container search: %s", ACTIVE_CONTAINERS_JQL)
    search_result = jira.search(
        ACTIVE_CONTAINERS_JQL,
        fields=["summary", "status"],
        max_results=200,
    )
    search_filename = _mock_search_filename(ACTIVE_CONTAINERS_JQL)
    jira.save_mock(search_result, search_filename, mock_dir)
    issues = search_result.get("issues", []) or []
    logger.info("  Saved %d containers -> %s", len(issues), search_filename)

    sample = issues[:SAMPLE_ISSUE_COUNT]
    logger.info("Capturing full issue + comments for %d sample containers", len(sample))
    for issue in sample:
        key = issue.get("key")
        if not key:
            continue
        try:
            full = jira.get_issue(key, expand="renderedFields")
            jira.save_mock(full, f"issue_{key}.json", mock_dir)
            comment_count = len(((full.get("fields") or {}).get("comment") or {}).get("comments") or [])
            logger.info("  %s: %d comments", key, comment_count)
        except Exception as exc:
            logger.error("  %s: failed to capture — %s", key, exc)
