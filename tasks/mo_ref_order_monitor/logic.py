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

import re
from dataclasses import dataclass, field
from datetime import datetime

from core.calendar import SG_HOLIDAYS, business_hours_by_day, fmt_hours

# MO status >= this counts as "closed" for publishing (80 or 90). Below it the
# MO is active; a drop back below it is a re-open.
CLOSED_THRESHOLD = 80

# Section headings owned by THIS tool. Deliberately distinct from the legacy
# Excel->Jira table so both can coexist during the phase-out period.
TRACKING_PREFIX = "h2. MO BUILD TRACKING - "
DWELL_PREFIX = "h3. MO BUILD DWELL - "

# The legacy Excel->Jira table heading. We must NEVER match, rewrite or delete
# it — the old tool keeps maintaining it until it is phased out.
LEGACY_PREFIX = "h2. MO BUILD STATUS - "

# Any wiki heading line, used to bound our section without swallowing
# neighbouring content (legacy tables, other MOs, manual notes).
_HEADING_RE = re.compile(r"h[1-6]\.\s")

# An "issue" is flagged by production by appending IS to the ref order no
# (case-insensitive, at the end) — e.g. "QM IS", "AOI-IS", "QMIS". Webex is
# notified only when this issue state CHANGES (raised / cleared), never on
# routine stage changes. Override via config `issue_regex`.
DEFAULT_ISSUE_REGEX = r"(?i)IS\s*$"


def marker_has_issue(marker: str, pattern: str | None = None) -> bool:
    """True if the ref-order-no value carries the IS issue flag."""
    if not marker:
        return False
    return bool(re.search(pattern or DEFAULT_ISSUE_REGEX, marker.strip()))


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
    last_modified: str = ""           # VHLMDT (diagnostic: replica freshness)
    change_no: str = ""               # VHCHNO (diagnostic)
    changed_by: str = ""              # VHCHID (diagnostic)
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
        "issue_active": False,         # IS flag currently on the ref order no
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
def _issue_actions(state: dict, marker: str, issue_regex: str | None) -> list[Action]:
    """
    Webex gating: notify ONLY when the IS issue flag on the ref order no
    changes state. Routine stage changes stay silent.
      no-IS -> IS : issue_raised
      IS -> no-IS : issue_cleared
    """
    now_issue = marker_has_issue(marker, issue_regex)
    was_issue = bool(state.get("issue_active", False))
    if now_issue == was_issue:
        return []
    state["issue_active"] = now_issue
    return [Action("webex", reason="issue_raised" if now_issue else "issue_cleared",
                   webex_marker=marker)]


def apply_observation(state: dict, obs: Observation,
                      holidays: set | None = None,
                      issue_regex: str | None = None) -> list[Action]:
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
        # A re-open is an exception event — always notify, then re-baseline the
        # IS state so the next raise/clear transition is detected correctly.
        actions.append(Action("webex", reason="reopen", webex_marker=new_marker))
        state["issue_active"] = marker_has_issue(new_marker, issue_regex)
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
                actions.extend(_issue_actions(state, marker, issue_regex))
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
            actions.extend(_issue_actions(state, marker, issue_regex))
        else:
            # Value can change IS-flag without changing stage (e.g. "QM" ->
            # "QM IS"); that is handled above. Nothing to do here for Webex.
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
        # MO close always notifies, regardless of IS state.
        actions.append(Action("publish", reason="closed"))
        actions.append(Action("webex", reason="closed",
                              webex_marker=state.get("current_marker") or ""))
        state["issue_active"] = False
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


# Characters that start a block-level construct in JIRA wiki markup. A cell
# beginning with one gets parsed as a list/heading instead of text — e.g. a
# "# Chg" header rendered as "1. Chg".
_WIKI_LEADERS = ("#", "*", "-", "+", "!", "{", "h1.", "h2.", "h3.")


def _cell(value) -> str:
    """
    Make a value safe inside a JIRA wiki table cell: no newlines, no bare
    pipes (they would split the row), and no leading block markup.
    """
    s = str(value if value is not None else "").replace("\r", " ").replace("\n", " ")
    s = s.replace("|", "\\|").strip()
    if not s:
        return " "
    if s.startswith(_WIKI_LEADERS):
        s = " " + s          # a leading space defuses the markup, renders the same
    return s


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

    # "Changes" not "# Chg" — a leading # is numbered-list markup in JIRA wiki.
    lines = [f"{TRACKING_PREFIX}{mo}", sub,
             "||Day||Ref Order No||Changes||Stages that day||"]
    for day_iso in sorted(state.get("days", {})):
        d = state["days"][day_iso]
        eod = d.get("note") or d.get("end_marker") or ""
        stages = " → ".join(d.get("stages", []))
        lines.append(f"|{_cell(_day_disp(day_iso))}|{_cell(eod)}|"
                     f"{_cell(d.get('changes', 0))}|{_cell(stages)}|")
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
        lines.append(f"|{_cell(r['marker'])}|{_cell(fmt(r['start']))}|"
                     f"{_cell(fmt(r['end']))}|{_cell(days)}|{_cell(daily)}|"
                     f"{_cell(f'{days}d, ' + fmt_hours(r['work_seconds']))}|")
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


def has_section(desc: str, mo_no: str) -> bool:
    """True if this tool's tracking section for `mo_no` is in the description."""
    head = f"{TRACKING_PREFIX}{mo_no}"
    return any(line.strip() == head for line in normalize(desc).split("\n"))


def upsert_mo_section(current_desc: str, state: dict, username: str,
                      timestamp: str) -> str:
    """
    Insert or replace THIS MO's tracking section (tracking table + optional
    dwell block) in the container description.

    Placement: a new section is appended at the END of the description, so it
    always lands *below* the legacy `MO BUILD STATUS` table during the
    phase-out period. An existing section is replaced in place.

    Boundaries are line-based and stop at the next wiki heading of any kind
    (except our own dwell sub-heading), so legacy tables, other MOs' sections
    and manual notes are never consumed.
    """
    desc = normalize(current_desc)
    mo = state["mo_no"]
    tracking_head = f"{TRACKING_PREFIX}{mo}"
    dwell_head = f"{DWELL_PREFIX}{mo}"
    new_section = render_mo_section(state, username, timestamp).rstrip()

    lines = desc.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == tracking_head), None)

    if start is None:
        base = desc.rstrip()
        return (base + "\n\n" if base else "") + new_section + "\n"

    # Our section runs until the next heading that isn't our own dwell block.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if _HEADING_RE.match(s) and s != dwell_head:
            end = j
            break

    before = "\n".join(lines[:start]).rstrip()
    after = "\n".join(lines[end:]).strip()
    parts = [p for p in (before, new_section, after) if p]
    return "\n\n".join(parts) + "\n"
