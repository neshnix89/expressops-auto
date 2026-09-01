"""
tasks/kpi_overlay/source_tableau.py — build the overlay cache from Tableau's
KPI fact tables instead of recomputing the KPIs from JIRA.

The output is deliberately the SAME shape ``logic.compute_live_kpi`` produces,
so ``main.py``, ``kpi_cache.json`` and the Tampermonkey userscript are all
unchanged. Only the provenance differs: every ``elapsed`` here was computed by
the warehouse job that also feeds the Tableau workbook, so the Kanban pills and
the dashboard can no longer disagree.

What this module does NOT do is re-derive anything. If the warehouse gives a
target, that target is used; TARGETS_V5 is only a fallback for a column the
warehouse does not carry. The one thing computed locally is the pill COLOUR,
because Green/Yellow/Red with a 2-day amber band is an overlay concept the
warehouse has no column for — and when the warehouse *does* carry its own
hit/miss verdict, ``check_colour_agreement`` reports every row where the two
disagree rather than quietly preferring one.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from core.kpi_core import (
    normalize_location,
    targets_for,
    to_date,
)
from tasks.kpi_overlay.logic import (
    DONE_RESOLUTIONS,
    YELLOW_THRESHOLD,
    build_wp_config,
)

# Resolution / status values that mean "this container is finished". The
# overlay only ever draws pills on open containers, and the fact tables carry
# closed ones too (the workbook has separate Running and Closed views).
CLOSED_RESOLUTIONS = {
    "done", "acknowledged", "closed", "completed", "cancelled", "canceled",
    "won't do", "wont do", "rejected", "duplicate",
}
CLOSED_STATUSES = {"done", "closed", "completed", "cancelled", "canceled", "resolved"}

# Warehouse hit/miss values, folded to lowercase.
HIT_TRUE = {"1", "true", "y", "yes", "hit", "green", "ok", "on target", "in target"}
HIT_FALSE = {"0", "false", "n", "no", "miss", "red", "nok", "off target", "late"}


def to_num(val: Any) -> float | None:
    """Best-effort numeric read of a warehouse cell (Decimal, str, int, ...)."""
    if val is None:
        return None
    if isinstance(val, bool):
        return float(val)
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_days(val: Any) -> int | None:
    """A workday count as an int. Warehouses hand these back as Decimal/str."""
    num = to_num(val)
    if num is None:
        return None
    return int(round(num))


def hit_verdict(val: Any) -> bool | None:
    """Interpret a warehouse target_hit cell. None when it says nothing useful."""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    text = str(val).strip().lower()
    if text in HIT_TRUE:
        return True
    if text in HIT_FALSE:
        return False
    num = to_num(val)
    if num is not None:
        return num != 0
    return None


def _is_open(table, row: dict[str, Any]) -> bool:
    """True when a fact row is a container/WP still running.

    Conservative on purpose: a row that says nothing about being finished is
    kept. Dropping a live container is a visible regression (its pill vanishes
    from the board); keeping a closed one is not, because the userscript only
    looks up cards that are on the Kanban in the first place.
    """
    resolution = table.value(row, "resolution")
    if resolution is not None and str(resolution).strip().lower() in CLOSED_RESOLUTIONS:
        return False
    if table.value(row, "resolved") is not None:
        return False
    status = table.value(row, "status")
    if status is not None and str(status).strip().lower() in CLOSED_STATUSES:
        return False
    return True


def _passes_row_filter(row: dict[str, Any], row_filter: dict[str, Any]) -> bool:
    """Apply the optional config `kpi_warehouse.row_filter` (raw column names).

    Exists so the running/closed split can be pinned to whatever flag column
    the warehouse actually has, once discovery names it, without a code change:

        row_filter:
          wc_kpi_state: ["Running"]
    """
    for column, allowed in (row_filter or {}).items():
        if column not in row:
            continue
        wanted = {str(a).strip().lower() for a in
                  (allowed if isinstance(allowed, (list, tuple, set)) else [allowed])}
        if str(row.get(column) or "").strip().lower() not in wanted:
            return False
    return True


def container_colour(elapsed: int | None, target: int | None) -> tuple[str, int | None]:
    """Overlay Green/Yellow/Red for a container. Same rule as the JIRA source."""
    if elapsed is None or target is None:
        return "Grey", None
    remaining = target - elapsed
    if elapsed > target:
        return "Red", remaining
    if remaining <= YELLOW_THRESHOLD:
        return "Yellow", remaining
    return "Green", remaining


def wp_colour(elapsed: int | None, target: int | None, state: str) -> str:
    """Overlay colour for one WP pill built from warehouse numbers.

    Deliberately simpler than the JIRA path's tech-prep rule: that rule needs
    the Material-fullset date, which the overlay derives from JIRA resolution
    dates. Where the warehouse publishes its own verdict, ``target_hit`` is
    reported by :func:`check_colour_agreement` instead of being second-guessed.
    """
    if state in ("waiting", "skipped") or elapsed is None or target is None:
        return "Grey"
    return "Green" if elapsed <= target else "Red"


def _wp_state(table, row: dict[str, Any]) -> str:
    resolution = str(table.value(row, "resolution") or "").strip()
    end_date = table.value(row, "end_date")
    if resolution and resolution not in DONE_RESOLUTIONS:
        return "skipped"
    if resolution in DONE_RESOLUTIONS or end_date is not None:
        return "done"
    return "active"


def build_wp_entries(wp_table, logger=None) -> dict[str, list[dict]]:
    """Group Work-Package fact rows into per-container pill lists."""
    by_container: dict[str, list[dict]] = {}
    if wp_table is None:
        return by_container

    for row in wp_table.rows:
        wp_key = wp_table.value(row, "issue_key")
        container_key = wp_table.value(row, "container_key")
        if not wp_key or not container_key:
            continue
        if not _is_open(wp_table, row) and _wp_state(wp_table, row) == "active":
            continue

        name = str(wp_table.value(row, "name", "") or "")
        location = normalize_location(wp_table.value(row, "location"))
        elapsed = to_days(wp_table.value(row, "elapsed"))
        if elapsed is None:
            elapsed = to_days(wp_table.value(row, "duration"))

        target = to_days(wp_table.value(row, "target"))
        if target is None:
            cfg = build_wp_config(location).get(name.strip().lower())
            target = cfg["target"] if cfg else None

        state = _wp_state(wp_table, row)
        entry = {
            "issueKey": str(wp_key),
            "name": name,
            "target": target,
            "elapsed": elapsed,
            "color": wp_colour(elapsed, target, state),
            "state": state,
            "startDate": _iso(wp_table.value(row, "start_date")),
            "endDate": _iso(wp_table.value(row, "end_date")),
            "containerKey": str(container_key),
            "targetHit": hit_verdict(wp_table.value(row, "target_hit")),
        }
        by_container.setdefault(str(container_key), []).append(entry)

    if logger is not None:
        logger.info("  WP fact rows grouped under %d container(s)", len(by_container))
    return by_container


def _iso(val: Any) -> str | None:
    d = to_date(val)
    return str(d) if d else None


def build_containers(wc_table, wp_table, today: date, row_filter: dict | None = None,
                     logger=None) -> list[dict]:
    """Turn the WC (+WP) fact tables into overlay cache entries."""
    wc_table.require()
    if wp_table is not None:
        wp_table.require()

    wps_by_container = build_wp_entries(wp_table, logger=logger)

    containers: list[dict] = []
    dropped_closed = 0
    dropped_filter = 0
    dropped_no_key = 0

    for row in wc_table.rows:
        key = wc_table.value(row, "issue_key")
        if not key:
            dropped_no_key += 1
            continue
        if not _passes_row_filter(row, row_filter or {}):
            dropped_filter += 1
            continue
        if not _is_open(wc_table, row):
            dropped_closed += 1
            continue

        key = str(key)
        location = normalize_location(wc_table.value(row, "location"))

        elapsed = to_days(wc_table.value(row, "elapsed"))
        if elapsed is None:
            elapsed = to_days(wc_table.value(row, "duration"))

        target = to_days(wc_table.value(row, "target"))
        target_source = "warehouse"
        if target is None:
            target = targets_for(location)["T_NPI"]
            target_source = "TARGETS_V5"

        color, remaining = container_colour(elapsed, target)
        wp_kpis = wps_by_container.get(key, [])
        wps_done = sum(1 for w in wp_kpis if w["state"] == "done")

        containers.append({
            "issueKey": key,
            "summary": str(wc_table.value(row, "summary", "") or ""),
            "status": str(wc_table.value(row, "status", "") or ""),
            "assignee": str(wc_table.value(row, "assignee", "") or ""),
            "location": location,
            "orderType": str(wc_table.value(row, "order_type", "") or ""),
            "projectId": str(wc_table.value(row, "project_id", "") or ""),
            "npiStart": _iso(wc_table.value(row, "npi_start")),
            "elapsed": elapsed,
            "target": target,
            "targetSource": target_source,
            "remaining": remaining,
            "color": color,
            "parked": str(wc_table.value(row, "parked_status", "") or ""),
            "parkingPeriods": [],          # the warehouse publishes the net number,
                                           # not the spans it subtracted
            "wpsDone": wps_done,
            "wpsTotal": len(wp_kpis),
            "workPackages": [],            # JIRA-only detail; not in the fact tables
            "wpKpis": wp_kpis,
            "targetHit": hit_verdict(wc_table.value(row, "target_hit")),
            "source": "tableau",
        })

    if logger is not None:
        logger.info(
            "  Built %d container(s) from the fact tables "
            "(dropped: %d closed, %d filtered, %d without a key)",
            len(containers), dropped_closed, dropped_filter, dropped_no_key,
        )
    return containers


def check_colour_agreement(containers: list[dict], logger=None) -> dict[str, Any]:
    """Compare our pill colour with the warehouse's own hit/miss verdict.

    Red vs hit=True (or Green vs hit=False) means the two sides disagree about
    the same container on the same numbers — a target mismatch, not rounding.
    Yellow is excluded: it is an overlay-only warning band with no warehouse
    equivalent, so it can never "disagree".
    """
    checked = 0
    disagreements = []
    for c in containers:
        hit = c.get("targetHit")
        if hit is None or c["color"] == "Grey":
            continue
        checked += 1
        ours_hit = c["color"] in ("Green", "Yellow")
        if ours_hit != hit:
            disagreements.append({
                "issueKey": c["issueKey"],
                "ourColor": c["color"],
                "elapsed": c["elapsed"],
                "target": c["target"],
                "targetSource": c.get("targetSource"),
                "warehouseTargetHit": hit,
            })

    if logger is not None and disagreements:
        logger.warning(
            "  %d/%d container(s) disagree with the warehouse target_hit column",
            len(disagreements), checked,
        )
        for d in disagreements[:10]:
            logger.warning(
                "    %s: ours=%s (%s/%s from %s) vs warehouse hit=%s",
                d["issueKey"], d["ourColor"], d["elapsed"], d["target"],
                d["targetSource"], d["warehouseTargetHit"],
            )
    return {"checked": checked, "disagreements": disagreements}


def staleness_hours(wc_table, now) -> float | None:
    """Hours since the newest as-of date in the snapshot, or None if unknown."""
    newest = None
    for row in wc_table.rows:
        d = to_date(wc_table.value(row, "as_of"))
        if d is not None and (newest is None or d > newest):
            newest = d
    if newest is None:
        return None
    delta = now.date() - newest if hasattr(now, "date") else now - newest
    return delta.days * 24.0
