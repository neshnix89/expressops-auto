"""
Pure-logic checks for costing_hs_code_trigger — runnable on the VPS with no
JIRA access:

    python -m tasks.costing_hs_code_trigger.test_logic

Covers the trigger gate (DMR + WP branches), per-person done detection with
the negation guard, working-day reminder timing, and the full ``decide()``
state machine.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TASK_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tasks.costing_hs_code_trigger.logic import (
    ACTION_NOOP,
    ACTION_REMIND,
    ACTION_TRIGGER,
    STATE_BASELINE_SKIP,
    Decision,
    apply_baseline,
    build_people,
    check_trigger_ready,
    decide,
    is_dmr,
    person_is_done,
    working_days_elapsed,
)

TODAY = date(2026, 7, 20)  # Monday

TASK_CONFIG = {
    "costing_people": [
        {"username": "kloo", "display": "Loo King Lun"},
        {"username": "yuhuang", "display": "Yu Huang"},
    ],
    "hs_code_person": {"username": "fpangilina", "display": "F. Pangilina"},
    "ready_resolutions": ["Done", "Acknowledged", "Won't Do"],
    "done_keywords": ["done", "updated", "completed"],
    "negation_guards": ["not done", "no update", "pending"],
    "reminder_interval_working_days": 2,
    "trigger_marker": "#Ref: CostHS-Trigger#",
    "reminder_marker": "#Ref: CostHS-Reminder#",
    "messages": {
        "trigger": (
            "Hi {costing_mentions}, please update the *Costing*.\n"
            "{hs_mention}, please update the *HS Code*.\n\n"
            'Reply "Done" when complete.'
        ),
        "reminder": "Reminder, still pending:\n{outstanding_lines}",
    },
}

PEOPLE = build_people(TASK_CONFIG)

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


def _dmr(order_type: str) -> dict:
    return {"fields": {"customfield_13905": {"value": order_type}}}


def _wp(summary: str, resolution: str | None) -> dict:
    return {
        "fields": {
            "summary": summary,
            "resolution": {"name": resolution} if resolution else None,
        }
    }


def _all_wps_ready() -> list[dict]:
    return [
        _wp("Material", "Done"),
        _wp("PCB", "Done"),
        _wp("Routing - TechnPrep", "Won't Do"),
        _wp("PE - TechnPrep", "Done"),
        _wp("te - technprep", "Done"),  # case-insensitive match
    ]


def _comment(author: str, created: str, body: str) -> dict:
    return {"author": {"name": author}, "created": created, "body": body}


TRIGGER_BODY = "trigger ... #Ref: CostHS-Trigger#"


def test_is_dmr() -> None:
    check("is_dmr true", is_dmr(_dmr("DMR - Direct manufacturing release")))
    check("is_dmr false (pilot)", not is_dmr(_dmr("PR – Pilot Run")))
    check("is_dmr false (empty)", not is_dmr({"fields": {}}))


def test_trigger_gate() -> None:
    # DMR → ready regardless of WPs
    ready, _ = check_trigger_ready(_dmr("DMR - Direct manufacturing release"), [])
    check("DMR ready with no WPs", ready)

    # Non-DMR, all WPs resolved → ready
    ready, reasons = check_trigger_ready(_dmr("PR – Pilot Run"), _all_wps_ready())
    check("non-DMR ready when 5 WPs resolved", ready, str(reasons))

    # Non-DMR, PCB unresolved → not ready
    wps = _all_wps_ready()
    wps[1] = _wp("PCB", None)
    ready, reasons = check_trigger_ready(_dmr("PR – Pilot Run"), wps)
    check("non-DMR blocked by unresolved PCB", not ready and any("pcb" in r for r in reasons))

    # Non-DMR, WP missing entirely → not ready
    ready, reasons = check_trigger_ready(_dmr("QS – Qualification sample"), _all_wps_ready()[:4])
    check("non-DMR blocked by missing WP", not ready)

    # "Acknowledged" counts as resolved (matches mo_trigger_comment)
    wps = _all_wps_ready()
    wps[0] = _wp("Material", "Acknowledged")
    ready, reasons = check_trigger_ready(_dmr("PR – Pilot Run"), wps, TASK_CONFIG["ready_resolutions"])
    check("Acknowledged counts as ready", ready, str(reasons))


def test_person_done() -> None:
    since = None
    comments = [
        _comment("kloo", "2026-07-16T10:00:00+0800", "Costing done"),
        _comment("yuhuang", "2026-07-16T10:00:00+0800", "not done yet"),
        _comment("fpangilina", "2026-07-16T10:00:00+0800", "looking into it"),
    ]
    check("alice done (keyword)",
          person_is_done(comments, "kloo", since, ["done"], ["not done"]))
    check("bob NOT done (negation guard)",
          not person_is_done(comments, "yuhuang", since, ["done"], ["not done"]))
    check("carol NOT done (no keyword)",
          not person_is_done(comments, "fpangilina", since, ["done"], ["not done"]))
    check("blank username never done",
          not person_is_done(comments, "", since, ["done"], []))

    # Done comment must be AFTER the trigger baseline.
    from datetime import datetime
    base = datetime(2026, 7, 15, 9, 0, 0)
    old = [_comment("kloo", "2026-07-14T10:00:00+0800", "done")]
    check("pre-trigger done ignored",
          not person_is_done(old, "kloo", base, ["done"], []))


def test_working_days() -> None:
    # Fri 2026-07-17 -> Mon 2026-07-20 = 1 working day (Sat/Sun skipped)
    check("Fri->Mon = 1 wd",
          working_days_elapsed(date(2026, 7, 17), TODAY) == 1,
          str(working_days_elapsed(date(2026, 7, 17), TODAY)))
    # Wed 2026-07-15 -> Mon 2026-07-20 = 3 working days
    check("Wed->Mon = 3 wd",
          working_days_elapsed(date(2026, 7, 15), TODAY) == 3,
          str(working_days_elapsed(date(2026, 7, 15), TODAY)))
    check("same day = 0 wd", working_days_elapsed(TODAY, TODAY) == 0)


def _decide(issue: dict, wps: list[dict]) -> "object":
    return decide(issue=issue, wps=wps, people=PEOPLE, task_config=TASK_CONFIG, today=TODAY)


def test_decide_trigger_dmr() -> None:
    issue = {"key": "D-1", "fields": {
        "customfield_13905": {"value": "DMR - Direct manufacturing release"},
        "comment": {"comments": []},
    }}
    d = _decide(issue, [])
    check("DMR untriggered → TRIGGER", d.action == ACTION_TRIGGER, d.state)
    check("trigger body tags all 3",
          all(m in d.body for m in ("[~kloo]", "[~yuhuang]", "[~fpangilina]")))


def test_decide_trigger_wp() -> None:
    issue = {"key": "W-1", "fields": {
        "customfield_13905": {"value": "PR – Pilot Run"},
        "comment": {"comments": []},
    }}
    check("WP-ready untriggered → TRIGGER", _decide(issue, _all_wps_ready()).action == ACTION_TRIGGER)

    issue2 = {"key": "W-2", "fields": {
        "customfield_13905": {"value": "PR – Pilot Run"},
        "comment": {"comments": []},
    }}
    wps = _all_wps_ready()
    wps[0] = _wp("Material", None)
    d = _decide(issue2, wps)
    check("WP-not-ready → NOOP not_ready", d.action == ACTION_NOOP and d.state == "not_ready")


def test_decide_waiting() -> None:
    issue = {"key": "T-1", "fields": {
        "customfield_13905": {"value": "DMR - Direct manufacturing release"},
        "comment": {"comments": [
            _comment("svc.automation", "2026-07-17T09:00:00+0800", TRIGGER_BODY),
        ]},
    }}
    d = _decide(issue, [])
    check("triggered Fri, nobody done → waiting", d.action == ACTION_NOOP and d.state == "waiting", d.reason)


def test_decide_remind() -> None:
    issue = {"key": "T-2", "fields": {
        "customfield_13905": {"value": "DMR - Direct manufacturing release"},
        "comment": {"comments": [
            _comment("svc.automation", "2026-07-15T09:00:00+0800", TRIGGER_BODY),
            _comment("kloo", "2026-07-16T10:00:00+0800", "Costing done"),
            _comment("yuhuang", "2026-07-16T11:00:00+0800", "not done yet"),
        ]},
    }}
    d = _decide(issue, [])
    check("due, partial done → REMIND", d.action == ACTION_REMIND, f"{d.state}/{d.reason}")
    check("reminder omits done alice", "[~kloo]" not in (d.body or ""))
    check("reminder tags outstanding bob", "[~yuhuang]" in (d.body or ""))
    check("reminder tags outstanding carol", "[~fpangilina]" in (d.body or ""))
    check("reminder groups by track",
          "Costing:" in (d.body or "") and "HS Code:" in (d.body or ""))


def test_decide_complete() -> None:
    issue = {"key": "T-3", "fields": {
        "customfield_13905": {"value": "DMR - Direct manufacturing release"},
        "comment": {"comments": [
            _comment("svc.automation", "2026-07-15T09:00:00+0800", TRIGGER_BODY),
            _comment("kloo", "2026-07-16T10:00:00+0800", "done"),
            _comment("yuhuang", "2026-07-16T10:00:00+0800", "updated"),
            _comment("fpangilina", "2026-07-16T10:00:00+0800", "completed"),
        ]},
    }}
    d = _decide(issue, [])
    check("all done → NOOP complete", d.action == ACTION_NOOP and d.state == "complete", d.state)


def test_never_retrigger() -> None:
    # Already triggered + WP gate would also pass: must NOT re-trigger.
    issue = {"key": "R-1", "fields": {
        "customfield_13905": {"value": "PR – Pilot Run"},
        "comment": {"comments": [
            _comment("svc.automation", "2026-07-15T09:00:00+0800", TRIGGER_BODY),
        ]},
    }}
    d = _decide(issue, _all_wps_ready())
    check("triggered container never re-triggers", d.action != ACTION_TRIGGER, d.state)


def test_apply_baseline() -> None:
    trig = Decision(key="X-1", action=ACTION_TRIGGER, state="trigger", body="hi")
    out = apply_baseline(trig, {"X-1"})
    check("baseline suppresses trigger",
          out.action == ACTION_NOOP and out.state == STATE_BASELINE_SKIP)
    check("non-baseline trigger passes through",
          apply_baseline(trig, {"OTHER"}).action == ACTION_TRIGGER)
    rem = Decision(key="X-1", action=ACTION_REMIND, state="remind", body="hi")
    check("baseline never suppresses a reminder",
          apply_baseline(rem, {"X-1"}).action == ACTION_REMIND)


def main() -> int:
    for fn in (
        test_is_dmr, test_trigger_gate, test_person_done, test_working_days,
        test_decide_trigger_dmr, test_decide_trigger_wp, test_decide_waiting,
        test_decide_remind, test_decide_complete, test_never_retrigger,
        test_apply_baseline,
    ):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'=' * 50}\n{_PASSED} passed, {_FAILED} failed")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
