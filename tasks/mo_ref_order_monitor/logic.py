"""
Pure business logic for mo_ref_order_monitor — no I/O, no API calls.

Everything here is testable with plain dicts so it runs on the VPS with no
JIRA/M3/Webex access. main.py wires these functions to the real clients.

Core responsibilities:
  * status gate / lifecycle state machine (publish while active, go quiet at
    80/90, resume on re-open, abandon when the container closes)
  * change detection on the VHRORN stage marker
  * dwell-time accounting (how long each stage stayed before advancing)
  * assembling the JIRA description content: the per-day `MO BUILD STATUS`
    table (same wiki format Excel->Jira used) + the stage-dwell summary block
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# MO status >= this counts as "closed" for publishing purposes (80 or 90).
# Below this the MO is active and we publish; a drop back below it is a re-open.
CLOSED_THRESHOLD = 80


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass
class Observation:
    """One M3 poll of an MO header (the fields we care about)."""
    mo_no: str
    marker: str          # VHRORN, stripped (the tracked stage, e.g. "QM")
    status: int | None   # VHWHST as int
    highest_status: int | None  # VHWHHS as int
    pn: str = ""         # VHPRNO
    pic: str = ""        # VHRESP (placeholder PIC source)
    at: datetime = field(default_factory=lambda: datetime(1970, 1, 1))


@dataclass
class Action:
    """A decision emitted by the state machine for main.py to execute."""
    kind: str            # publish_status | webex | reopened
    activity: str = ""   # text for the status-table Activity cell
    webex_marker: str = ""
    reason: str = ""     # change | heartbeat | closed | reopen (for logs)


def parse_status(raw: Any) -> int | None:
    """M3 status is a short string like '90'/'20'/' '. Return int or None."""
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
    """Active = status known and below the closed threshold."""
    return status is not None and status < CLOSED_THRESHOLD


# ---------------------------------------------------------------------------
# Dwell-time accounting
# ---------------------------------------------------------------------------
def format_dwell(seconds: float) -> str:
    """Human days+hours, e.g. 183600s -> '2d 3h'. Sub-hour shows minutes."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def close_stage(state: dict, end: datetime) -> None:
    """Move the currently-open stage into history with its computed dwell."""
    marker = state.get("current_marker")
    start_iso = state.get("current_marker_since")
    if not marker or not start_iso:
        return
    start = datetime.fromisoformat(start_iso)
    seconds = (end - start).total_seconds()
    state.setdefault("history", []).append({
        "marker": marker,
        "start": start_iso,
        "end": end.isoformat(),
        "seconds": seconds,
    })


# ---------------------------------------------------------------------------
# State machine — the heart of the poller
# ---------------------------------------------------------------------------
def new_state(mo_no: str) -> dict:
    return {
        "mo_no": mo_no,
        "container_key": None,
        "pn": "",
        "current_marker": None,
        "current_marker_since": None,
        "last_status": None,
        "last_published_date": None,   # 'YYYY-MM-DD' of last status-table write
        "closed_published": False,
        "history": [],
        "abandoned": False,
    }


def apply_observation(state: dict, obs: Observation) -> list[Action]:
    """
    Advance the lifecycle by one poll. Mutates `state`, returns the actions
    main.py should perform (JIRA writes, Webex sends). Pure w.r.t. I/O.

    Rules (from the task spec):
      * active (status < 80):
          - marker changed  -> close prior stage, open new one, publish row
            (Activity = marker) + Webex.
          - first poll of a new day, no change -> publish heartbeat row (no Webex).
          - otherwise -> nothing.
      * crossing into closed (>= 80): publish "MO CLOSED - Sts N" row + dwell
        summary, then go quiet.
      * already closed -> nothing (keep polling silently).
      * drop from closed back to active -> re-open: resume publishing.
    """
    actions: list[Action] = []
    if state.get("abandoned"):
        return actions

    if obs.pn:
        state["pn"] = obs.pn

    today = obs.at.date().isoformat()
    prev_status = state.get("last_status")
    active = is_active(obs.status)
    was_closed = state.get("closed_published", False)

    # --- Re-open: we were closed, status is active again ---
    if was_closed and active:
        state["closed_published"] = False
        # Treat the current marker as freshly starting from now.
        state["current_marker"] = obs.marker or state.get("current_marker")
        state["current_marker_since"] = obs.at.isoformat()
        state["last_published_date"] = today
        actions.append(Action(kind="reopened",
                              activity=f"MO RE-OPENED - Sts {obs.status}",
                              reason="reopen"))
        actions.append(Action(kind="webex", webex_marker=obs.marker,
                              reason="reopen"))
        state["last_status"] = obs.status
        return actions

    # --- Active: normal publishing ---
    if active:
        marker = (obs.marker or "").strip()
        changed = marker and marker != (state.get("current_marker") or "")

        if changed:
            # Close the previous stage, open the new one.
            if state.get("current_marker"):
                close_stage(state, obs.at)
            state["current_marker"] = marker
            state["current_marker_since"] = obs.at.isoformat()
            state["last_published_date"] = today
            actions.append(Action(kind="publish_status", activity=marker,
                                  reason="change"))
            actions.append(Action(kind="webex", webex_marker=marker,
                                  reason="change"))
        else:
            # No marker change. Heartbeat if this is the first write of the day.
            if state.get("last_published_date") != today:
                # Seed marker tracking on the very first sighting.
                if marker and not state.get("current_marker"):
                    state["current_marker"] = marker
                    state["current_marker_since"] = obs.at.isoformat()
                state["last_published_date"] = today
                activity = state.get("current_marker") or marker or "(no ref order no)"
                actions.append(Action(kind="publish_status", activity=activity,
                                      reason="heartbeat"))
        state["last_status"] = obs.status
        return actions

    # --- Closed (status >= 80) ---
    if not was_closed:
        # First time we see it closed: final row + dwell summary, then quiet.
        if state.get("current_marker"):
            close_stage(state, obs.at)
        state["closed_published"] = True
        state["last_published_date"] = today
        actions.append(Action(kind="publish_status",
                              activity=f"MO CLOSED - Sts {obs.status}",
                              reason="closed"))
        # dwell summary is rendered by main.py from state['history']
    # else: already closed -> stay silent.

    state["last_status"] = obs.status
    return actions


# ---------------------------------------------------------------------------
# JIRA description assembly — MO BUILD STATUS table (ported from Excel->Jira)
# ---------------------------------------------------------------------------
def normalize(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def _parse_day_sort_key(day_str: str) -> datetime:
    try:
        return datetime.strptime(day_str, "%d-%b")
    except ValueError:
        return datetime(9999, 12, 31)


def upsert_status_table(current_desc: str, mo_no: str, pn: str, day: str,
                        pic: str, activity: str, username: str,
                        timestamp: str) -> str:
    """
    Insert/overwrite one row (keyed by Day) in the `MO BUILD STATUS - {mo}`
    table inside the container description. Faithful port of the Excel->Jira
    backend so existing container tables stay consistent.
    """
    current_desc = normalize(current_desc)
    table_marker = f"h2. MO BUILD STATUS - {mo_no}"

    new_row = {"pn": pn, "mo_no": mo_no, "day": day, "pic": pic, "activity": activity}

    if table_marker in current_desc:
        table_start = current_desc.find(table_marker)
        # This table's section ends at the next section marker of ANY kind
        # (another MO's status table OR this MO's dwell block) — so a trailing
        # dwell block is preserved, not swallowed into the rebuild.
        next_table_start = _next_section(current_desc,
                                         table_start + len(table_marker))
        if next_table_start != -1:
            table_section = current_desc[table_start:next_table_start]
            after_table = current_desc[next_table_start:]
        else:
            table_section = current_desc[table_start:]
            after_table = ""

        foot_idx = table_section.find("_Last updated by")
        table_wo_foot = (table_section[:foot_idx].rstrip() + "\n"
                         if foot_idx != -1 else table_section)

        existing_rows = []
        for line in table_wo_foot.split("\n"):
            if line.strip().startswith("|") and line.count("|") >= 6 and "||MO #||" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 7:
                    existing_rows.append({
                        "pn": parts[2], "mo_no": parts[3], "day": parts[4],
                        "pic": parts[5], "activity": parts[6],
                    })

        replaced = False
        updated_rows = []
        for row in existing_rows:
            if row.get("day") == day:
                if not replaced:
                    updated_rows.append(new_row)
                    replaced = True
            else:
                updated_rows.append(row)
        if not replaced:
            updated_rows.append(new_row)
        updated_rows.sort(key=lambda r: _parse_day_sort_key(r["day"]))

        header_line = table_wo_foot.split("\n")[0]
        table_header = "||MO #||PN||MO Nr||Day||PIC||Activity||\n"
        sorted_table = header_line + "\n" + table_header
        for idx, row in enumerate(updated_rows, start=1):
            sorted_table += (f"|{idx}|{row['pn']}|{row['mo_no']}|{row['day']}|"
                             f"{row['pic']}|{row['activity']}|\n")
        new_table = sorted_table + f"\n_Last updated by {username} on {timestamp}_\n"
        return current_desc[:table_start] + new_table + after_table

    # Table missing -> create it.
    table = f"\n\n{table_marker}\n"
    table += "||MO #||PN||MO Nr||Day||PIC||Activity||\n"
    table += f"|1|{pn}|{mo_no}|{day}|{pic}|{activity}|\n"
    table += f"\n_Last updated by {username} on {timestamp}_\n"
    return current_desc + table


# ---------------------------------------------------------------------------
# JIRA description assembly — stage dwell summary block
# ---------------------------------------------------------------------------
def build_dwell_summary(state: dict, mo_no: str,
                        include_open_stage_end: datetime | None = None) -> str:
    """
    Render the `MO BUILD DWELL - {mo}` block from state['history']. If
    include_open_stage_end is given, the still-open current stage is appended
    with that end time (used at MO close).
    """
    rows = list(state.get("history", []))
    if include_open_stage_end and state.get("current_marker") and state.get("current_marker_since"):
        start_iso = state["current_marker_since"]
        rows.append({
            "marker": state["current_marker"],
            "start": start_iso,
            "end": include_open_stage_end.isoformat(),
            "seconds": (include_open_stage_end
                        - datetime.fromisoformat(start_iso)).total_seconds(),
        })
    if not rows:
        return ""

    def fmt(iso: str) -> str:
        return datetime.fromisoformat(iso).strftime("%d-%b %H:%M")

    out = [f"h3. MO BUILD DWELL - {mo_no}",
           "||Stage||From||To||Duration||"]
    total = 0.0
    for r in rows:
        total += r["seconds"]
        out.append(f"|{r['marker']}|{fmt(r['start'])}|{fmt(r['end'])}|{format_dwell(r['seconds'])}|")
    out.append(f"|*Total*| | |*{format_dwell(total)}*|")
    return "\n".join(out) + "\n"


# Section markers used to bound blocks when inserting/replacing the dwell block.
_SECTION_PREFIXES = ("h2. MO BUILD STATUS - ", "h3. MO BUILD DWELL - ")


def upsert_dwell_block(current_desc: str, mo_no: str, summary: str) -> str:
    """
    Insert/replace the dwell block for this MO immediately after its status
    table. If `summary` is empty, any existing block is removed.
    """
    desc = normalize(current_desc)
    dwell_marker = f"h3. MO BUILD DWELL - {mo_no}"

    # Remove any existing dwell block for this MO (marker -> next section/end).
    start = desc.find(dwell_marker)
    if start != -1:
        end = _next_section(desc, start + len(dwell_marker))
        desc = desc[:start].rstrip() + "\n" + (desc[end:] if end != -1 else "")

    if not summary.strip():
        return desc

    status_marker = f"h2. MO BUILD STATUS - {mo_no}"
    s_start = desc.find(status_marker)
    if s_start == -1:
        # No status table for this MO — just append.
        return desc.rstrip() + "\n\n" + summary

    insert_at = _next_section(desc, s_start + len(status_marker))
    if insert_at == -1:
        return desc.rstrip() + "\n\n" + summary
    return desc[:insert_at].rstrip() + "\n\n" + summary + "\n" + desc[insert_at:]


def _next_section(desc: str, from_idx: int) -> int:
    """Index of the next section marker at/after from_idx, or -1 if none."""
    positions = [desc.find(p, from_idx) for p in _SECTION_PREFIXES]
    positions = [p for p in positions if p != -1]
    return min(positions) if positions else -1
