"""
Pure-logic checks for the kpi_overlay KPI maths — runnable with no JIRA access:

    python -m tasks.kpi_overlay.test_logic

Focused on the SMT Build start gate, because that is the rule that changed on
2026-09-22 and the one whose failure mode is silent: get it wrong and the pill
shows a plausible number computed from the wrong day, which nobody spots on a
Kanban card.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tasks.kpi_overlay.logic import (
    build_wp_config,
    closes_gate,
    compute_build_gate,
    compute_wp_kpis,
)

TODAY = date(2026, 9, 22)

_PASSED = 0
_FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  PASS  {name}")
    else:
        _FAILED += 1
        print(f"  FAIL  {name}  {detail}")


def wp(summary, resolution=None, resolved=None, created="2026-08-03"):
    """One official WP as compute_wp_kpis expects it."""
    return {
        "key": f"USRE-{abs(hash(summary)) % 9000 + 1000}",
        "summary": summary,
        "created": date.fromisoformat(created),
        "resolved": date.fromisoformat(resolved) if resolved else None,
        "status": "Done" if resolution else "In Progress",
        "resolution": resolution,
    }


def full_kit(**overrides):
    """The five gate packages, all Done, with easily-read dates."""
    base = {
        "Material":           ("Done", "2026-09-01"),
        "PCB":                ("Done", "2026-09-02"),
        "Routing - TechnPrep": ("Done", "2026-09-03"),
        "PE - TechnPrep":     ("Done", "2026-09-04"),
        "TE - TechnPrep":     ("Done", "2026-09-07"),
    }
    base.update(overrides)
    return [wp(name, res, when) for name, (res, when) in base.items()]


# ═══════════════════════════════════════════════════════════════

def test_closes_gate() -> None:
    check("Done closes", closes_gate("Done") is True)
    check("Acknowledged closes", closes_gate("Acknowledged") is True)
    check("Won't Do closes", closes_gate("Won't Do") is True)
    check("Wont Do (no apostrophe) closes", closes_gate("Wont Do") is True)
    check("WON'T DO closes", closes_gate("WON'T DO") is True)
    check("unresolved does not close", closes_gate(None) is False)
    # The accepted set is exactly these three by decision, not by oversight —
    # widening it was offered on 2026-09-22 and declined. These two assertions
    # exist so that stays a decision rather than drifting back.
    check("Cancelled does not close", closes_gate("Cancelled") is False,
          "only Done / Acknowledged / Won't Do were specified")
    check("Duplicate does not close", closes_gate("Duplicate") is False,
          "seen live on NPIOTHER-6325 / -6452; keeping it out is deliberate")


def test_gate_is_the_latest_of_all_five() -> None:
    gate = compute_build_gate(full_kit())
    check("gate = latest of the five", gate == date(2026, 9, 7), str(gate))

    # The old rule would have answered 2026-09-02 (max of Material, PCB).
    check("gate is NOT the old material-fullset date", gate != date(2026, 9, 2))


def test_each_techprep_blocks() -> None:
    for blocker in ("Routing - TechnPrep", "PE - TechnPrep", "TE - TechnPrep"):
        gate = compute_build_gate(full_kit(**{blocker: (None, None)}))
        check(f"unresolved {blocker} blocks the gate", gate is None, str(gate))


def test_material_and_pcb_still_block() -> None:
    check("unresolved Material blocks",
          compute_build_gate(full_kit(Material=(None, None))) is None)
    check("unresolved PCB blocks",
          compute_build_gate(full_kit(PCB=(None, None))) is None)


def test_wont_do_closes_the_gate() -> None:
    gate = compute_build_gate(full_kit(**{"PE - TechnPrep": ("Won't Do", "2026-09-10")}))
    check("Won't Do tech-prep releases the gate and counts its date",
          gate == date(2026, 9, 10), str(gate))

    # The behaviour the old code got wrong: a Won't Do Material left SMT Build
    # waiting forever.
    gate = compute_build_gate(full_kit(Material=("Won't Do", "2026-09-01")))
    check("Won't Do Material no longer strands SMT Build",
          gate == date(2026, 9, 7), str(gate))


def test_absent_techprep_does_not_block() -> None:
    kit = [w for w in full_kit() if w["summary"] != "PE - TechnPrep"]
    gate = compute_build_gate(kit)
    check("a container with no PE package is not blocked by it",
          gate == date(2026, 9, 7), str(gate))


def test_missing_material_or_pcb_is_not_anchored() -> None:
    kit = [w for w in full_kit() if w["summary"] != "PCB"]
    check("no PCB package at all -> not anchored",
          compute_build_gate(kit) is None)


def test_pill_waits_then_counts() -> None:
    cfg = build_wp_config("Singapore")

    blocked = full_kit(**{"TE - TechnPrep": (None, None)}) + [
        wp("SMT Build", None, None, created="2026-08-03")]
    pills = {p["name"]: p for p in compute_wp_kpis(
        blocked, date(2026, 8, 3), [], TODAY, "Singapore", cfg)}
    smt = pills["SMT Build"]
    check("SMT Build pill waits while a tech-prep is open",
          smt["state"] == "waiting" and smt["elapsed"] is None and smt["color"] == "Grey",
          str(smt))

    ready = full_kit() + [wp("SMT Build", None, None, created="2026-08-03")]
    pills = {p["name"]: p for p in compute_wp_kpis(
        ready, date(2026, 8, 3), [], TODAY, "Singapore", cfg)}
    smt = pills["SMT Build"]
    # 2026-09-07 (Mon) -> 2026-09-22 (Tue), weekdays inclusive minus one, no SG
    # holiday in that window.
    check("SMT Build counts from the gate date once it clears",
          smt["state"] == "active" and smt["startDate"] == "2026-09-07",
          str(smt))
    check("SMT Build elapsed is measured, not None", smt["elapsed"] == 11,
          f"elapsed={smt['elapsed']}")


def test_techprep_colour_rule_is_not_circular() -> None:
    """Tech-prep Green/Red must still compare against max(Material, PCB).

    If the new gate had replaced material_fullset, every tech-prep package
    would be compared against a date that includes its own completion and so
    could never be Red.
    """
    cfg = build_wp_config("Singapore")
    # Routing took 30+ working days and finished well AFTER the kit -> Red.
    wps = full_kit(**{"Routing - TechnPrep": ("Done", "2026-09-21")})
    for w in wps:
        if w["summary"] == "Routing - TechnPrep":
            w["created"] = date(2026, 7, 1)
    pills = {p["name"]: p for p in compute_wp_kpis(
        wps, date(2026, 7, 1), [], TODAY, "Singapore", cfg)}
    routing = pills["Routing - TechnPrep"]
    check("a late tech-prep can still be Red", routing["color"] == "Red",
          str(routing))


def test_logistics_still_keys_off_smt_build() -> None:
    cfg = build_wp_config("Singapore")
    wps = full_kit() + [
        wp("SMT Build", "Done", "2026-09-14", created="2026-08-03"),
        wp("Logistics", None, None, created="2026-08-03"),
    ]
    pills = {p["name"]: p for p in compute_wp_kpis(
        wps, date(2026, 8, 3), [], TODAY, "Singapore", cfg)}
    check("Logistics starts at SMT Build's resolution, unchanged",
          pills["Logistics"]["startDate"] == "2026-09-14",
          str(pills["Logistics"]))


def main() -> int:
    for fn in (
        test_closes_gate,
        test_gate_is_the_latest_of_all_five,
        test_each_techprep_blocks,
        test_material_and_pcb_still_block,
        test_wont_do_closes_the_gate,
        test_absent_techprep_does_not_block,
        test_missing_material_or_pcb_is_not_anchored,
        test_pill_waits_then_counts,
        test_techprep_colour_rule_is_not_circular,
        test_logistics_still_keys_off_smt_build,
    ):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'=' * 50}\n{_PASSED} passed, {_FAILED} failed")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
