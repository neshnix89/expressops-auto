"""
Mock-data capture for container_summary.

Run on the company laptop (has live JIRA access); the captured fixtures
are then committed to the repo so the VPS can exercise the pipeline in
``--mock`` mode.

Writes:
    search_containers.json    Full nested-relation() search result
    issue_<KEY>.json          Full issue + renderedFields + comments
    children_<KEY>.json       Child Work Package list (level1 relation)

Errors on individual issues are logged and skipped — one bad container
won't abort the whole capture.
"""

from __future__ import annotations

from pathlib import Path

from core.jira_client import JiraClient

from tasks.container_summary.main import (
    CONTAINERS_JQL,
    CONTAINER_FIELDS,
    SEARCH_MOCK_FILE,
    WP_FIELDS,
)

ISSUE_SAMPLE_COUNT = 3
CHILDREN_SAMPLE_COUNT = 5


def _capture_search(jira: JiraClient, mock_dir: Path, logger) -> list[dict]:
    logger.info("Capturing container search (nested relation JQL)")
    result = jira.search(CONTAINERS_JQL, fields=CONTAINER_FIELDS, max_results=500)
    jira.save_mock(result, SEARCH_MOCK_FILE, mock_dir)
    issues = result.get("issues", []) or []
    logger.info("  Saved %d container(s) — %s", len(issues), SEARCH_MOCK_FILE)
    return issues


def _capture_issue(jira: JiraClient, key: str, mock_dir: Path, logger) -> None:
    try:
        full = jira.get_issue(key, expand="renderedFields")
    except Exception as exc:  # noqa: BLE001
        logger.error("  %s: issue fetch failed — %s", key, exc)
        return
    jira.save_mock(full, f"issue_{key}.json", mock_dir)
    fields = full.get("fields") or {}
    n_comments = len(((fields.get("comment") or {}).get("comments")) or [])
    logger.info("  %s: issue saved (comments=%d)", key, n_comments)


def _capture_children(jira: JiraClient, key: str, mock_dir: Path, logger) -> None:
    jql = (
        f'issue in relation("{key}", "Project Children", '
        "Tasks, Deviations, level1)"
    )
    try:
        result = jira.search(jql, fields=WP_FIELDS, max_results=200)
    except Exception as exc:  # noqa: BLE001
        logger.error("  %s: child fetch failed — %s", key, exc)
        return
    issues = result.get("issues") if isinstance(result, dict) else result
    jira.save_mock(issues or [], f"children_{key}.json", mock_dir)
    logger.info("  %s: %d child WP row(s) saved", key, len(issues or []))


def capture(config, mock_dir: Path, logger) -> None:
    """Entry point used by scripts/capture_mock_data.py."""
    mock_dir.mkdir(parents=True, exist_ok=True)

    jira = JiraClient(config)
    issues = _capture_search(jira, mock_dir, logger)

    keys = [i.get("key") for i in issues if i.get("key")]
    logger.info(
        "Capturing %d issue fixture(s) + %d children fixture(s)",
        min(ISSUE_SAMPLE_COUNT, len(keys)),
        min(CHILDREN_SAMPLE_COUNT, len(keys)),
    )

    for key in keys[:ISSUE_SAMPLE_COUNT]:
        _capture_issue(jira, key, mock_dir, logger)
    for key in keys[:CHILDREN_SAMPLE_COUNT]:
        _capture_children(jira, key, mock_dir, logger)

    logger.info("container_summary capture complete.")
