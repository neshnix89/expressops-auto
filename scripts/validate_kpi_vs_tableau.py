"""
Validate the overlay's KPI arithmetic against Tableau's, container by container.

Run this ON THE COMPANY LAPTOP once the fact tables are reachable:

    python scripts\\validate_kpi_vs_tableau.py --live
    python scripts\\validate_kpi_vs_tableau.py --live --md   # also a Confluence-able table

Why a script and not a code review: the warehouse job that computes
Fact_pm_npi_wc_kpi is not in this repo, so there is no formula to read. The only
honest validation is to run both implementations over the same containers on the
same day and diff the numbers. This does that, then interprets the diff:

  * every container in one source and not the other  → a SCOPE difference, which
    matters more than any arithmetic one, because that is what makes a pill
    vanish from the board
  * one single non-zero bucket in the elapsed histogram → a SYSTEMATIC offset,
    almost always the "KPI method" -1 (does the start day count as day 0 or 1)
  * same elapsed, different target                   → the two sides hold
    different target tables; the per-location breakdown says which cells
  * same elapsed and target, different colour        → an overlay-only rule
    (the 2-day amber band, the tech-prep secondary rule) with no warehouse
    equivalent — expected, not a bug

Read-only. Nothing is uploaded and the live overlay attachment is not touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config                      # noqa: E402
from core.errors import FriendlyError, handle_friendly          # noqa: E402
from core.kpi_core import TARGETS_V5                            # noqa: E402
from core.logger import get_logger                              # noqa: E402
from tasks.kpi_overlay import compare as source_compare         # noqa: E402
from tasks.kpi_overlay.main import (                            # noqa: E402
    containers_from_jira,
    containers_from_tableau,
)

OUT_DIR = PROJECT_ROOT / "outputs" / "kpi_validation"


def interpret(container_report: dict, wp_report: dict,
              jira_entries: list[dict], tableau_entries: list[dict]) -> list[str]:
    """Turn the raw diff into the handful of sentences a human needs."""
    lines: list[str] = []
    cr = container_report
    matched = cr["matched"]

    # --- scope ---
    if cr["onlyInJira"] or cr["onlyInTableau"]:
        lines.append(
            f"SCOPE: {len(cr['onlyInJira'])} container(s) only the JIRA query "
            f"sees, {len(cr['onlyInTableau'])} only the fact table sees. Fix this "
            "before reading anything below — the two sides are not describing "
            "the same population."
        )
        if cr["onlyInJira"]:
            lines.append(f"  only in JIRA   : {', '.join(cr['onlyInJira'][:20])}")
        if cr["onlyInTableau"]:
            lines.append(f"  only in Tableau: {', '.join(cr['onlyInTableau'][:20])}")
    else:
        lines.append(f"SCOPE: identical — both sources hold the same {matched} container(s).")

    # --- elapsed ---
    hist = cr["elapsedDeltaHistogram"]
    if not hist:
        lines.append("ELAPSED: nothing comparable (no container had both numbers).")
    elif set(hist) == {"0"}:
        lines.append(f"ELAPSED: identical on all {hist['0']} container(s). "
                     "The working-day arithmetic agrees.")
    elif len(hist) == 1:
        delta = int(next(iter(hist)))
        lines.append(
            f"ELAPSED: Tableau is {delta:+d} day(s) on EVERY container "
            f"({hist[str(delta)]} of them). A constant offset is not noise — it is "
            "a convention difference. The usual cause is the 'KPI method' -1 in "
            "core/kpi_core.py fNetWorkdays (start day counted as 0). Decide which "
            "convention is official and change the one that is wrong; do not "
            "average them."
        )
    else:
        zero = hist.get("0", 0)
        lines.append(
            f"ELAPSED: {zero}/{matched} identical; the rest spread across "
            f"{len(hist) - (1 if zero else 0)} different offsets {hist}. A spread "
            "means a per-container input differs, not a convention — check the "
            "parked containers and the holiday calendars first (core/kpi_core.py "
            "HOLIDAYS is hardcoded and currently only covers 2026)."
        )

    # --- targets ---
    target_pairs: dict[tuple, int] = {}
    for d in cr["diffs"]:
        if d["targetDiffers"]:
            key = (d["location"], d["jira"]["target"], d["tableau"]["target"])
            target_pairs[key] = target_pairs.get(key, 0) + 1
    if target_pairs:
        lines.append("TARGETS: the two sides hold different target tables —")
        for (loc, jt, tt), n in sorted(target_pairs.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {loc or '?':<12} JIRA={jt}  Tableau={tt}  ({n} container(s))")
        lines.append(
            "  core/kpi_core.py TARGETS_V5 carries two deliberate corrections vs "
            "the legacy table (Singapore Documentation 1 -> 4, Trutnov Logistics "
            "4 -> 1). If Tableau still has the legacy values, that is the "
            "difference, and it is a business decision which one is right."
        )
    else:
        lines.append("TARGETS: identical wherever both sides had one.")

    # --- warehouse target source ---
    from_warehouse = sum(1 for c in tableau_entries
                         if c.get("targetSource") == "warehouse")
    if from_warehouse < len(tableau_entries):
        lines.append(
            f"TARGET SOURCE: only {from_warehouse}/{len(tableau_entries)} Tableau "
            "containers carried their own target column; the rest fell back to "
            "TARGETS_V5, so those target comparisons are trivially equal and "
            "prove nothing. Map the warehouse's target column in "
            "kpi_warehouse.columns.wc to make this a real test."
        )

    # --- colour ---
    colour_only = [d for d in cr["diffs"]
                   if d["colorDiffers"] and not d["targetDiffers"]
                   and d["elapsedDelta"] == 0]
    if colour_only:
        lines.append(
            f"COLOUR: {len(colour_only)} container(s) differ on colour ALONE — "
            "same elapsed, same target. That is the overlay's amber band "
            "(remaining <= 2 days) and has no warehouse equivalent, so it is "
            "expected. Confirm each one is Yellow on our side."
        )

    # --- work packages ---
    wa = wp_report["agreement"]
    if wa["of"]:
        lines.append(
            f"WORK PACKAGES: {wa['elapsed']}/{wa['of']} same elapsed, "
            f"{wa['target']}/{wa['of']} same target, {wa['color']}/{wa['of']} same "
            f"colour. Deltas {wp_report['elapsedDeltaHistogram']}."
        )
        if wp_report["onlyInJira"] or wp_report["onlyInTableau"]:
            lines.append(
                f"  {len(wp_report['onlyInJira'])} WP(s) only in JIRA, "
                f"{len(wp_report['onlyInTableau'])} only in Tableau. The JIRA path "
                "draws pills only for the names in OVERLAY_WP_NAMES; the fact "
                "table may carry more (or fewer) rows per container."
            )
    else:
        lines.append("WORK PACKAGES: nothing comparable — the WP fact table gave no "
                     "rows that join to a container in both sources.")

    return lines


def to_markdown(cr: dict, wp: dict, verdicts: list[str], today: date) -> str:
    md = [f"# KPI validation — overlay vs Tableau ({today})", ""]
    md.append("## Verdict")
    md.extend(f"- {v}" for v in verdicts)
    md.append("")
    md.append("## Agreement")
    md.append("| Level | Same elapsed | Same target | Same colour | Matched |")
    md.append("|---|---|---|---|---|")
    a, b = cr["agreement"], wp["agreement"]
    md.append(f"| Container | {a['elapsed']} | {a['target']} | {a['color']} | {a['of']} |")
    md.append(f"| Work package | {b['elapsed']} | {b['target']} | {b['color']} | {b['of']} |")
    md.append("")
    if cr["diffs"]:
        md.append("## Container differences")
        md.append("| Container | Location | JIRA elapsed/target | Tableau elapsed/target "
                  "| JIRA | Tableau | Delta |")
        md.append("|---|---|---|---|---|---|---|")
        for d in cr["diffs"]:
            j, t = d["jira"], d["tableau"]
            md.append(
                f"| {d['issueKey']} | {d['location'] or ''} | {j['elapsed']}/{j['target']} "
                f"| {t['elapsed']}/{t['target']} | {j['color']} | {t['color']} "
                f"| {d['elapsedDelta']} |"
            )
        md.append("")
    md.append("## Reference — the overlay's target table (core/kpi_core.py TARGETS_V5)")
    md.append("| Bucket | Singapore | Trutnov |")
    md.append("|---|---|---|")
    for bucket in TARGETS_V5["Singapore"]:
        md.append(f"| {bucket} | {TARGETS_V5['Singapore'][bucket]} "
                  f"| {TARGETS_V5['Trutnov'][bucket]} |")
    return "\n".join(md)


def run(mode: str, write_md: bool, verbose: bool) -> int:
    config = load_config(mode_override=mode)
    logger = get_logger("kpi_validation", log_dir=config.log_dir,
                        level="DEBUG" if verbose else "INFO")
    today = date.today()

    logger.info("=" * 60)
    logger.info("KPI validation — JIRA vs Tableau (%s mode, %s)", config.mode, today)

    jira_entries = containers_from_jira(config, today, logger)
    tableau_entries = containers_from_tableau(config, today, logger)

    cr = source_compare.compare_containers(jira_entries, tableau_entries)
    wp = source_compare.compare_work_packages(jira_entries, tableau_entries)
    verdicts = interpret(cr, wp, jira_entries, tableau_entries)

    print()
    print(source_compare.format_report(cr, wp, max_rows=100))
    print()
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    for v in verdicts:
        print(v)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    js = OUT_DIR / f"validation_{stamp}.json"
    js.write_text(json.dumps({
        "asOf": datetime.now().isoformat(),
        "asOfDate": str(today),
        "mode": config.mode,
        "verdict": verdicts,
        "containers": cr,
        "workPackages": wp,
    }, indent=2, default=str), encoding="utf-8")
    print()
    print(f"Report: {js}")

    if write_md:
        md = OUT_DIR / f"validation_{stamp}.md"
        md.write_text(to_markdown(cr, wp, verdicts, today), encoding="utf-8")
        print(f"Markdown (paste into Confluence): {md}")

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Diff the overlay's KPIs against Tableau's")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_const", const="mock", dest="mode")
    g.add_argument("--live", action="store_const", const="live", dest="mode")
    p.add_argument("--md", action="store_true", help="also write a markdown report")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(mode="mock")
    args = p.parse_args()
    try:
        return run(args.mode, args.md, args.verbose)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
