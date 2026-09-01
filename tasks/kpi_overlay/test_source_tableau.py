"""
Pure-logic checks for the Tableau KPI source — runnable with no network:

    python -m tasks.kpi_overlay.test_source_tableau

Two things are worth testing without a warehouse in front of you:

  * column resolution — the fact tables' real column names are still unknown,
    so the thing that must not break is the RESOLVER: it has to survive
    different spellings, honour a config override, and fail loudly (naming the
    real columns) rather than silently producing a table of Nones.
  * the diff — a validation harness that cannot detect a difference is worse
    than none, because it reports agreement. Each of the three failure modes
    the migration actually risks is injected here and has to be caught.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.errors import FriendlyError
from core.kpi_warehouse import WarehouseTable, resolve_columns
from tasks.kpi_overlay import compare as source_compare
from tasks.kpi_overlay.source_tableau import (
    build_containers,
    check_colour_agreement,
    container_colour,
    hit_verdict,
    to_days,
)

TODAY = date(2026, 9, 1)

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


def _wc_table(rows, columns=None, overrides=None) -> WarehouseTable:
    columns = columns or list(rows[0].keys())
    return WarehouseTable("wc", rows, columns, overrides, source="test")


def _wp_table(rows, columns=None) -> WarehouseTable:
    columns = columns or list(rows[0].keys())
    return WarehouseTable("wp", rows, columns, None, source="test")


# ═══════════════════════════════════════════════════════════════

def test_column_resolution() -> None:
    snake = resolve_columns(
        ["wc_issue_key", "wc_running_duration_workdays", "wc_npi_location"], "wc")
    check("snake_case resolves", snake.get("issue_key") == "wc_issue_key")
    check("elapsed prefers the running column",
          snake.get("elapsed") == "wc_running_duration_workdays")

    spaced = resolve_columns(["WC Issue Key", "Target Line"], "wc")
    check("spaced + title case resolves", spaced.get("issue_key") == "WC Issue Key",
          str(spaced))
    check("target_line maps to target", spaced.get("target") == "Target Line")

    # A column the candidate lists have never heard of, pinned from config.
    weird = resolve_columns(["ZZ_KEY", "ZZ_DAYS"], "wc",
                            {"issue_key": "ZZ_KEY", "elapsed": "ZZ_DAYS"})
    check("config override wins", weird == {"issue_key": "ZZ_KEY", "elapsed": "ZZ_DAYS"},
          str(weird))

    try:
        resolve_columns(["ZZ_KEY"], "wc", {"issue_key": "NOT_THERE"})
    except FriendlyError as exc:
        check("override pointing at a missing column raises",
              "NOT_THERE" in exc.message and "ZZ_KEY" in (exc.hint or ""))
    else:
        check("override pointing at a missing column raises", False)


def test_required_fields() -> None:
    table = _wc_table([{"wc_summary": "no key here"}])
    try:
        table.require()
    except FriendlyError as exc:
        check("missing required field raises", "issue_key" in exc.message, exc.message)
        check("the error names the real columns", "wc_summary" in (exc.hint or ""))
    else:
        check("missing required field raises", False)


def test_value_coercion() -> None:
    check("Decimal-ish string -> int", to_days("18.0") == 18)
    check("blank -> None", to_days("") is None)
    check("None -> None", to_days(None) is None)
    check("hit 'Y' is True", hit_verdict("Y") is True)
    check("hit 0 is False", hit_verdict(0) is False)
    check("hit 'maybe' is None", hit_verdict("maybe") is None)


def test_colours() -> None:
    check("under target is Green", container_colour(10, 24)[0] == "Green")
    check("2 days left is Yellow", container_colour(22, 24)[0] == "Yellow")
    check("over target is Red", container_colour(25, 24)[0] == "Red")
    check("no elapsed is Grey", container_colour(None, 24)[0] == "Grey")


def test_build_and_scope() -> None:
    rows = [
        {"wc_issue_key": "USRE-1", "wc_npi_location": "Singapore",
         "wc_running_duration_workdays": 10, "target_line": 24,
         "wc_resolution": None, "wc_status": "In Progress"},
        # closed: must not reach the board
        {"wc_issue_key": "USRE-2", "wc_npi_location": "Singapore",
         "wc_running_duration_workdays": 30, "target_line": 24,
         "wc_resolution": "Done", "wc_status": "Done"},
        {"wc_issue_key": "POSX-3", "wc_npi_location": "Trutnov",
         "wc_running_duration_workdays": 25, "target_line": 21,
         "wc_resolution": None, "wc_status": "In Progress"},
    ]
    built = build_containers(_wc_table(rows), None, TODAY)
    keys = [c["issueKey"] for c in built]
    check("closed containers are dropped", keys == ["USRE-1", "POSX-3"], str(keys))
    check("target comes from the warehouse",
          all(c["targetSource"] == "warehouse" for c in built))
    check("colours applied", [c["color"] for c in built] == ["Green", "Red"])

    # No target column at all -> TARGETS_V5 fallback, per location.
    bare = [{"wc_issue_key": "USRE-9", "wc_npi_location": "Trutnov",
             "wc_running_duration_workdays": 5}]
    fb = build_containers(_wc_table(bare), None, TODAY)
    check("target falls back to TARGETS_V5 by location",
          fb[0]["target"] == 21 and fb[0]["targetSource"] == "TARGETS_V5",
          str(fb[0]))


def test_wp_grouping() -> None:
    wc = _wc_table([{"wc_issue_key": "USRE-1", "wc_npi_location": "Singapore",
                     "wc_running_duration_workdays": 10, "target_line": 24}])
    wp = _wp_table([
        {"wp_issue_key": "USRE-11", "wc_issue_key": "USRE-1", "wp_name": "Material",
         "wp_running_duration_workdays": 12, "wp_target": 15,
         "wp_resolution": "Done", "wp_end_date": "2026-08-20"},
        {"wp_issue_key": "USRE-12", "wc_issue_key": "USRE-1", "wp_name": "Logistics",
         "wp_running_duration_workdays": 9, "wp_target": 4,
         "wp_resolution": None, "wp_end_date": None},
    ])
    built = build_containers(wc, wp, TODAY)
    pills = built[0]["wpKpis"]
    check("WPs attach to their container", len(pills) == 2, str(pills))
    check("within target is Green",
          next(p for p in pills if p["name"] == "Material")["color"] == "Green")
    check("over target is Red",
          next(p for p in pills if p["name"] == "Logistics")["color"] == "Red")
    check("done state read from resolution",
          next(p for p in pills if p["name"] == "Material")["state"] == "done")
    check("wpsDone counted", built[0]["wpsDone"] == 1)


def test_target_hit_disagreement() -> None:
    # Warehouse says the container hit its target; our numbers say Red.
    rows = [{"wc_issue_key": "USRE-1", "wc_npi_location": "Singapore",
             "wc_running_duration_workdays": 30, "target_line": 24,
             "wc_target_hit": 1}]
    built = build_containers(_wc_table(rows), None, TODAY)
    report = check_colour_agreement(built)
    check("colour vs warehouse target_hit disagreement is caught",
          len(report["disagreements"]) == 1, str(report))

    agree = [{"wc_issue_key": "USRE-1", "wc_npi_location": "Singapore",
              "wc_running_duration_workdays": 10, "target_line": 24,
              "wc_target_hit": 1}]
    ok = check_colour_agreement(build_containers(_wc_table(agree), None, TODAY))
    check("agreement reports no disagreement", ok["disagreements"] == [])


# ═══════════════════════════════════════════════════════════════
# The three failure modes the migration actually risks.
# ═══════════════════════════════════════════════════════════════

def _entry(key, elapsed, target, color, location="Singapore", wps=None):
    return {"issueKey": key, "elapsed": elapsed, "target": target, "color": color,
            "location": location, "wpKpis": wps or []}


def test_diff_detects_systematic_offset() -> None:
    jira = [_entry(f"USRE-{i}", 10 + i, 24, "Green") for i in range(5)]
    tableau = [_entry(f"USRE-{i}", 11 + i, 24, "Green") for i in range(5)]
    cr = source_compare.compare_containers(jira, tableau)
    check("off-by-one shows as a single histogram bucket",
          cr["elapsedDeltaHistogram"] == {"1": 5}, str(cr["elapsedDeltaHistogram"]))
    check("no container counted as agreeing on elapsed",
          cr["agreement"]["elapsed"] == 0)


def test_diff_detects_target_mismatch() -> None:
    jira = [_entry("USRE-1", 3, 4, "Green", "Trutnov")]      # SG Documentation = 4
    tableau = [_entry("USRE-1", 3, 1, "Red", "Trutnov")]     # legacy = 1
    cr = source_compare.compare_containers(jira, tableau)
    check("target mismatch flagged", cr["diffs"][0]["targetDiffers"] is True)
    check("colour mismatch flagged", cr["diffs"][0]["colorDiffers"] is True)
    check("elapsed still agrees", cr["elapsedDeltaHistogram"] == {"0": 1})


def test_diff_detects_scope_mismatch() -> None:
    jira = [_entry("USRE-1", 5, 24, "Green"), _entry("USRE-2", 5, 24, "Green")]
    tableau = [_entry("USRE-2", 5, 24, "Green"), _entry("POSX-9", 5, 21, "Green")]
    cr = source_compare.compare_containers(jira, tableau)
    check("container only in JIRA found", cr["onlyInJira"] == ["USRE-1"])
    check("container only in Tableau found", cr["onlyInTableau"] == ["POSX-9"])
    check("only the shared container is matched", cr["matched"] == 1)
    check("disagreement rate ignores unmatched rows",
          source_compare.disagreement_rate(cr) == 0.0)


def test_diff_reports_full_agreement() -> None:
    same = [_entry("USRE-1", 5, 24, "Green"), _entry("POSX-2", 20, 21, "Yellow")]
    cr = source_compare.compare_containers(same, [dict(e) for e in same])
    check("identical sources produce no diffs", cr["diffs"] == [])
    check("agreement counts everything", cr["agreement"]["color"] == 2)
    check("disagreement rate is zero", source_compare.disagreement_rate(cr) == 0.0)


def main() -> int:
    for fn in (
        test_column_resolution, test_required_fields, test_value_coercion,
        test_colours, test_build_and_scope, test_wp_grouping,
        test_target_hit_disagreement, test_diff_detects_systematic_offset,
        test_diff_detects_target_mismatch, test_diff_detects_scope_mismatch,
        test_diff_reports_full_agreement,
    ):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'=' * 50}\n{_PASSED} passed, {_FAILED} failed")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
