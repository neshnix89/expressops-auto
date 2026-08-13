"""
kpi_overlay — Daily backend for the JIRA Kanban KPI Overlay.

Migrated from the legacy standalone ``LiveKPI_Overlay/live_kpi.py``. Fetches all
OPEN SMT PCBA Work Containers for BOTH Singapore and Trutnov from on-prem JIRA,
computes container- and WP-level elapsed working days per location (targets +
holiday calendar chosen by NPI Location), writes kpi_cache.json, and uploads it
as an attachment to the Confluence overlay-cache page. A Tampermonkey userscript
downloads that attachment and draws the coloured pills on the Kanban cards — the
userscript is a pure renderer and needs no change to cover Trutnov.

Usage:
    python -m tasks.kpi_overlay.main --mock              # VPS testing (default)
    python -m tasks.kpi_overlay.main --live             # company laptop
    python -m tasks.kpi_overlay.main --live --dry-run   # compute, don't write/upload
    python -m tasks.kpi_overlay.main --live --verbose

Output:
    outputs/kpi_cache.json  — the overlay cache (also uploaded to Confluence)
    logs/kpi_overlay.log    — audit log
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
from tasks.kpi_overlay.logic import compute_live_kpi, YELLOW_THRESHOLD

TASK_NAME = "kpi_overlay"
MOCK_DIR = TASK_DIR / "mock_data"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CACHE_FILE = OUTPUT_DIR / "kpi_cache.json"
ATTACHMENT_NAME = "kpi_cache.json"

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
# PIPELINE
# ═══════════════════════════════════════════════════════════════

def run(mode: str, dry_run: bool = False, verbose: bool = False) -> int:
    config = load_config(mode_override=mode)
    logger = get_logger(TASK_NAME, log_dir=config.log_dir,
                        level="DEBUG" if verbose else "INFO")
    logger.info("=" * 60)
    logger.info("KPI overlay pipeline starting (%s mode)", config.mode)
    today = date.today()
    logger.info("  Date: %s", today)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)

    # ─── Fetch open containers (Singapore + Trutnov) ───
    logger.info("Fetching open SMT PCBA Work Containers (Singapore + Trutnov)...")
    wc_issues = fetch_containers(jira, logger)
    logger.info("  Found %d open Work Container(s)", len(wc_issues))

    # ─── Fetch child WPs per container ───
    logger.info("Fetching Work Packages...")
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

    # ─── Compute KPI per container ───
    logger.info("Computing live KPI...")
    containers = []
    color_counts = {"Green": 0, "Yellow": 0, "Red": 0}
    loc_counts: dict[str, int] = {}
    for wc in wc_issues:
        entry = compute_live_kpi(wc, wc_to_wps.get(wc["key"], []), today, logger)
        if entry is not None:
            containers.append(entry)
            color_counts[entry["color"]] += 1
            loc_counts[entry["location"]] = loc_counts.get(entry["location"], 0) + 1

    logger.info(
        "  Results: %d container(s) — Green=%d Yellow=%d Red=%d  (by location: %s)",
        len(containers), color_counts["Green"], color_counts["Yellow"],
        color_counts["Red"],
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch & compute but don't write/upload the cache")
    parser.add_argument("--verbose", action="store_true", help="Debug-level logging")
    parser.set_defaults(mode="mock")
    args = parser.parse_args()
    try:
        return run(args.mode, dry_run=args.dry_run, verbose=args.verbose)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
