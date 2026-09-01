"""
kpi_overlay — Daily backend for the JIRA Kanban KPI Overlay.

Migrated from the legacy standalone ``LiveKPI_Overlay/live_kpi.py``. Fetches all
OPEN SMT PCBA Work Containers for BOTH Singapore and Trutnov from on-prem JIRA,
computes container- and WP-level elapsed working days per location (targets +
holiday calendar chosen by NPI Location), writes kpi_cache.json, and uploads it
as an attachment to the Confluence overlay-cache page. A Tampermonkey userscript
downloads that attachment and draws the coloured pills on the Kanban cards — the
userscript is a pure renderer and needs no change to cover Trutnov.

Two sources of the same numbers (``--source``, default from
``kpi_overlay.source`` in config.yaml):

  jira    — compute elapsed/target/colour here from JIRA issues. The original
            behaviour; core/kpi_core.py is the authority.
  tableau — read the KPIs already computed in the Tableau fact tables
            (Fact_pm_npi_wc_kpi / _wp_kpi) via core/kpi_warehouse.py. This
            machine only renders them, so the Kanban pills and the Tableau
            dashboard cannot drift apart.
  both    — compute both, publish whichever ``kpi_overlay.source_of_truth``
            names, and log every container where they disagree. Run this for a
            week before switching over: the diff report is the migration's
            evidence, and it stays on afterwards as a regression check.

Usage:
    python -m tasks.kpi_overlay.main --mock              # VPS testing (default)
    python -m tasks.kpi_overlay.main --live             # company laptop
    python -m tasks.kpi_overlay.main --live --source both --dry-run
    python -m tasks.kpi_overlay.main --live --source tableau
    python -m tasks.kpi_overlay.main --live --verbose

Output:
    outputs/kpi_cache.json        — the overlay cache (also uploaded to Confluence)
    outputs/kpi_source_diff.json  — --source both only: the full JIRA-vs-Tableau diff
    logs/kpi_overlay.log          — audit log
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import runlock
from core.config_loader import load_config
from core.confluence import ConfluenceClient
from core.errors import FriendlyError, handle_friendly
from core.jira_client import JiraClient
from core.logger import get_logger
from core.kpi_core import (
    TARGETS_V5,
    CF_ORDER_TYPE, CF_NPI_LOCATION, CF_PRODUCT_TYPE, CF_REQUEST_TYPE,
    CF_PARKED_LOG, CF_PROJECT_ID,
)
from core.kpi_warehouse import KpiWarehouseClient
from tasks.kpi_overlay import compare as source_compare
from tasks.kpi_overlay import source_tableau
from tasks.kpi_overlay.logic import compute_live_kpi, YELLOW_THRESHOLD

TASK_NAME = "kpi_overlay"
MOCK_DIR = TASK_DIR / "mock_data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CACHE_FILE = OUTPUT_DIR / "kpi_cache.json"
DIFF_FILE = OUTPUT_DIR / "kpi_source_diff.json"
ATTACHMENT_NAME = "kpi_cache.json"

SOURCES = ("jira", "tableau", "both")

# ─── JQL for OPEN Singapore + Trutnov containers ───
# The only change vs the legacy Singapore-only overlay is the NPI Location clause:
# now an ``in (...)`` set so Trutnov containers enter the cache too.
OPEN_WC_JQL = (
    'issuetype = "Work Container"'
    ' AND "Product Type" = "SMT PCBA"'
    ' AND "NPI Location" in ("Singapore", "Trutnov")'
    ' AND resolution is EMPTY'
    ' ORDER BY created ASC'
)

WC_FIELDS = [
    "key", "summary", "issuetype", "status", "resolution", "resolutiondate",
    "created", "assignee",
    CF_ORDER_TYPE, CF_NPI_LOCATION, CF_PRODUCT_TYPE, CF_REQUEST_TYPE,
    CF_PARKED_LOG, CF_PROJECT_ID,
]

WP_FIELDS = [
    "key", "summary", "issuetype", "status", "resolution", "resolutiondate",
    "created", "assignee",
]


# ═══════════════════════════════════════════════════════════════
# DATA FETCH (live JIRA / mock fixtures)
# ═══════════════════════════════════════════════════════════════

def fetch_containers(jira: JiraClient, logger) -> list[dict]:
    """Fetch open SMT PCBA containers (SG + Trutnov). Mock: mock_data/containers.json."""
    if jira.config.is_mock:
        data = _load_mock_json(MOCK_DIR / "containers.json")
        return data.get("issues", [])
    return jira.search_all(OPEN_WC_JQL, fields=WC_FIELDS)


def fetch_child_wps(jira: JiraClient, wc_key: str) -> list[tuple[str, dict]]:
    """Fetch child Work Packages for one container as (key, fields) tuples.

    Live uses the legacy Project-Children relation JQL (level1, all child
    types). Mock: mock_data/children/<WC_KEY>.json.
    """
    if jira.config.is_mock:
        path = MOCK_DIR / "children" / f"{wc_key}.json"
        if not path.exists():
            return []
        data = _load_mock_json(path)
        return [(i["key"], i["fields"]) for i in data.get("issues", [])]

    child_jql = f'issue in relation("{wc_key}", "Project Children", level1)'
    issues = jira.search_all(child_jql, fields=WP_FIELDS)
    return [(i["key"], i["fields"]) for i in issues if i.get("key") != wc_key]


def _load_mock_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════
# SOURCES — each returns a list of container cache entries
# ═══════════════════════════════════════════════════════════════

def containers_from_jira(config, today: date, logger) -> list[dict]:
    """The original path: fetch JIRA issues, compute the KPIs here."""
    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    logger.info("[jira] Fetching open SMT PCBA Work Containers (Singapore + Trutnov)...")
    wc_issues = fetch_containers(jira, logger)
    logger.info("  Found %d open Work Container(s)", len(wc_issues))

    logger.info("[jira] Fetching Work Packages...")
    wc_to_wps: dict[str, list] = {}
    for wc in wc_issues:
        wc_key = wc["key"]
        try:
            wc_to_wps[wc_key] = fetch_child_wps(jira, wc_key)
        except Exception as exc:  # noqa: BLE001 — never abort the whole run on one WC
            logger.error("  Failed to fetch WPs for %s: %s", wc_key, exc)
            wc_to_wps[wc_key] = []
    total_wps = sum(len(v) for v in wc_to_wps.values())
    logger.info("  Total: %d Work Package(s) across %d container(s)",
                total_wps, len(wc_to_wps))

    logger.info("[jira] Computing live KPI...")
    containers = []
    for wc in wc_issues:
        entry = compute_live_kpi(wc, wc_to_wps.get(wc["key"], []), today, logger)
        if entry is not None:
            entry["source"] = "jira"
            containers.append(entry)
    return containers


def containers_from_tableau(config, today: date, logger) -> list[dict]:
    """The Tableau path: read the fact tables, render what they already say."""
    warehouse = KpiWarehouseClient(config, mock_data_dir=MOCK_DIR, logger=logger)
    try:
        logger.info("[tableau] Reading the KPI fact tables...")
        wc_table = warehouse.table("wc")
        try:
            wp_table = warehouse.table("wp")
        except FriendlyError as exc:
            # Container pills are the point; WP pills are the detail. Losing the
            # WP table should degrade the overlay, not take it down.
            logger.warning("  WP fact table unavailable (%s) — container pills only",
                           exc.message)
            wp_table = None

        max_stale = float((config.get("kpi_warehouse", {}) or {})
                          .get("max_staleness_hours", 0) or 0)
        if max_stale > 0:
            stale = source_tableau.staleness_hours(wc_table, datetime.now())
            if stale is None:
                logger.info("  Snapshot age unknown (no as-of column mapped)")
            elif stale > max_stale:
                logger.warning(
                    "  STALE: newest fact row is ~%.0fh old (limit %.0fh) — the "
                    "warehouse refresh may have failed", stale, max_stale)

        containers = source_tableau.build_containers(
            wc_table, wp_table,
            today,
            row_filter=(config.get("kpi_warehouse", {}) or {}).get("row_filter") or {},
            logger=logger,
        )
        source_tableau.check_colour_agreement(containers, logger=logger)
        return containers
    finally:
        warehouse.close()


# ═══════════════════════════════════════════════════════════════
# PIPELINE
# ═══════════════════════════════════════════════════════════════

def run(mode: str, source: str | None = None, dry_run: bool = False,
        verbose: bool = False) -> int:
    config = load_config(mode_override=mode)
    logger = get_logger(TASK_NAME, log_dir=config.log_dir,
                        level="DEBUG" if verbose else "INFO")

    source = (source or config.get("kpi_overlay.source", "jira") or "jira").lower()
    if source not in SOURCES:
        raise FriendlyError(
            f"unknown KPI source '{source}'",
            f"use one of: {', '.join(SOURCES)} (--source, or kpi_overlay.source)",
        )

    logger.info("=" * 60)
    logger.info("KPI overlay pipeline starting (%s mode, source=%s)", config.mode, source)
    today = date.today()
    logger.info("  Date: %s", today)

    # Both laptops run this so either can cover. The overlay is a full
    # recompute-and-replace, so a double run is not wrong — but two machines
    # uploading the same attachment in the same minute is a race with no winner
    # worth having, and the loser's upload can land second with stale data.
    if not (dry_run or config.is_mock):
        if not runlock.acquire(TASK_NAME, config.get("shared_dir", ""), logger,
                               ttl_minutes=float(config.get("run_lock_ttl_minutes", 20))):
            logger.info("the other laptop is publishing the overlay now — skipping")
            return 0

    # ─── Gather from the configured source(s) ───
    diff_report = None
    if source == "jira":
        containers = containers_from_jira(config, today, logger)
    elif source == "tableau":
        containers = containers_from_tableau(config, today, logger)
    else:
        jira_containers = containers_from_jira(config, today, logger)
        tableau_containers = containers_from_tableau(config, today, logger)
        diff_report = {
            "asOf": datetime.now().isoformat(),
            "asOfDate": str(today),
            "containers": source_compare.compare_containers(
                jira_containers, tableau_containers),
            "workPackages": source_compare.compare_work_packages(
                jira_containers, tableau_containers),
        }
        logger.info("JIRA vs Tableau:\n%s", source_compare.format_report(
            diff_report["containers"], diff_report["workPackages"]))

        rate = source_compare.disagreement_rate(diff_report["containers"])
        limit = float(config.get("kpi_overlay.max_disagreement", 1.0) or 1.0)
        if rate > limit:
            raise FriendlyError(
                f"{rate:.0%} of containers disagree between JIRA and Tableau "
                f"(limit {limit:.0%}) — not publishing",
                "check outputs/kpi_source_diff.json; raise "
                "kpi_overlay.max_disagreement to publish anyway",
            )

        winner = (config.get("kpi_overlay.source_of_truth", "tableau")
                  or "tableau").lower()
        containers = tableau_containers if winner == "tableau" else jira_containers
        logger.info("  Publishing the %s numbers (kpi_overlay.source_of_truth)", winner)

    # ─── Tally ───
    color_counts = {"Green": 0, "Yellow": 0, "Red": 0, "Grey": 0}
    loc_counts: dict[str, int] = {}
    for entry in containers:
        color_counts[entry["color"]] = color_counts.get(entry["color"], 0) + 1
        loc_counts[entry["location"]] = loc_counts.get(entry["location"], 0) + 1

    logger.info(
        "  Results: %d container(s) — Green=%d Yellow=%d Red=%d Grey=%d  (by location: %s)",
        len(containers), color_counts["Green"], color_counts["Yellow"],
        color_counts["Red"], color_counts["Grey"],
        ", ".join(f"{k}={v}" for k, v in sorted(loc_counts.items())) or "none",
    )

    # ─── Flatten per-WP KPIs for the userscript ───
    all_wp_kpis = []
    wp_color_counts = {"Green": 0, "Yellow": 0, "Red": 0, "Grey": 0}
    for c in containers:
        for wpk in c.get("wpKpis", []):
            all_wp_kpis.append(wpk)
            wp_color_counts[wpk["color"]] = wp_color_counts.get(wpk["color"], 0) + 1
    logger.info(
        "  Per-WP pills: %d — Green=%d Yellow=%d Red=%d Grey=%d",
        len(all_wp_kpis), wp_color_counts["Green"], wp_color_counts["Yellow"],
        wp_color_counts["Red"], wp_color_counts["Grey"],
    )

    # ─── Build cache ───
    cache = {
        "asOf": datetime.now().isoformat(),
        "asOfDate": str(today),
        # Where these numbers came from. The userscript ignores it; a human
        # opening the attachment to ask "why is this pill Red" needs it.
        "source": source,
        "publishedSource": containers[0].get("source") if containers else source,
        "locations": sorted(loc_counts.keys()) or ["Singapore", "Trutnov"],
        # Backward-compatible scalars for the existing renderer; per-container
        # `target`/`location` are authoritative.
        "location": "Singapore",
        "target": TARGETS_V5["Singapore"]["T_NPI"],
        "targetsByLocation": {loc: t["T_NPI"] for loc, t in TARGETS_V5.items()},
        "yellowThreshold": YELLOW_THRESHOLD,
        "containerCount": len(containers),
        "workPackageCount": len(all_wp_kpis),
        "summary": {
            "green": color_counts["Green"],
            "yellow": color_counts["Yellow"],
            "red": color_counts["Red"],
        },
        "wpSummary": wp_color_counts,
        "containers": containers,
        "workPackageKpis": all_wp_kpis,
    }
    if diff_report is not None:
        cache["sourceAgreement"] = {
            "containers": diff_report["containers"]["agreement"],
            "workPackages": diff_report["workPackages"]["agreement"],
            "onlyInJira": len(diff_report["containers"]["onlyInJira"]),
            "onlyInTableau": len(diff_report["containers"]["onlyInTableau"]),
        }

    # The diff is evidence, not output — it is written even on a dry run,
    # because "--source both --dry-run" is exactly how the migration is
    # validated without touching the live attachment.
    if diff_report is not None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DIFF_FILE, "w", encoding="utf-8") as f:
            json.dump(diff_report, f, indent=2, ensure_ascii=False, default=str)
        logger.info("Source diff written: %s", DIFF_FILE)

    # ─── Write + upload ───
    if dry_run:
        logger.info("DRY RUN — cache not written. Preview:")
        for c in containers[:8]:
            logger.info("    %s [%s]: %s/%s -> %s",
                        c["issueKey"], c["location"], c["elapsed"],
                        c["target"], c["color"])
        if len(containers) > 8:
            logger.info("    ... and %d more", len(containers) - 8)
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cache, indent=2, ensure_ascii=False)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(payload)
    logger.info("Cache written: %s (%d bytes)", CACHE_FILE, len(payload))

    if config.is_live:
        page_id = config.pages.get("kpi_overlay_cache")
        if not page_id:
            logger.error(
                "No pages.kpi_overlay_cache in config.yaml — cannot upload. "
                "Add: pages.kpi_overlay_cache: 572629046"
            )
        else:
            logger.info("Uploading cache to Confluence page %s...", page_id)
            confluence = ConfluenceClient(config, mock_data_dir=MOCK_DIR)
            confluence.upload_attachment(
                page_id, ATTACHMENT_NAME, payload.encode("utf-8"),
                content_type="application/json",
            )
            logger.info("  Uploaded %s to page %s", ATTACHMENT_NAME, page_id)
    else:
        logger.info("Mock mode — skipping Confluence upload.")

    logger.info("Pipeline complete.")
    logger.info("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Live KPI backend for the JIRA Kanban overlay")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_const", const="mock", dest="mode",
                       help="Read from mock_data/ (default)")
    group.add_argument("--live", action="store_const", const="live", dest="mode",
                       help="Hit live JIRA + upload to Confluence (company laptop only)")
    parser.add_argument("--source", choices=SOURCES, default=None,
                        help="Where the KPIs come from (default: kpi_overlay.source "
                             "in config.yaml, or 'jira')")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch & compute but don't write/upload the cache")
    parser.add_argument("--verbose", action="store_true", help="Debug-level logging")
    parser.set_defaults(mode="mock")
    args = parser.parse_args()
    try:
        return run(args.mode, source=args.source, dry_run=args.dry_run,
                   verbose=args.verbose)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
