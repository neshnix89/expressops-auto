"""
Mock-data capture for costing_hs_code_trigger.

Run on the company laptop (via `scripts/capture_mock_data.py` or
`ops capture costing_hs_code_trigger`) to save the fixtures the decision
engine reads:

    containers.json      JIRA: in-scope container list ({issues:[...]})
    issue_<KEY>.json     JIRA: full issue incl. Order Type + comment stream
    wps_<KEY>.json       JIRA: child Work Packages (level1)

No M3 / Confluence — this task is JIRA-only.
"""

from __future__ import annotations

from pathlib import Path

from core.jira_client import JiraClient

from tasks.costing_hs_code_trigger.main import (
    ACTIVE_CONTAINERS_JQL,
    _ISSUE_FIELDS,
    _WP_FIELDS,
)

SAMPLE_CONTAINER_COUNT = 8


def _capture_containers(jira: JiraClient, mock_dir: Path, logger) -> list[dict]:
    logger.info("Capturing in-scope container list")
    result = jira.search(
        ACTIVE_CONTAINERS_JQL, fields=["summary", "status"], max_results=500,
    )
    jira.save_mock(result, "containers.json", mock_dir)
    issues = result.get("issues", []) or []
    logger.info("  Saved %d container(s) — containers.json", len(issues))
    return issues


def _capture_issue(jira: JiraClient, key: str, mock_dir: Path, logger) -> None:
    try:
        full = jira.get_issue(key)
    except Exception as exc:  # noqa: BLE001 — capture is best-effort
        logger.error("  %s: issue fetch failed — %s", key, exc)
        return
    jira.save_mock(full, f"issue_{key}.json", mock_dir)
    fields = full.get("fields") or {}
    n_comments = len(((fields.get("comment") or {}).get("comments")) or [])
    logger.info("  %s: comments=%d", key, n_comments)


def _capture_wps(jira: JiraClient, key: str, mock_dir: Path, logger) -> None:
    jql = f'issue in relation("{key}", "Project Children", level1)'
    try:
        result = jira.search(jql, fields=_WP_FIELDS, max_results=100)
    except Exception as exc:  # noqa: BLE001
        logger.error("  %s: WP search failed — %s", key, exc)
        return
    jira.save_mock(result, f"wps_{key}.json", mock_dir)
    logger.info("  %s: %d child WP(s)", key, len(result.get("issues", []) or []))


def capture(config, mock_dir: Path, logger) -> None:
    """Entry point used by scripts/capture_mock_data.py."""
    mock_dir.mkdir(parents=True, exist_ok=True)
    jira = JiraClient(config)

    issues = _capture_containers(jira, mock_dir, logger)
    logger.info(
        "Capturing %d sample container(s) + child WPs",
        min(SAMPLE_CONTAINER_COUNT, len(issues)),
    )
    for issue in issues[:SAMPLE_CONTAINER_COUNT]:
        key = issue.get("key")
        if not key:
            continue
        _capture_issue(jira, key, mock_dir, logger)
        _capture_wps(jira, key, mock_dir, logger)

    logger.info("costing_hs_code_trigger capture complete.")
