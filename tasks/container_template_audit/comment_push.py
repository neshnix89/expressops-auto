"""
Post a staged audit comment from Confluence to a specific JIRA container.

Reads the draft comment from Confluence page 592255806, checks for the
duplicate guard marker, then posts to JIRA if clear.

Usage:
    python comment_push.py NPIOTHER-123
    python comment_push.py NPIOTHER-123 --dry-run
    python comment_push.py NPIOTHER-123 --live
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.confluence import ConfluenceClient
from core.jira_client import JiraClient
from core.logger import get_logger

from tasks.container_template_audit.batch import (
    AUDIT_PAGE_ID,
    GUARD_MARKER,
    MOCK_DIR,
    parse_existing_page,
)

TASK_NAME = "container_template_audit.comment_push"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="comment_push.py",
        description="Post a staged audit draft comment from Confluence to JIRA.",
    )
    parser.add_argument("key", help="JIRA container key, e.g. NPIOTHER-123")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the comment but do not post to JIRA.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--mock", action="store_const", const="mock", dest="mode",
        help="Use mock data (default)",
    )
    mode_group.add_argument(
        "--live", action="store_const", const="live", dest="mode",
        help="Hit live JIRA + Confluence (company laptop only)",
    )
    parser.set_defaults(mode="mock")
    args = parser.parse_args()

    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=args.mode)
    logger.info("comment_push: %s mode (key=%s, dry_run=%s)", config.mode, args.key, args.dry_run)

    confluence = ConfluenceClient(config, mock_data_dir=MOCK_DIR)
    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    # 1. Fetch Confluence page and parse staged drafts
    try:
        existing_html = confluence.get_page_html(AUDIT_PAGE_ID)
    except Exception as exc:
        print(f"ERROR: Could not read Confluence page {AUDIT_PAGE_ID}: {exc}")
        return 1

    draft_comments, _ = parse_existing_page(existing_html)

    # 2. Find draft for this key
    draft = draft_comments.get(args.key, "")
    if not draft:
        print(
            f"{args.key}: no staged draft found on Confluence page {AUDIT_PAGE_ID}.\n"
            f"Run `batch.py scan --live` first to populate the audit dashboard."
        )
        return 1

    if draft.strip() == "Already posted":
        print(f"{args.key}: comment is shown as 'Already posted' on Confluence — nothing to do.")
        return 0

    if not draft.strip():
        print(f"{args.key}: draft comment cell is empty — nothing to post.")
        return 1

    # 3. Check JIRA for duplicate guard marker
    try:
        issue = jira.get_issue(args.key)
    except Exception as exc:
        print(f"{args.key}: could not fetch from JIRA: {exc}")
        return 1

    comments = (
        ((issue.get("fields") or {}).get("comment") or {}).get("comments") or []
    )
    if any(GUARD_MARKER in (c.get("body") or "") for c in comments):
        print(f"{args.key}: {GUARD_MARKER!r} already exists in JIRA comments — skipping.")
        return 0

    # 4. Dry-run or post
    if args.dry_run:
        print(f"--- dry-run comment for {args.key} ---")
        print(draft)
        return 0

    if config.is_mock:
        print(f"{args.key}: [mock] would post audit comment ({len(draft)} chars)")
        return 0

    try:
        jira.add_comment(args.key, draft)
    except Exception as exc:
        print(f"{args.key}: failed to post comment: {exc}")
        return 1

    logger.info("%s: posted audit comment", args.key)
    print(f"{args.key}: comment posted successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
