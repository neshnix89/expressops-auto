"""
Measure what the new SMT Build start gate does to the live board, before it
publishes anything.

    python scripts\\smt_gate_impact.py --live
    python scripts\\smt_gate_impact.py --live --verbose

Read-only: it reads JIRA, computes both the OLD gate and the NEW one for every
open container, and prints the difference. Nothing is written and nothing is
uploaded.

  OLD gate  max(Material, PCB) resolution, Done/Acknowledged only
  NEW gate  the latest of Material, PCB, Routing/PE/TE - TechnPrep, each
            Done, Acknowledged or Won't Do

Three outcomes matter, and the third is the one to look at before switching:

  SAME       the gate date does not move — the pill is unchanged
  LATER      the gate moves later, so SMT Build's elapsed count DROPS
  NOW GREY   the gate no longer closes at all, because a tech-prep package is
             still open. The pill loses its number and shows as waiting, even
             if SMT Build itself is finished. That is honest — you cannot
             measure from a start that never happened — but it is visible on
             the board, so it is worth knowing the count and the names first.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config_loader import load_config                       # noqa: E402
from core.errors import FriendlyError, handle_friendly           # noqa: E402
from core.kpi_core import to_date                                # noqa: E402
from core.logger import get_logger                               # noqa: E402
from tasks.kpi_overlay.logic import (                            # noqa: E402
    DONE_RESOLUTIONS,
    OVERLAY_WP_NAMES,
    SMT_BUILD_GATE_WPS,
    closes_gate,
    compute_build_gate,
)
from tasks.kpi_overlay.main import fetch_child_wps, fetch_containers  # noqa: E402
from core.jira_client import JiraClient                          # noqa: E402

MOCK_DIR = PROJECT_ROOT / "tasks" / "kpi_overlay" / "mock_data"
OUT_DIR = PROJECT_ROOT / "outputs"


def official_wps_of(wp_list) -> list[dict]:
    """Same shape compute_wp_kpis consumes, filtered to recognised WP names."""
    known = {n.lower() for n in OVERLAY_WP_NAMES}
    out = []
    for wp_key, wpf in wp_list:
        summary = (wpf.get("summary") or "").strip()
        if summary.lower() not in known:
            continue
        out.append({
            "key": wp_key,
            "summary": summary,
            "created": to_date(wpf.get("created", "")),
            "resolved": to_date(wpf.get("resolutiondate", "")),
            "status": ((wpf.get("status") or {}).get("name") or "").strip(),
            "resolution": ((wpf.get("resolution") or {}).get("name") or "").strip(),
        })
    return out


def old_gate(official_wps) -> date | None:
    """The pre-2026-09-22 rule: max(Material, PCB), Done/Acknowledged only."""
    material = pcb = None
    for wp in official_wps:
        if wp["resolution"] in DONE_RESOLUTIONS and wp["resolved"] is not None:
            if wp["summary"].strip().lower() == "material":
                material = wp["resolved"]
            elif wp["summary"].strip().lower() == "pcb":
                pcb = wp["resolved"]
    if material is None or pcb is None:
        return None
    return max(material, pcb)


def blockers(official_wps) -> list[str]:
    """Which gate packages are still open on this container."""
    by_name = {}
    for wp in official_wps:
        by_name.setdefault(wp["summary"].strip().lower(), wp)
    open_ones = []
    for name in SMT_BUILD_GATE_WPS:
        wp = by_name.get(name)
        if wp is None:
            continue
        if not closes_gate(wp["resolution"]) or wp["resolved"] is None:
            open_ones.append(f"{wp['summary']} ({wp['resolution'] or 'unresolved'})")
    return open_ones


def run(mode: str, verbose: bool) -> int:
    config = load_config(mode_override=mode)
    logger = get_logger("smt_gate_impact", log_dir=config.log_dir,
                        level="DEBUG" if verbose else "INFO")
    today = date.today()
    logger.info("SMT Build gate impact — %s mode, %s", config.mode, today)

    jira = JiraClient(config, mock_data_dir=MOCK_DIR)
    wc_issues = fetch_containers(jira, logger)
    logger.info("  %d open Work Container(s)", len(wc_issues))

    rows = []
    # Every resolution value actually seen on a gate package, and how often.
    # The accepted set (Done / Acknowledged / Won't Do) was chosen from how the
    # process is described, not from the data; anything else in here silently
    # holds a gate open forever, so it has to be looked at rather than assumed.
    seen_resolutions: dict[str, int] = {}
    gate_names = set(SMT_BUILD_GATE_WPS)

    for wc in wc_issues:
        key = wc["key"]
        try:
            wps = official_wps_of(fetch_child_wps(jira, key))
        except Exception as exc:  # noqa: BLE001 — one bad container is not the run
            logger.error("  %s: could not fetch WPs (%s)", key, exc)
            continue
        if not wps:
            continue

        for wp in wps:
            if wp["summary"].strip().lower() not in gate_names:
                continue
            label = wp["resolution"] or "(unresolved)"
            seen_resolutions[label] = seen_resolutions.get(label, 0) + 1

        before = old_gate(wps)
        after = compute_build_gate(wps)
        smt = next((w for w in wps if w["summary"].strip().lower() == "smt build"), None)
        smt_state = "absent" if smt is None else (
            "done" if smt["resolution"] in DONE_RESOLUTIONS else "running")

        if before == after:
            verdict = "SAME"
        elif before is not None and after is None:
            verdict = "NOW GREY"
        elif before is None and after is not None:
            verdict = "NOW COUNTS"
        elif after > before:
            verdict = "LATER"
        else:
            verdict = "EARLIER"

        rows.append({
            "key": key, "before": before, "after": after,
            "verdict": verdict, "smt": smt_state,
            "blockers": blockers(wps),
        })

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    print()
    print("=" * 78)
    print(f"SMT BUILD GATE IMPACT — {len(rows)} container(s) with recognised WPs")
    print("=" * 78)
    for verdict in ("SAME", "LATER", "EARLIER", "NOW COUNTS", "NOW GREY"):
        if verdict in counts:
            print(f"  {verdict:<11} {counts[verdict]}")

    changed = [r for r in rows if r["verdict"] != "SAME"]
    if changed:
        print()
        print(f"  {'CONTAINER':<16} {'OLD GATE':<12} {'NEW GATE':<12} {'SMT':<8} VERDICT")
        for r in sorted(changed, key=lambda r: r["verdict"]):
            print(f"  {r['key']:<16} {str(r['before'] or '-'):<12} "
                  f"{str(r['after'] or '-'):<12} {r['smt']:<8} {r['verdict']}")

    print()
    print("  RESOLUTIONS SEEN ON GATE PACKAGES — does each one release the gate?")
    print(f"    {'RESOLUTION':<22} {'COUNT':>6}  RELEASES?")
    for label, n in sorted(seen_resolutions.items(), key=lambda kv: -kv[1]):
        if label == "(unresolved)":
            verdict = "no - still open, correct"
        elif closes_gate(label):
            verdict = "yes"
        else:
            verdict = "NO  <-- holds the gate open forever"
        print(f"    {label:<22} {n:>6}  {verdict}")
    stranding = {l: n for l, n in seen_resolutions.items()
                 if l != "(unresolved)" and not closes_gate(l)}
    if stranding:
        print()
        print("    A package closed with one of the marked resolutions is finished as")
        print("    far as the process is concerned, but the gate does not accept it, so")
        print("    that container's SMT Build pill can never show a number again.")
        print("    Decide whether these should count as 'off the table' too.")

    grey = [r for r in rows if r["verdict"] == "NOW GREY"]
    if grey:
        print()
        print("  LOSING THEIR NUMBER — the packages holding each gate open:")
        for r in grey:
            print(f"    {r['key']:<16} {', '.join(r['blockers']) or '(none found)'}")
        print()
        print("  Each of these is a tech-prep package left open in JIRA while the")
        print("  build moved on. Closing it in JIRA restores the pill; the overlay")
        print("  is reporting the data, not breaking it.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / "smt_gate_impact.txt"
    with open(dest, "w", encoding="utf-8") as f:
        f.write(f"SMT Build gate impact — {today}\n")
        for r in rows:
            f.write(f"{r['key']}|{r['before']}|{r['after']}|{r['smt']}|"
                    f"{r['verdict']}|{'; '.join(r['blockers'])}\n")
    print()
    print(f"Report: {dest}")
    print("Nothing was written to JIRA or Confluence.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="What the new SMT Build gate changes")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_const", const="mock", dest="mode")
    g.add_argument("--live", action="store_const", const="live", dest="mode")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(mode="mock")
    args = p.parse_args()
    try:
        return run(args.mode, args.verbose)
    except FriendlyError as exc:
        return handle_friendly(exc)


if __name__ == "__main__":
    sys.exit(main())
