"""
costing_hs_code_trigger — scan containers, trigger Costing/HS-Code updates,
and chase reminders until everyone has replied "Done".

Per run, for each in-scope Work Container we decide one of:
  * TRIGGER  — post the initial comment tagging the 2 costing people + the
               HS Code person (fires when the WP gate is met, or immediately
               for DMR containers).
  * REMIND   — re-tag only the people still outstanding (>= N working days
               since the last nudge).
  * NOOP     — not ready / not yet due / already complete.

Decision logic lives in ``logic.py`` (pure, unit-tested); this module owns all
JIRA I/O. Mock mode reads ``mock_data/`` and never posts. Live mode posts
comments — that is a JIRA write, so it is gated behind ``--live`` and honours
``--dry-run``.

Usage:
    python -m tasks.costing_hs_code_trigger.main --mock
    python -m tasks.costing_hs_code_trigger.main --live
    python -m tasks.costing_hs_code_trigger.main --live --dry-run
    python -m tasks.costing_hs_code_trigger.main --mock --today 2026-07-20
    python -m tasks.costing_hs_code_trigger.main --mock --only NPIOTHER-4566
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger

from tasks.costing_hs_code_trigger.logic import (
    ACTION_NOOP,
    ACTION_REMIND,
    ACTION_TRIGGER,
    Decision,
    apply_baseline,
    build_people,
    decide,
)

TASK_NAME = "costing_hs_code_trigger"
MOCK_DIR = TASK_DIR / "mock_data"

# Go-live baseline: keys of containers that were ALREADY ready when the
# automation was first switched on. They are skipped forever so the operator
# never retroactively nags an existing backlog. Lives under the gitignored
# outputs/ dir so it persists across `sync_now.bat` pulls (which keep outputs).
BASELINE_PATH = PROJECT_ROOT / "outputs" / f"{TASK_NAME}_baseline.json"

# Same scope the other SG SMT PCBA tasks use — a single source of truth for
# "which containers are in play".
ACTIVE_CONTAINERS_JQL = (
    'issuetype = "Work Container" '
    'AND "Product Type" = "SMT PCBA" '
    'AND "NPI Location" = "Singapore" '
    'AND resolution is EMPTY '
    'ORDER BY created ASC'
)

# Child WP fields needed for the readiness gate.
_WP_FIELDS = ["summary", "status", "resolution", "assignee"]

# Issue fields needed to decide: summary, Order Type, and the comment stream.
_ISSUE_FIELDS = ["summary", "customfield_13905", "comment"]


# ── JIRA fetch helpers ───────────────────────────────────────────────


def _load_mock_container_keys(logger) -> list[str]:
    """
    Mock container list comes from ``mock_data/containers.json`` (a JSON list
    of keys, or a search-result dict with an ``issues`` array) — this sidesteps
    the sanitised-JQL filename convention and keeps fixtures obvious.
    """
    path = MOCK_DIR / "containers.json"
    if not path.exists():
        logger.warning("mock: no containers.json — nothing to scan")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [i.get("key") for i in data.get("issues", []) if i.get("key")]
    if isinstance(data, list):
        return [x.get("key") if isinstance(x, dict) else x for x in data if x]
    return []


def fetch_container_keys(jira: JiraClient, logger) -> list[str]:
    if jira.config.is_mock:
        keys = _load_mock_container_keys(logger)
        logger.info("mock: %d container(s) to evaluate", len(keys))
        return keys
    result = jira.search(
        ACTIVE_CONTAINERS_JQL, fields=["summary", "status"], max_results=500,
    )
    issues = result.get("issues", []) or []
    keys = [i.get("key") for i in issues if i.get("key")]
    logger.info("JIRA: %d open SG SMT PCBA container(s)", len(keys))
    return keys


def _load_mock_wps(container_key: str, logger) -> list[dict[str, Any]]:
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
    """Child Work Packages via a level1 relation() search (direct mock read)."""
    if jira.config.is_mock:
        return _load_mock_wps(container_key, logger)
    jql = f'issue in relation("{container_key}", "Project Children", level1)'
    result = jira.search(jql, fields=_WP_FIELDS, max_results=100)
    return result.get("issues", []) or []


def resolve_container(
    jira: JiraClient, key: str, logger,
) -> dict[str, Any] | None:
    """Fetch a container with its comment stream. None when mock is missing."""
    if jira.config.is_mock and not (MOCK_DIR / f"issue_{key}.json").exists():
        logger.debug("Skipping %s — no issue_%s.json", key, key)
        return None
    try:
        return jira.get_issue(key)
    except FriendlyError as exc:
        logger.warning("Failed to fetch %s: %s", key, exc.message)
        return None


# ── Orchestration ────────────────────────────────────────────────────


def load_baseline(logger) -> set[str]:
    """Read the go-live baseline key set (empty when the file is absent)."""
    if not BASELINE_PATH.exists():
        return set()
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("could not read baseline %s: %s", BASELINE_PATH, exc)
        return set()
    keys = data.get("keys", []) if isinstance(data, dict) else data
    return {str(k) for k in keys if k}


def save_baseline(keys: set[str], logger) -> None:
    """Persist the baseline key set to the gitignored outputs/ dir."""
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump({"keys": sorted(keys)}, f, indent=2)
    logger.info("baseline saved: %d key(s) -> %s", len(keys), BASELINE_PATH)


def _parse_today(value: str | None) -> date:
    if not value:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise FriendlyError(
            f"invalid --today value: {value!r}",
            "use ISO format, e.g. --today 2026-07-20",
        ) from exc


def _warn_blank_usernames(people, logger) -> None:
    blanks = [p.label for p in people if not p.username]
    if blanks:
        logger.warning(
            "%d tagged person/people have no JIRA username in config (%s) — "
            "they cannot be @mentioned or auto-detected as done; the reminder "
            "loop will not converge for them until usernames are filled in.",
            len(blanks), ", ".join(blanks),
        )


def _post(
    jira: JiraClient, key: str, body: str, dry_run: bool, logger,
) -> bool:
    """Post a comment unless mock/dry-run. Returns True if actually posted."""
    if dry_run:
        logger.info("%s: [dry-run] would post comment (%d chars)", key, len(body))
        print(f"--- dry-run comment for {key} ---\n{body}\n")
        return False
    if jira.config.is_mock:
        logger.info("%s: [mock] would post comment (%d chars)", key, len(body))
        print(f"--- mock comment for {key} ---\n{body}\n")
        return False
    try:
        jira.add_comment(key, body)
    except FriendlyError as exc:
        logger.error("%s: failed to post comment: %s", key, exc.message)
        return False
    logger.info("%s: posted comment", key)
    return True


def run(
    mode: str,
    dry_run: bool,
    today_str: str | None,
    only: str | None,
    seed_baseline: bool = False,
) -> int:
    logger = get_logger(TASK_NAME)
    config = load_config(mode_override=mode)
    today = _parse_today(today_str)
    logger.info(
        "run: %s mode (dry_run=%s, today=%s, seed_baseline=%s%s)",
        config.mode, dry_run, today, seed_baseline,
        f", only={only}" if only else "",
    )

    task_config = config.get(TASK_NAME) or {}
    if not task_config:
        logger.warning(
            "No `%s` section in config — using built-in defaults; "
            "people lists are empty so nothing will be tagged.", TASK_NAME,
        )

    # Append the appropriate marker footer to every comment we post so future
    # runs can recognise our own trigger/reminder comments.
    trigger_marker = str(task_config.get("trigger_marker") or "").strip()
    reminder_marker = str(task_config.get("reminder_marker") or "").strip()

    people = build_people(task_config)
    if not seed_baseline:
        _warn_blank_usernames(people, logger)

    # Normal runs honour the go-live baseline; seeding builds it.
    baseline = set() if seed_baseline else load_baseline(logger)
    if not seed_baseline:
        logger.info("baseline: %d container(s) will be skipped", len(baseline))

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    keys = fetch_container_keys(jira, logger)
    if only:
        keys = [k for k in keys if k == only]
        if not keys:
            logger.warning("--only %s not present in scanned containers", only)

    if seed_baseline:
        return _run_seed(
            jira, keys, people, task_config, today, logger,
        )

    counts = {"trigger": 0, "remind": 0, "waiting": 0,
              "complete": 0, "not_ready": 0}
    rows: list[tuple[str, Decision, bool]] = []

    for key in keys:
        issue = resolve_container(jira, key, logger)
        if issue is None:
            continue
        wps = fetch_child_wps(jira, key, logger)

        decision = decide(
            issue=issue, wps=wps, people=people,
            task_config=task_config, today=today,
        )
        # Suppress the initial trigger for pre-existing backlog containers.
        decision = apply_baseline(decision, baseline)
        counts[decision.state] = counts.get(decision.state, 0) + 1

        posted = False
        if decision.action in (ACTION_TRIGGER, ACTION_REMIND) and decision.body:
            marker = trigger_marker if decision.action == ACTION_TRIGGER else reminder_marker
            body = f"{decision.body}\n\n{marker}" if marker else decision.body
            posted = _post(jira, key, body, dry_run, logger)
        rows.append((key, decision, posted))

    _print_summary(rows, counts, dry_run, today)
    return 0


def _run_seed(
    jira: JiraClient,
    keys: list[str],
    people,
    task_config: dict[str, Any],
    today: date,
    logger,
) -> int:
    """
    Seed the go-live baseline: record every container that WOULD trigger right
    now (so it is never nagged) and post nothing. Unions with any existing
    baseline so re-running is safe. This is the one-time switch-on step.
    """
    existing = load_baseline(logger)
    would_trigger: set[str] = set()

    for key in keys:
        issue = resolve_container(jira, key, logger)
        if issue is None:
            continue
        wps = fetch_child_wps(jira, key, logger)
        decision = decide(
            issue=issue, wps=wps, people=people,
            task_config=task_config, today=today,
        )
        if decision.action == ACTION_TRIGGER:
            would_trigger.add(key)

    merged = existing | would_trigger
    save_baseline(merged, logger)

    new_count = len(would_trigger - existing)
    print("=" * 78)
    print(f"Baseline seeded — {len(would_trigger)} container(s) currently ready "
          f"marked as backlog (skip forever).")
    print(f"  new this run: {new_count}   total in baseline: {len(merged)}")
    print(f"  file: {BASELINE_PATH}")
    print("These containers will NOT be triggered. Only containers that become "
          "ready from now on will be.")
    return 0


def _print_summary(
    rows: list[tuple[str, Decision, bool]],
    counts: dict[str, int],
    dry_run: bool,
    today: date,
) -> None:
    print("=" * 78)
    print(f"costing_hs_code_trigger — {today} — {len(rows)} container(s) scanned")
    print("-" * 78)
    for key, d, posted in rows:
        tag = "DMR" if d.is_dmr else "   "
        flag = "POSTED" if posted else ("would" if d.action != ACTION_NOOP else "-")
        detail = d.reason
        if d.outstanding and d.state in ("waiting", "remind", "trigger"):
            detail = f"{detail} | outstanding: {', '.join(d.outstanding)}"
        print(f"  {key:<16} {tag}  {d.state:<10} {flag:<7} {detail}")
    print("-" * 78)
    summary = "  ".join(f"{k}={v}" for k, v in counts.items() if v)
    print(f"Summary: {summary or 'nothing to do'}"
          f"{'   (dry-run: nothing posted)' if dry_run else ''}")


# ── CLI ──────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tasks.costing_hs_code_trigger.main",
        description=(
            "Trigger Costing/HS-Code update requests on JIRA containers and "
            "chase reminders until everyone replies Done."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mock", action="store_const", const="mock", dest="mode",
        help="Read from mock_data/ and never post (default)",
    )
    group.add_argument(
        "--live", action="store_const", const="live", dest="mode",
        help="Hit live JIRA and post comments (company laptop only)",
    )
    parser.set_defaults(mode="mock")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Decide and print comments, but post nothing.",
    )
    parser.add_argument(
        "--today", metavar="YYYY-MM-DD", default=None,
        help="Override 'today' for reminder-timing (testing/backfill).",
    )
    parser.add_argument(
        "--only", metavar="KEY", default=None,
        help="Evaluate a single container key only.",
    )
    parser.add_argument(
        "--seed-baseline", action="store_true",
        help="One-time switch-on: record every currently-ready container as "
             "backlog to be skipped forever, and post nothing. Run this once "
             "(with --live) so pre-existing containers are never retroactively "
             "nagged; afterwards only newly-ready containers trigger.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        return run(
            mode=args.mode,
            dry_run=args.dry_run,
            today_str=args.today,
            only=args.only,
            seed_baseline=args.seed_baseline,
        )
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
