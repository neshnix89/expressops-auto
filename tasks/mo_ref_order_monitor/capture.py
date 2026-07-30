"""
Capture real mock data for mo_ref_order_monitor (run on the company laptop).

Read-only: fetches the live container universe + comments and the M3 MO-header
rows for MOs found in comments, saving them under mock_data/ so the VPS can run
`--mock` against realistic data. No writes to any system.

Usage (company laptop, live systems):
    python -m tasks.mo_ref_order_monitor.capture
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.jira_client import JiraClient
from core.logger import get_logger
from core.m3 import M3Client

from tasks.mo_ref_order_monitor.main import DEFAULT_JQL, DEFAULT_MO_REGEX
from tasks.mo_ref_order_monitor.m3_mo import _SQL

MOCK_DIR = TASK_DIR / "mock_data"


def main() -> int:
    config = load_config(mode_override="live")
    log = get_logger("mo_ref_order_monitor_capture", config.log_dir, config.log_level)
    jira = JiraClient(config)
    m3 = M3Client(config)

    jql = config.get("mo_ref_order_monitor.jql", DEFAULT_JQL)
    mo_re = re.compile(config.get("mo_ref_order_monitor.mo_number_regex", DEFAULT_MO_REGEX))

    log.info("[capture] JIRA search (container universe)...")
    result = jira.search(jql, fields=["summary"])
    jira.save_mock(result, f"search_{re.sub(r'[^\\w]', '_', jql)[:80]}.json", MOCK_DIR)
    containers = result.get("issues", [])
    log.info("[capture] %d containers", len(containers))

    mos: set[str] = set()
    for c in containers:
        key = c.get("key")
        if not key:
            continue
        issue = jira.get_issue(key)
        jira.save_mock(issue, f"issue_{key}.json", MOCK_DIR)
        comments = (issue.get("fields", {}).get("comment", {}) or {}).get("comments", [])
        blob = "\n".join((cm.get("body") or "") for cm in comments)
        mos.update(mo_re.findall(blob))
    log.info("[capture] %d MO(s) found in comments", len(mos))

    sql = _SQL.format(schema=config.m3_schema)
    for mo in sorted(mos):
        rows = m3.query(sql, (mo,))
        m3.save_mock(rows, f"mo_header_{mo}.json", MOCK_DIR)
        log.info("[capture] M3 header saved: %s (%d row)", mo, len(rows))

    log.info("[capture] done -> %s", MOCK_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
