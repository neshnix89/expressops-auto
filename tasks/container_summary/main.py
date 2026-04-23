"""
container_summary — daily dashboard of active SMT PCBA Singapore
Work Containers.

Phase 1 (Python) extracts structured fields, WP roll-up, parking log,
keyword timeline, flags. Phase 2 (Opus 4.6, optional) adds a prose
narrative. Incremental cache skips unchanged containers between runs;
only containers whose JIRA `updated` timestamp or comment count has
changed get re-summarised.

Usage:
    python -m tasks.container_summary.main --mock
    python -m tasks.container_summary.main --live
    python -m tasks.container_summary.main --live --no-llm
    python -m tasks.container_summary.main --live --full-refresh
    python -m tasks.container_summary.main --live --dry-run
    python -m tasks.container_summary.main --live --key USRE-1234
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.confluence import ConfluenceClient
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger

from tasks.container_summary import cache as cache_mod
from tasks.container_summary import llm as llm_mod
from tasks.container_summary import logic

TASK_NAME = "container_summary"
PAGE_KEY = "container_summary"
MOCK_DIR = TASK_DIR / "mock_data"
CACHE_PATH = TASK_DIR / "last_run.json"

# Simple field-based filter. The nested relation() JQL from TASK.md
# fails via REST (HTTP 400 — nested double quotes break the JSON
# parser); this filter returns the same active container set and is
# confirmed working in mo_trigger_comment.
CONTAINERS_JQL = (
    'issuetype = "Work Container" '
    'AND "Product Type" = "SMT PCBA" '
    'AND "NPI Location" = "Singapore" '
    'AND resolution is EMPTY '
    'ORDER BY created ASC'
)

CONTAINER_FIELDS = [
    "summary", "status", "assignee", "reporter", "created", "updated",
    "description", "comment",
    "customfield_13300", "customfield_13700",
    "customfield_13903", "customfield_13904", "customfield_13905",
    "customfield_13906", "customfield_13907", "customfield_15009",
    "customfield_15400", "customfield_15800", "customfield_15805",
]

WP_FIELDS = ["summary", "status", "resolution", "assignee"]

SEARCH_MOCK_FILE = "search_containers.json"


# ── Fetch helpers ────────────────────────────────────────────────────


def fetch_containers(
    jira: JiraClient, logger, key_override: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch the active SG SMT PCBA containers.

    Mock mode bypasses `search_all()` because the nested relation() JQL
    produces an unwieldy auto-generated mock filename — instead, we read
    `mock_data/search_containers.json` (populated by capture.py).
    """
    if key_override:
        try:
            issue = jira.get_issue(key_override, expand="renderedFields")
        except FriendlyError as exc:
            logger.error("Failed to fetch %s: %s", key_override, exc.message)
            return []
        return [issue]

    if jira.config.is_mock:
        path = MOCK_DIR / SEARCH_MOCK_FILE
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            issues = (
                data.get("issues") if isinstance(data, dict) else data
            ) or []
            logger.info("Mock: loaded %d containers from %s",
                        len(issues), SEARCH_MOCK_FILE)
            return issues
        logger.info(
            "Mock: %s not found — falling back to issue_*.json fixtures",
            SEARCH_MOCK_FILE,
        )
        fallback: list[dict[str, Any]] = []
        for fp in sorted(MOCK_DIR.glob("issue_*.json")):
            with open(fp, "r", encoding="utf-8") as f:
                fallback.append(json.load(f))
        return fallback

    return jira.search_all(CONTAINERS_JQL, fields=CONTAINER_FIELDS)


def _mock_children(key: str) -> list[dict[str, Any]]:
    path = MOCK_DIR / f"children_{key}.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("issues") or []
    return [wp for wp in data if wp.get("key") != key]


def fetch_children_for(
    jira: JiraClient, key: str, logger,
) -> list[dict[str, Any]]:
    """Fetch child Work Packages for a container; filter out parent self-ref."""
    if jira.config.is_mock:
        return _mock_children(key)

    jql = (
        f'issue in relation("{key}", "Project Children", '
        "Tasks, Deviations, level1)"
    )
    try:
        children = jira.search_all(jql, fields=WP_FIELDS)
    except FriendlyError as exc:
        logger.warning("%s: child WP fetch failed — %s", key, exc.message)
        return []
    return [wp for wp in children if wp.get("key") != key]


def fetch_all_children(
    jira: JiraClient, keys: list[str], logger,
) -> dict[str, list[dict[str, Any]]]:
    """Parallel fetch of child WPs for every container."""
    result: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_children_for, jira, key, logger): key
            for key in keys
        }
        for future in futures:
            key = futures[future]
            try:
                result[key] = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s: child fetch raised %s: %s",
                               key, type(exc).__name__, exc)
                result[key] = []
    return result


# ── Narrative orchestration ──────────────────────────────────────────


def _run_llm(
    client: Any,
    issues_by_key: dict[str, dict[str, Any]],
    summaries: list[dict[str, Any]],
    cache: dict[str, Any],
    full_refresh: bool,
    logger,
) -> tuple[int, int, float]:
    """
    Populate each summary's `narrative`. Returns (processed, skipped, total_cost).
    """
    processed = 0
    skipped = 0
    total_cost = 0.0

    for summary in summaries:
        key = summary["key"]
        issue = issues_by_key.get(key, {})
        cache_entry = cache.get(key)

        if not full_refresh and not cache_mod.needs_update(cache_entry, issue):
            summary["narrative"] = (cache_entry or {}).get("narrative", "")
            skipped += 1
            continue

        comments = ((issue.get("fields") or {}).get("comment") or {}).get("comments") or []

        if full_refresh or not cache_entry:
            payload = llm_mod.build_full_payload(
                issue, summary["identity"], summary["wp_rollup"],
            )
        else:
            cached_count = cache_entry.get("last_comment_count", 0)
            new_comments = llm_mod.get_new_comments(comments, cached_count)
            payload = llm_mod.build_incremental_payload(
                issue,
                summary["identity"],
                cache_entry.get("narrative", ""),
                new_comments,
                starting_index=cached_count + 1,
                last_run_date=(cache_entry.get("generated_at", "") or "").split("T")[0],
            )

        narrative, usage = llm_mod.call_opus(client, payload, logger)
        summary["narrative"] = narrative
        if narrative:
            processed += 1
            total_cost += llm_mod.usage_cost(usage)
            logger.info("%s: Opus narrative generated (%d chars)",
                        key, len(narrative))
        else:
            logger.warning("%s: Opus call returned no narrative — Phase 1 only", key)

    return processed, skipped, round(total_cost, 4)


# ── Console output ───────────────────────────────────────────────────


def _print_console_table(summaries: list[dict[str, Any]]) -> None:
    """Compact per-container status table printed on every run."""
    print()
    print(f"{'Container':<14} {'Summary':<32} {'Status':<12} "
          f"{'WPs':<14} {'Age':>4}  Flags")
    for s in summaries:
        identity = s.get("identity", {})
        rollup = s.get("wp_rollup", {})
        staleness = s.get("staleness", {})
        key = (identity.get("key") or "")[:13]
        summary = (identity.get("summary") or "")[:31]
        status = (identity.get("status") or "")[:11]
        wp = (rollup.get("summary_line") or "")[:13]
        age = f"{staleness.get('age_wd', 0)}d"
        flags = ", ".join(s.get("flags") or [])
        print(f"{key:<14} {summary:<32} {status:<12} {wp:<14} {age:>4}  {flags}")


# ── Publish ──────────────────────────────────────────────────────────


def _publish(
    confluence: ConfluenceClient, page_id: int | str, html_body: str, logger,
) -> None:
    """Read current page, preserve title, write new HTML body."""
    current = confluence.get_page(page_id)
    title = current.get("title") or "Container Summary"
    confluence.update_page(page_id, title=title, html_body=html_body)
    logger.info("Confluence page %s updated", page_id)


# ── Main pipeline ────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    config = load_config(mode_override=args.mode)
    logger = get_logger(TASK_NAME, config.log_dir, config.log_level)
    logger.info(
        "run: %s mode (no_llm=%s, full_refresh=%s, dry_run=%s, key=%s)",
        config.mode, args.no_llm, args.full_refresh, args.dry_run, args.key,
    )

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    confluence = ConfluenceClient(config, mock_data_dir=MOCK_DIR)

    issues = fetch_containers(jira, logger, key_override=args.key)
    logger.info("Fetched %d container(s)", len(issues))
    if not issues:
        logger.info("Nothing to summarise")
        return 0

    issues_by_key = {i.get("key"): i for i in issues if i.get("key")}
    keys = list(issues_by_key.keys())

    children_map = fetch_all_children(jira, keys, logger)
    logger.info("Fetched children for %d container(s)", len(children_map))

    today = date.today()
    summaries: list[dict[str, Any]] = []
    for key in keys:
        issue = issues_by_key[key]
        children = children_map.get(key, [])
        try:
            summary = logic.summarise_container(issue, children, today)
        except Exception as exc:  # noqa: BLE001 — keep the batch going
            logger.exception("%s: summarise failed: %s", key, exc)
            continue
        summaries.append(summary)

    cache = cache_mod.load_cache(CACHE_PATH, logger)

    if not args.no_llm:
        try:
            client = llm_mod.create_client(config)
        except FriendlyError as exc:
            logger.error("LLM disabled — %s (%s)", exc.message, exc.hint)
            client = None
    else:
        client = None

    if client is not None:
        need_count = sum(
            1 for s in summaries
            if args.full_refresh or cache_mod.needs_update(cache.get(s["key"]), issues_by_key[s["key"]])
        )
        est = llm_mod.estimate_batch_cost(need_count, args.full_refresh)
        logger.info("LLM: %d container(s) will be (re)summarised — est. ~$%.2f",
                    need_count, est)
        processed, skipped, cost = _run_llm(
            client, issues_by_key, summaries, cache,
            args.full_refresh, logger,
        )
        logger.info("LLM: %d processed, %d skipped (unchanged) — actual $%.4f",
                    processed, skipped, cost)
    else:
        for summary in summaries:
            summary["narrative"] = (cache.get(summary["key"]) or {}).get("narrative", "")

    html_body = logic.build_confluence_html(summaries)
    _print_console_table(summaries)

    if args.dry_run:
        print("\n--- DRY RUN: Confluence HTML ---")
        print(html_body)
        return 0

    page_id = (config.pages or {}).get(PAGE_KEY)
    if not page_id:
        raise FriendlyError(
            f"no Confluence page ID configured for '{PAGE_KEY}'",
            f"create a page in space {config.confluence_space_key}, then add "
            f"`pages.{PAGE_KEY}: <id>` to config/config.yaml",
        )

    _publish(confluence, page_id, html_body, logger)

    # Upload the structured JSON as a page attachment so the
    # Tampermonkey overlay on JIRA can fetch a single container without
    # parsing the HTML. Runs only after the HTML publish succeeds; any
    # upload failure is logged but does not fail the whole run.
    try:
        export = logic.build_json_export(summaries)
        payload = json.dumps(export, indent=2, default=str).encode("utf-8")
        confluence.upload_attachment(
            page_id, "container_summary.json", payload,
        )
        logger.info("Uploaded container_summary.json attachment")
    except FriendlyError as exc:
        logger.warning(
            "JSON attachment upload failed: %s (%s)", exc.message, exc.hint,
        )

    # Only persist cache once the publish has succeeded — on failure,
    # the next run will re-summarise and retry.
    if client is not None:
        new_cache = dict(cache)
        for summary in summaries:
            key = summary["key"]
            issue = issues_by_key[key]
            narrative = summary.get("narrative") or ""
            if not narrative:
                # Preserve the previous entry rather than overwriting with
                # an empty narrative from a failed call.
                continue
            new_cache[key] = cache_mod.update_cache_entry(key, issue, narrative)

        live_keys = set(keys)
        for stale_key in list(new_cache.keys()):
            if stale_key not in live_keys and args.key is None:
                del new_cache[stale_key]

        cache_mod.save_cache(CACHE_PATH, new_cache)
        logger.info("Cache saved to %s", CACHE_PATH.name)

    return 0


# ── CLI ──────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tasks.container_summary.main",
        description=(
            "Build and publish a Confluence dashboard summarising all "
            "active SMT PCBA Singapore Work Containers."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--mock", action="store_const", const="mock", dest="mode",
        help="Read from mock_data/ (default)",
    )
    mode.add_argument(
        "--live", action="store_const", const="live", dest="mode",
        help="Hit live JIRA + Confluence + Anthropic (company laptop only)",
    )
    parser.set_defaults(mode="mock")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip all Opus calls. Phase 1 output only.",
    )
    parser.add_argument(
        "--full-refresh", action="store_true",
        help="Ignore cache and re-summarise every container.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build output, print HTML to console, do not publish.",
    )
    parser.add_argument(
        "--key", default=None, metavar="KEY",
        help="Process a single container by key (useful for debugging).",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        return run(args)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
