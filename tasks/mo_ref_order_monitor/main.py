"""
mo_ref_order_monitor — poll M3 'Ref order no' (VHRORN) per active MO and keep
the JIRA Work Container's MO BUILD STATUS table + working-hours dwell summary
up to date, with Webex notifications on stage changes.

Per run (intended cadence ~15 min via Task Scheduler):
  1. Build the watch-list: scan open SG SMT PCBA container comments for MO
     numbers, plus any MO already tracked in state (until its container closes).
  2. For each MO: if its container is closed -> abandon. Else poll M3, advance
     the lifecycle (logic.apply_observation), and on a publish action rewrite
     the MO's section in the container description. Webex fires on stage change.

Decision logic is in logic.py (pure, verified). This module owns all I/O.
Mock mode reads mock_data/ and never writes. Live mode PUTs the container
description — a JIRA write, gated behind --live and honouring --dry-run.

Usage:
    python -m tasks.mo_ref_order_monitor.main --mock
    python -m tasks.mo_ref_order_monitor.main --live
    python -m tasks.mo_ref_order_monitor.main --live --dry-run
    python -m tasks.mo_ref_order_monitor.main --mock --only 7003904788
    python -m tasks.mo_ref_order_monitor.main --mock --now "2026-07-20 11:00"
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger
from core.calendar import fmt_hours
from core.m3 import M3Client

from tasks.mo_ref_order_monitor import state as state_store
from tasks.mo_ref_order_monitor.logic import (
    apply_observation, has_section, new_state, upsert_mo_section,
)
from tasks.mo_ref_order_monitor.m3_mo import fetch_mo_header
from tasks.mo_ref_order_monitor.webex import WebexNotifier

TASK_NAME = "mo_ref_order_monitor"
MOCK_DIR = TASK_DIR / "mock_data"

DEFAULT_JQL = (
    'issue in relation("filter=25423", "Project Parent", Tasks, Deviations, '
    'level1) AND "Product Type" = "SMT PCBA" AND "NPI Location" = "Singapore" '
    'ORDER BY created ASC'
)
DEFAULT_MO_REGEX = r"\b(70\d{8})\b"


# ── watch-list discovery ─────────────────────────────────────────────
def discover_mo_container_map(jira: JiraClient, jql: str, mo_re: re.Pattern,
                              log) -> tuple[dict[str, str], set[str]]:
    """
    Scan container comments for MO numbers.

    Returns ({mo_no: container_key}, {container keys in JQL scope}) — the scope
    set lets the caller explain why a requested container produced no MO.
    """
    result = jira.search(jql, fields=["summary"])
    containers = result.get("issues", [])
    log.info("watch-list: %d containers in scope", len(containers))
    mo_map: dict[str, str] = {}
    in_scope: set[str] = set()
    for c in containers:
        key = c.get("key")
        if not key:
            continue
        in_scope.add(key.upper())
        issue = jira.get_issue(key)
        comments = (issue.get("fields", {}).get("comment", {}) or {}).get("comments", [])
        blob = "\n".join((cm.get("body") or "") for cm in comments)
        for mo in mo_re.findall(blob):
            mo_map.setdefault(mo, key)  # first container wins
    log.info("watch-list: %d MO(s) found in comments", len(mo_map))
    return mo_map, in_scope


def container_is_closed(jira: JiraClient, key: str) -> bool:
    issue = jira.get_issue(key)
    return bool(issue.get("fields", {}).get("resolution"))


DEFAULT_WEBEX_TEMPLATES = {
    "issue_raised": ("🔴 **ISSUE — MO {mo}** · {pn}\n"
                     "Ref order no: **{marker}**\n"
                     "Container: [{container}]({url})"),
    "issue_cleared": ("🟢 **RESOLVED — MO {mo}** · {pn}\n"
                      "Ref order no: **{marker}**\n"
                      "Issue lasted **{issue_dwell}**\n"
                      "Container: [{container}]({url})"),
    "reopen": ("🟠 **MO {mo}** · {pn} — **RE-OPENED** (Sts {status})\n"
               "Ref order no: **{marker}**\n"
               "Container: [{container}]({url})"),
    "closed": ("✅ **MO {mo}** · {pn} — **CLOSED** (Sts {status})\n"
               "Total build time: **{total_dwell}**\n"
               "Container: [{container}]({url})"),
}


# Typed into the compose box, markdown syntax is unreliable — these plain-text
# variants are used for the "desktop" transport unless overridden in config.
DESKTOP_WEBEX_TEMPLATES = {
    "issue_raised": "🔴 ISSUE — MO {mo} ({pn}) — Ref order no: {marker} — {container} {url}",
    "issue_cleared": ("🟢 RESOLVED — MO {mo} ({pn}) — Ref order no: {marker} — "
                      "issue lasted {issue_dwell} — {container} {url}"),
    "reopen": "🟠 MO {mo} ({pn}) RE-OPENED (Sts {status}) — Ref order no: {marker} — {container} {url}",
    "closed": "✅ MO {mo} ({pn}) CLOSED (Sts {status}) — total build time {total_dwell} — {container} {url}",
}


def webex_message(reason: str, state: dict, templates: dict, base_url: str,
                  transport: str = "desktop") -> str:
    """Render the Webex notification for one action."""
    history = state.get("history", [])
    last = history[-1] if history else None
    grand: dict[str, float] = {}
    for h in history:
        for day, sec in (h.get("by_day") or {}).items():
            grand[day] = grand.get(day, 0.0) + sec
    container = state.get("container_key", "")

    fields = {
        "mo": state["mo_no"],
        "pn": state.get("pn", "") or "—",
        "marker": state.get("current_marker", "") or "—",
        "status": state.get("last_status", ""),
        "container": container,
        "url": f"{base_url.rstrip('/')}/browse/{container}",
        "order_type": state.get("order_type", ""),
        "responsible": state.get("responsible", ""),
        "prev_marker": (last or {}).get("marker", "—"),
        "prev_dwell": (f"{len((last or {}).get('by_day', {}))}d, "
                       f"{fmt_hours((last or {}).get('work_seconds', 0))}"
                       if last else "—"),
        "total_dwell": f"{len(grand)}d, {fmt_hours(sum(grand.values()))}" if grand else "—",
        # On issue_cleared, `last` is the IS-flagged value that just ended, so
        # its dwell is how long the issue was open.
        "issue_dwell": (f"{len((last or {}).get('by_day', {}))}d, "
                        f"{fmt_hours((last or {}).get('work_seconds', 0))}"
                        if last else "—"),
    }
    fallback = (DESKTOP_WEBEX_TEMPLATES if transport == "desktop"
                else DEFAULT_WEBEX_TEMPLATES)
    tpl = templates.get(reason) or fallback.get(reason) or "{mo} {marker}"
    try:
        return tpl.format(**fields)
    except KeyError as exc:
        return f"MO {fields['mo']} {fields['marker']} (bad template placeholder {exc})"


# ── main ─────────────────────────────────────────────────────────────
def run(args: argparse.Namespace) -> int:
    mode = "live" if args.live else "mock"
    config = load_config(mode_override=mode)
    log = get_logger(TASK_NAME, config.log_dir, config.log_level)

    now = (datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now
           else datetime.now())
    jql = config.get("mo_ref_order_monitor.jql", DEFAULT_JQL)
    mo_re = re.compile(config.get("mo_ref_order_monitor.mo_number_regex", DEFAULT_MO_REGEX))
    username = config.get("mo_ref_order_monitor.username", "ExpressOPS MO Monitor")
    state_dir = PROJECT_ROOT / config.get(
        "mo_ref_order_monitor.state_dir", f"outputs/{TASK_NAME}_state")

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    m3 = M3Client(config, mock_data_dir=MOCK_DIR)
    webex_templates = config.get("mo_ref_order_monitor.webex.messages", {}) or {}
    issue_regex = config.get("mo_ref_order_monitor.issue_regex") or None
    webex = WebexNotifier(
        enabled=bool(config.get("mo_ref_order_monitor.webex.enabled", False)),
        logger=log,
        transport=config.get("mo_ref_order_monitor.webex.transport", "desktop"),
        queue_file=PROJECT_ROOT / config.get(
            "mo_ref_order_monitor.webex.queue_file",
            f"outputs/{TASK_NAME}_webex_queue.json"),
        space_link=config.get("mo_ref_order_monitor.webex.space_link", ""),
        open_delay=config.get("mo_ref_order_monitor.webex.open_delay_seconds", 6),
        type_delay=config.get("mo_ref_order_monitor.webex.type_delay_seconds", 2),
        max_age_hours=config.get("mo_ref_order_monitor.webex.max_age_hours", 12),
        webhook_url=config.get("mo_ref_order_monitor.webex.webhook_url", ""),
        token=config.get("webex.bot_token", ""),
        default_room=config.get("mo_ref_order_monitor.webex.default_room_id", ""),
        routing=config.get("mo_ref_order_monitor.webex.routing", {}) or {},
        dry_run=args.dry_run,
    )

    # Standalone transport test — send one message and exit (no JIRA/M3 work).
    if args.test_webex:
        webex.notify("TEST", args.test_webex)
        sent, pending = webex.flush()
        log.info("test-webex: sent=%d pending=%d", sent, pending)
        return 0 if sent else 1

    writes_disabled = config.is_mock or args.dry_run
    log.info("mode=%s dry_run=%s now=%s", mode, args.dry_run, now.isoformat())

    # --map MO=KEY forces an explicit MO->container pairing and SKIPS the
    # comment-scan discovery entirely. Use it to test a specific MO in a
    # specific container without relying on the MO number being present in that
    # container's comments.
    if args.map:
        mo_map = {}
        in_scope = set()
        for pair in args.map:
            if "=" not in pair:
                log.error("--map expects MO=CONTAINER, got %r", pair)
                return 2
            mo_no, key = (p.strip() for p in pair.split("=", 1))
            mo_map[mo_no] = key.upper()
        log.info("forced map (comment scan skipped): %s",
                 ", ".join(f"{m}->{k}" for m, k in mo_map.items()))
    else:
        # Build the watch-list: comment-scan + MOs already tracked in state.
        mo_map, in_scope = discover_mo_container_map(jira, jql, mo_re, log)
        for st in state_store.all_states(state_dir):
            if not st.get("abandoned") and st.get("container_key"):
                mo_map.setdefault(st["mo_no"], st["container_key"])
    if args.only:
        mo_map = {m: k for m, k in mo_map.items() if m == args.only}
    if args.container:
        allowed = {c.strip().upper() for c in args.container.split(",") if c.strip()}
        mo_map = {m: k for m, k in mo_map.items() if k.upper() in allowed}
        log.info("pilot: restricted to container(s) %s -> %d MO(s)",
                 ", ".join(sorted(allowed)), len(mo_map))
        # Diagnose silent misses: a requested container with no MO either fell
        # outside the JQL scope or has no MO number in its comments.
        matched = {k.upper() for k in mo_map.values()}
        for key in sorted(allowed - matched):
            log.warning("pilot: %s produced no MO — %s", key,
                        "no MO number found in its comments" if key in in_scope
                        else "NOT in JQL scope (check Product Type / NPI Location)")

    # --reset wipes persisted state for the MOs in scope, so a test starts from
    # a clean table. Guarded to the current (already-filtered) mo_map, never the
    # whole store. Refused without an explicit MO selector to avoid surprises.
    if args.reset:
        if not (args.map or args.only or args.container):
            log.error("--reset requires --map / --only / --container to scope it")
            return 2
        for mo_no in mo_map:
            if state_store.delete_state(state_dir, mo_no):
                log.warning("reset: cleared persisted state for MO %s", mo_no)

    published = webex_sent = abandoned = skipped = 0

    for mo_no, container_key in sorted(mo_map.items()):
        state = state_store.load_state(state_dir, mo_no) or new_state(mo_no)
        state["container_key"] = container_key

        # Abandon once the container closes.
        if container_is_closed(jira, container_key):
            if not state.get("abandoned"):
                state["abandoned"] = True
                if not args.dry_run:
                    state_store.save_state(state_dir, state)
                abandoned += 1
                log.info("MO %s: container %s closed -> abandon", mo_no, container_key)
            continue
        if state.get("abandoned"):
            continue

        obs = fetch_mo_header(m3, mo_no, now)
        if obs is None:
            log.warning("MO %s: not found in M3 (skip)", mo_no)
            skipped += 1
            continue

        actions = apply_observation(state, obs, issue_regex=issue_regex)
        do_publish = any(a.kind == "publish" for a in actions)
        reasons = ",".join(a.reason for a in actions) or "no-op"
        # VHLMDT/VHCHNO expose the ODS replica's freshness: if a P1 change isn't
        # reflected here, MWOHED_AP simply hasn't synced yet (not a bug).
        log.info("MO %s: marker=%r sts=%s -> %s  [M3 lastmod=%s chg#=%s by=%s]",
                 mo_no, obs.marker, obs.status, reasons,
                 obs.last_modified or "?", obs.change_no or "?", obs.changed_by or "?")

        # Self-heal: during the phase-out the legacy Excel->Jira tool also PUTs
        # the whole description, so a concurrent run can drop our section. If we
        # have published before but the section is gone, re-publish it.
        issue = desc = None
        if not do_publish and state.get("days"):
            issue = jira.get_issue(container_key)
            desc = issue.get("fields", {}).get("description", "") or ""
            if not has_section(desc, mo_no):
                log.warning("MO %s: tracking section missing from %s — restoring",
                            mo_no, container_key)
                do_publish = True

        if do_publish:
            if desc is None:
                issue = jira.get_issue(container_key)
                desc = issue.get("fields", {}).get("description", "") or ""
            new_desc = upsert_mo_section(desc, state, username,
                                         now.strftime("%Y-%m-%d %H:%M:%S"))
            if writes_disabled:
                log.info("MO %s: [no-write] would update %s description (%d chars)",
                         mo_no, container_key, len(new_desc))
            else:
                jira.update_fields(container_key, {"description": new_desc})
                log.info("MO %s: updated %s description", mo_no, container_key)
            published += 1

        for a in actions:
            if a.kind == "webex":
                msg = webex_message(a.reason, state, webex_templates,
                                    config.jira_base_url, webex.transport)
                if webex.notify(a.webex_marker, msg):
                    webex_sent += 1

        # A --dry-run must not advance the lifecycle: persisting state here
        # would make a later real run think this poll already happened (missed
        # publishes) and would seed history the container never actually got.
        if not args.dry_run:
            state_store.save_state(state_dir, state)

    # Deliver queued notifications last, so a Webex/UI failure can never
    # interfere with JIRA updates. Anything undelivered is retried next run.
    delivered, pending = webex.flush()

    log.info("SUMMARY: published=%d webex_queued=%d delivered=%d pending=%d "
             "abandoned=%d skipped=%d (of %d MOs)",
             published, webex_sent, delivered, pending, abandoned, skipped,
             len(mo_map))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Poll M3 Ref order no -> JIRA MO build status")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_true", help="mock mode (default, no writes)")
    g.add_argument("--live", action="store_true", help="live mode (real JIRA writes)")
    p.add_argument("--dry-run", action="store_true", help="fetch + compute, no writes")
    p.add_argument("--only", metavar="MO", help="restrict to a single MO number")
    p.add_argument("--container", metavar="KEY[,KEY]",
                   help="pilot: restrict to specific container key(s)")
    p.add_argument("--map", action="append", metavar="MO=CONTAINER",
                   help="force an MO->container pairing and skip comment-scan "
                        "discovery (repeatable); for controlled tests")
    p.add_argument("--reset", action="store_true",
                   help="wipe persisted state for the in-scope MOs before running "
                        "(requires --map/--only/--container); clears test history")
    p.add_argument("--now", metavar='"YYYY-MM-DD HH:MM"', help="override poll time (testing)")
    p.add_argument("--test-webex", metavar="TEXT",
                   help="send one test message via the configured transport, then exit")
    args = p.parse_args()
    try:
        return run(args)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
