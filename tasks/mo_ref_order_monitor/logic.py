"""
Pure business logic for mo_ref_order_monitor — no I/O, no API calls.

Testable with plain dicts (runs on the VPS with no JIRA/M3/Webex access).
main.py wires these to the real clients.

Responsibilities:
  * lifecycle state machine (publish while active, go quiet at status 80/90,
    resume on re-open, abandon when the container closes)
  * change detection on the VHRORN stage marker
  * per-day aggregation: end-of-day stage, number of changes, stages seen
  * dwell-time accounting in WORKING HOURS (08:00-17:00, Mon-Fri, minus SG
    public holidays) via core.calendar
  * assembling the JIRA description: `MO BUILD STATUS` daily table +
    `MO BUILD DWELL` working-hours summary (regenerated from state each write)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.calendar import SG_HOLIDAYS, business_hours_by_day, fmt_hours

# MO status >= this counts as "closed" for publishing (80 or 90). Below it the
# MO is active; a drop back below it is a re-open.
CLOSED_THRESHOLD = 80

STATUS_PREFIX = "h2. MO BUILD STATUS - "
DWELL_PREFIX = "h3. MO BUILD DWELL - "


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass
class Observation:
    """One M3 poll of an MO header (fields we care about)."""
    mo_no: str
    marker: str                       # VHRORN, stripped (tracked stage)
    status: int | None                # VHWHST as int
    highest_status: int | None = None # VHWHHS as int
    pn: str = ""                      # VHPRNO
    order_type: str = ""              # VHORTY
    responsible: str = ""             # VHRESP
    at: datetime = field(default_factory=lambda: datetime(1970, 1, 1))


@dataclass
class Action:
    """A decision for main.py to execute."""
    kind: str            # publish | webex
    reason: str = ""     # initial | change | heartbeat | closed | reopen
    webex_marker: str = ""


def parse_status(raw) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def is_active(status: int | None) -> bool:
    return status is not None and status < CLOSED_THRESHOLD


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def new_state(mo_no: str) -> dict:
    return {
        "mo_no": mo_no,
        "container_key": None,
        "pn": "",
        "order_type": "",
        "responsible": "",
        "current_marker": None,        # running stage across days
        "current_marker_since": None,
        "last_status": None,
        "last_poll_date": None,        # 'YYYY-MM-DD' of last status write
        "closed_published": False,
        "abandoned": False,
        "history": [],                 # completed stages (with work_seconds)
        "days": {},                    # 'YYYY-MM-DD' -> per-day aggregate
    }


def _touch_day(state: dict, day_iso: str, seed_marker: str | None) -> dict:
    days = state["days"]
    if day_iso not in days:
        days[day_iso] = {
            "stages": [seed_marker] if seed_marker else [],
            "changes": 0,
            "end_marker": seed_marker or "",
            "note": "",
        }
    return days[day_iso]


def _close_stage(state: dict, end: datetime,
                 holidays: set | None = None) -> None:
    """Move the open stage into history with its per-working-day dwell."""
    marker = state.get("current_marker")
    start_iso = state.get("current_marker_since")
    if not marker or not start_iso:
        return
    start = datetime.fromisoformat(start_iso)
    by_day = business_hours_by_day(
        start, end, holidays if holidays is not None else SG_HOLIDAYS)
    state.setdefault("history", []).append({
        "marker": marker,
        "start": start_iso,
        "end": end.isoformat(),
        "by_day": by_day,                        # {'YYYY-MM-DD': seconds}
        "work_seconds": sum(by_day.values()),    # total working seconds
    })


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def apply_observation(state: dict, obs: Observation,
                      holidays: set | None = None) -> list[Action]:
    """Advance the lifecycle by one poll. Mutates state, returns actions."""
    actions: list[Action] = []
    if state.get("abandoned"):
        return actions

    if obs.pn:
        state["pn"] = obs.pn
    if obs.order_type:
        state["order_type"] = obs.order_type
    if obs.responsible:
        state["responsible"] = obs.responsible

    today = obs.at.date().isoformat()
    active = is_active(obs.status)
    was_closed = state.get("closed_published", False)
    marker = (obs.marker or "").strip()

    # --- Re-open: was closed, now active again ---
    if was_closed and active:
        state["closed_published"] = False
        new_marker = marker or (state.get("current_marker") or "")
        state["current_marker"] = new_marker
        state["current_marker_since"] = obs.at.isoformat()
        day = _touch_day(state, today, new_marker)
        day["note"] = f"RE-OPENED Sts {obs.status}"
        day["end_marker"] = new_marker
        state["last_poll_date"] = today
        state["last_status"] = obs.status
        actions.append(Action("publish", reason="reopen"))
        actions.append(Action("webex", reason="reopen", webex_marker=new_marker))
        return actions

    # --- Active ---
    if active:
        prev_marker = state.get("current_marker") or ""

        if not prev_marker:
            # First-ever sighting: seed the initial stage (not a "change").
            if marker:
                state["current_marker"] = marker
                state["current_marker_since"] = obs.at.isoformat()
                day = _touch_day(state, today, marker)
                day["stages"] = [marker]
                day["end_marker"] = marker
                day["changes"] = 0
                state["last_poll_date"] = today
                actions.append(Action("publish", reason="initial"))
                actions.append(Action("webex", reason="initial", webex_marker=marker))
            state["last_status"] = obs.status
            return actions

        changed = bool(marker) and marker != prev_marker
        day = _touch_day(state, today, prev_marker)

        if changed:
            _close_stage(state, obs.at, holidays)
            state["current_marker"] = marker
            state["current_marker_since"] = obs.at.isoformat()
            if not day["stages"] or day["stages"][-1] != marker:
                day["stages"].append(marker)
            day["changes"] += 1
            day["end_marker"] = marker
            state["last_poll_date"] = today
            actions.append(Action("publish", reason="change"))
            actions.append(Action("webex", reason="change", webex_marker=marker))
        else:
            # No change: heartbeat row only on the first write of a new day.
            if state.get("last_poll_date") != today:
                day["end_marker"] = prev_marker
                if not day["stages"]:
                    day["stages"] = [prev_marker]
                state["last_poll_date"] = today
                actions.append(Action("publish", reason="heartbeat"))

        state["last_status"] = obs.status
        return actions

    # --- Closed (status >= 80) ---
    if not was_closed:
        if state.get("current_marker"):
            _close_stage(state, obs.at, holidays)
        state["closed_published"] = True
        day = _touch_day(state, today, state.get("current_marker") or "")
        day["note"] = f"CLOSED Sts {obs.status}"
        day["end_marker"] = state.get("current_marker") or ""
        state["last_poll_date"] = today
        actions.append(Action("publish", reason="closed"))
        actions.append(Action("webex", reason="closed",
                              webex_marker=state.get("current_marker") or ""))
    # else already closed -> stay silent.

    state["last_status"] = obs.status
    return actions


# ---------------------------------------------------------------------------
# JIRA description assembly (regenerated from state each write)
# ---------------------------------------------------------------------------
def normalize(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _day_disp(day_iso: str) -> str:
    return datetime.strptime(day_iso, "%Y-%m-%d").strftime("%d-%b")


def render_status_table(state: dict, username: str, timestamp: str) -> str:
    """Daily table: end-of-day stage, # changes, stages seen that day."""
    mo = state["mo_no"]
    pn = state.get("pn", "")
    ot = state.get("order_type", "")
    resp = state.get("responsible", "")

    sub = f"_PN {pn}"
    if ot:
        sub += f" · Order type {ot}"
    if resp:
        sub += f" · Responsible {resp}"
    sub += f" · updated by {username} on {timestamp}_"

    lines = [f"{STATUS_PREFIX}{mo}", sub,
             "||Day||Ref Order No||# Chg||Stages that day||"]
    for day_iso in sorted(state.get("days", {})):
        d = state["days"][day_iso]
        eod = d.get("note") or d.get("end_marker") or ""
        stages = " → ".join(d.get("stages", []))
        lines.append(f"|{_day_disp(day_iso)}|{eod}|{d.get('changes', 0)}|{stages}|")
    return "\n".join(lines) + "\n"


def render_dwell(state: dict) -> str:
    """
    Per-stage dwell from state['history']: distinct working days, the daily
    working-hours breakdown, and the stage total ("{days}d {hours}h" = that
    many working days and that many total working hours).
    """
    rows = list(state.get("history", []))
    if not rows:
        return ""
    mo = state["mo_no"]

    def fmt(iso: str) -> str:
        return datetime.fromisoformat(iso).strftime("%d-%b %H:%M")

    lines = [
        f"{DWELL_PREFIX}{mo}",
        "_Working hours only (08:00-17:00, Mon-Fri, excl. SG public holidays). "
        "'2d, 4h' = 2 working days, 4 working hours total._",
        "||Stage||From||To||Days||Daily working hrs||Total||",
    ]
    grand_by_day: dict[str, float] = {}
    for r in rows:
        by_day = r.get("by_day", {})
        for k, v in by_day.items():
            grand_by_day[k] = grand_by_day.get(k, 0.0) + v
        daily = " · ".join(f"{_day_disp(day)} {fmt_hours(sec)}"
                           for day, sec in sorted(by_day.items())) or "—"
        days = len(by_day)
        lines.append(f"|{r['marker']}|{fmt(r['start'])}|{fmt(r['end'])}|"
                     f"{days}|{daily}|{days}d, {fmt_hours(r['work_seconds'])}|")
    g_days = len(grand_by_day)
    g_secs = sum(grand_by_day.values())
    lines.append(f"|*Total*| | |*{g_days}*| |*{g_days}d, {fmt_hours(g_secs)}*|")
    return "\n".join(lines) + "\n"


def render_mo_section(state: dict, username: str, timestamp: str) -> str:
    """Full MO section = status table (+ dwell block once closed)."""
    section = render_status_table(state, username, timestamp)
    if state.get("closed_published"):
        dwell = render_dwell(state)
        if dwell:
            section = section.rstrip() + "\n\n" + dwell
    return section


def upsert_mo_section(current_desc: str, state: dict, username: str,
                      timestamp: str) -> str:
    """
    Replace this MO's whole section (status table + optional dwell) in the
    container description, bounded by the next MO's status marker. Regenerated
    from state, so no fragile row parsing.
    """
    desc = normalize(current_desc)
    mo = state["mo_no"]
    marker_line = f"{STATUS_PREFIX}{mo}"
    new_section = render_mo_section(state, username, timestamp).rstrip() + "\n"

    start = desc.find(marker_line)
    if start == -1:
        base = desc.rstrip()
        return (base + "\n\n" if base else "") + new_section

    # Section ends at the next MO's status marker (or end of description).
    nxt = desc.find(STATUS_PREFIX, start + len(marker_line))
    before = desc[:start].rstrip()
    after = desc[nxt:] if nxt != -1 else ""
    parts = []
    if before:
        parts.append(before)
    parts.append(new_section.rstrip())
    if after.strip():
        parts.append(after.strip())
    return "\n\n".join(parts) + "\n"
