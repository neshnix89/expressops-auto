"""
Prove the Webex issue-alert state machine works ON THIS MACHINE.

`diagnose.py` answers "is the code present?" by grepping for markers. This
answers the harder question — "does it actually decide correctly?" — by running
the real `logic.apply_observation` over replayed observations and checking which
alerts come out.

Pure logic only: no JIRA, no M3, no Webex, no state files, no network. Nothing
live is touched, so it is safe to run on a production laptop at any time.

    python scripts\\selftest_issue_alerts.py

Exit code 0 = every scenario behaved. 1 = a real regression on this checkout.

The headline case is the one that went wrong on 14-Aug-2026: MO 7003944044 moved
AOI-IS -> PACK-IS and alerted nothing, because the IS flag was a single on/off
latch and it was already on.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.mo_ref_order_monitor.logic import (  # noqa: E402
    Observation, apply_observation, new_state,
)

DAY = 0


def poll(state: dict, marker: str, status: int = 60) -> list[str]:
    """One observation a day apart, returning the webex reasons it produced."""
    global DAY
    DAY += 1
    obs = Observation(mo_no=state["mo_no"], marker=marker, status=status,
                      at=datetime(2026, 8, 1 + DAY, 9, 0))
    actions = apply_observation(state, obs)
    return [a.reason for a in actions if a.kind == "webex"]


def fresh(mo: str = "TEST") -> dict:
    return new_state(mo)


def legacy(marker: str) -> dict:
    """
    A state file as written BEFORE per-stage tracking existed: the issue latch
    is on but there is no `issue_stage` key. Every currently-flagged MO on this
    laptop looks like this until its next transition, so the upgrade path
    matters more than the clean-install one.
    """
    st = fresh("LEGACY")
    st.update(current_marker=marker, current_marker_since="2026-08-01T09:00:00",
              last_status=60, last_poll_date="2026-08-01", issue_active=True,
              days={"2026-08-01": {"stages": [marker], "changes": 0,
                                   "end_marker": marker, "note": ""}})
    st.pop("issue_stage", None)
    return st


# (label, setup, [(marker, expected webex reasons), ...])
SCENARIOS = [
    ("MO 7003944044, the 14-Aug miss", fresh, [
        ("AOI", []),
        ("AOI-IS", ["issue_raised"]),
        ("AOI-IS", []),                      # same issue, still open
        ("PACK-IS", ["issue_moved"]),        # <-- the alert that was lost
        ("PACK", ["issue_cleared"]),
    ]),
    ("routine stage changes stay silent", fresh, [
        ("AOI", []), ("QM", []), ("PACK", []), ("SHIP", []),
    ]),
    ("separator/case noise is the same issue", fresh, [
        ("QM-IS", ["issue_raised"]),
        ("QM IS", []), ("qm.is", []), ("QMIS", []),
        ("S.S-IS", ["issue_moved"]),
    ]),
    ("close and re-open still alert", fresh, [
        ("QM-IS", ["issue_raised"]),
        ("QM", ["issue_cleared"]),
    ]),
    ("pre-upgrade state: noise must NOT false-fire",
     lambda: legacy("PACK-IS"), [("PACK IS", [])]),
    ("pre-upgrade state: a real move must fire",
     lambda: legacy("PACK-IS"), [("SHIP-IS", ["issue_moved"])]),
    ("pre-upgrade state: a clear must fire",
     lambda: legacy("PACK-IS"), [("PACK", ["issue_cleared"])]),
]


def main() -> int:
    global DAY
    failures = 0
    for label, setup, steps in SCENARIOS:
        DAY = 0
        state = setup()
        print(f"\n{label}")
        for marker, want in steps:
            got = poll(state, marker)
            ok = got == want
            failures += not ok
            print(f"  [{'ok  ' if ok else 'FAIL'}] {marker:<9} -> "
                  f"{got or 'silent'}"
                  + ("" if ok else f"   EXPECTED {want or 'silent'}"))

    # A closing MO always reports, regardless of IS state.
    DAY = 0
    state = fresh()
    poll(state, "QM-IS")
    got = poll(state, "QM-IS", status=90)
    ok = got == ["closed"]
    failures += not ok
    print(f"\nMO close always alerts\n  [{'ok  ' if ok else 'FAIL'}] "
          f"sts 90    -> {got}" + ("" if ok else "   EXPECTED ['closed']"))

    if failures:
        print(f"\n{failures} FAILURE(S) — this checkout has a regression.")
        return 1
    print("\nAll scenarios passed. Per-stage issue alerting is live here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
