"""
tasks/kpi_overlay/logic.py — pure KPI computation for the live JIRA overlay.

Migrated from the container/WP KPI logic in the legacy
``LiveKPI_Overlay/live_kpi.py``. This module is pure (no JIRA/Confluence/file
I/O) so it is testable with plain dicts and mock data.

Location-aware: every elapsed-day calculation and every target now flows from
the container's NPI Location (Singapore or Trutnov) via core.kpi_core, so the
same code renders both boards. Singapore keeps Overall=24 / Logistics=4 /
Documentation=4; Trutnov uses Overall=21 / Logistics=1 / Documentation=1, and
each site's own holiday calendar drives the working-day math.
"""

from __future__ import annotations

import re
from datetime import date

from core.kpi_core import (
    OFFICIAL_WP_NAMES,
    targets_for,
    normalize_location,
    to_date,
    fNetWorkdays,
    netWorkdaysRaw,
    _get_field_value,
    CF_ORDER_TYPE,
    CF_PARKED_LOG,
    CF_PROJECT_ID,
    CF_NPI_LOCATION,
)

# Extended Work Package list for the live overlay: standard NPI WPs plus the
# DMR alternate flow. The container-level T_NPI target still applies to all.
OVERLAY_WP_NAMES = OFFICIAL_WP_NAMES + ["Direct Manufacturing Release"]

DONE_RESOLUTIONS = {"Done", "Acknowledged"}

# Container is "at risk" (Yellow) when remaining working days <= this.
YELLOW_THRESHOLD = 2


def build_wp_config(location: str) -> dict:
    """Build the per-WP overlay config for a location.

    Structure (start strategy / pill / kind) is fixed; every ``target`` is
    sourced from core.kpi_core.TARGETS_V5 for the given location so the target
    tables stay the single source of truth. Only Logistics and Documentation
    actually differ between Singapore and Trutnov.

    Start strategies:
      "own"           — WP's own JIRA creation date
      "material_full" — max(Material, PCB) resolution date
      "smt_build"     — SMT Build WP resolution date
    """
    t = targets_for(location)
    return {
        "material":                 {"target": t["T_Material"],     "start": "own",           "pill": True,  "kind": "standard"},
        "pcb":                      {"target": t["T_PCB"],          "start": "own",           "pill": True,  "kind": "standard"},
        "routing - technprep":      {"target": t["T_Routing"],      "start": "own",           "pill": True,  "kind": "techprep"},
        "pe - technprep":           {"target": t["T_PE"],           "start": "own",           "pill": True,  "kind": "techprep"},
        "te - technprep":           {"target": t["T_TE"],           "start": "own",           "pill": True,  "kind": "techprep"},
        "smt build":                {"target": t["T_SMT Build"],    "start": "material_full", "pill": True,  "kind": "standard"},
        "qm p+l":                   {"target": 0,                   "start": "own",           "pill": False, "kind": "standard"},
        "logistics":                {"target": t["T_Logistic"],     "start": "smt_build",     "pill": True,  "kind": "standard"},
        "documentation":            {"target": t["T_Documentation"], "start": "smt_build",    "pill": True,  "kind": "standard"},
        "direct manufacturing release": {"target": t["T_NPI"],      "start": "own",           "pill": True,  "kind": "standard"},
    }


# ═══════════════════════════════════════════════════════════════
# PARKING (multi park/unpark cycles)
# ═══════════════════════════════════════════════════════════════

def parse_parked_log_multi(parked_str):
    """Parse Issue_parked_log into ALL parking pairs as (start_date, end_date).

    Each field is a ``date`` or None. A trailing Start with no End (currently
    parked) yields a pair ending in None.
    """
    if not parked_str or not isinstance(parked_str, str):
        return []

    tokens = re.findall(
        r'(Start|End):\s*(\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}:\d{2})?',
        parked_str,
    )

    pairs = []
    pending_start = None
    for label, datestr in tokens:
        d = to_date(datestr)
        if label == "Start":
            if pending_start is not None:
                pairs.append((pending_start, None))
            pending_start = d
        elif label == "End":
            if pending_start is not None:
                pairs.append((pending_start, d))
                pending_start = None
    if pending_start is not None:
        pairs.append((pending_start, None))
    return pairs


def elapsed_wd(start_date, end_date, parking_pairs, location):
    """Elapsed working days start→end, subtracting overlap with parking pairs.

    Currently-parked (open pair) freezes elapsed at that park's start; earlier
    completed parks inside the window are still subtracted. Uses ``location``'s
    holiday calendar throughout.
    """
    if start_date is None or end_date is None:
        return None

    effective_end = end_date
    currently_parked_start = None
    closed_parks = []
    for ps, pe in parking_pairs:
        if pe is None:
            currently_parked_start = ps
        else:
            closed_parks.append((ps, pe))

    if currently_parked_start is not None:
        effective_end = min(end_date, currently_parked_start)

    raw = fNetWorkdays(start_date, effective_end, location)
    if raw is None or raw <= 0:
        return raw if raw is not None else 0

    total_parked_overlap = 0
    for ps, pe in closed_parks:
        ov_start = max(start_date, ps) if ps is not None else start_date
        ov_end = min(effective_end, pe) if pe is not None else effective_end
        if ov_start <= ov_end:
            total_parked_overlap += netWorkdaysRaw(ov_start, ov_end, location)

    elapsed = raw - total_parked_overlap
    return max(0, elapsed)


# ═══════════════════════════════════════════════════════════════
# PER-WP KPI
# ═══════════════════════════════════════════════════════════════

def compute_wp_kpis(official_wps, npi_start, parking_pairs, today, location,
                    wp_config, logger=None, wc_key=""):
    """Compute per-WP KPI pill entries for one container (pure)."""
    # ─── Pass 1: key dates other WPs depend on ───
    material_resolved = None
    pcb_resolved = None
    smt_build_resolved = None
    for wp in official_wps:
        name_lower = wp["summary"].strip().lower()
        if wp["resolution"] in DONE_RESOLUTIONS and wp["resolved"] is not None:
            if name_lower == "material":
                material_resolved = wp["resolved"]
            elif name_lower == "pcb":
                pcb_resolved = wp["resolved"]
            elif name_lower == "smt build":
                smt_build_resolved = wp["resolved"]

    if material_resolved is not None and pcb_resolved is not None:
        material_fullset = max(material_resolved, pcb_resolved)
    else:
        material_fullset = None

    # ─── Pass 2: each WP's KPI ───
    wp_kpis = []
    for wp in official_wps:
        name_lower = wp["summary"].strip().lower()
        config = wp_config.get(name_lower)
        if config is None or not config.get("pill"):
            continue

        target = config["target"]
        strategy = config["start"]
        kind = config["kind"]
        is_done = wp["resolution"] in DONE_RESOLUTIONS

        # Skipped WP: resolved but NOT Done/Acknowledged (Won't Do, Cancelled).
        is_skipped = (
            wp["resolution"] not in (None, "", *DONE_RESOLUTIONS)
            and wp["resolved"] is not None
        )

        # ─── Start date ───
        start_date = None
        state = "active"
        if strategy == "own":
            start_date = wp["created"]
        elif strategy == "material_full":
            start_date = material_fullset
            if start_date is None:
                state = "waiting"
        elif strategy == "smt_build":
            start_date = smt_build_resolved
            if start_date is None:
                state = "waiting"

        # ─── End date & elapsed ───
        elapsed = None
        end_date = None
        if is_skipped:
            state = "skipped"
            end_date = wp["resolved"]
        elif state == "waiting":
            elapsed = None
        elif is_done:
            state = "done"
            end_date = wp["resolved"]
            elapsed = elapsed_wd(start_date, end_date, parking_pairs, location)
        else:
            end_date = today
            elapsed = elapsed_wd(start_date, end_date, parking_pairs, location)

        # ─── Color ───
        color = "Grey"
        if state in ("waiting", "skipped"):
            color = "Grey"
        elif kind == "techprep":
            if is_done:
                if elapsed is not None and elapsed <= target:
                    color = "Green"
                elif material_fullset is None:
                    color = "Green"
                elif end_date is not None and end_date <= material_fullset:
                    color = "Green"
                else:
                    color = "Red"
            else:
                if elapsed is None:
                    color = "Grey"
                elif elapsed <= 3:
                    color = "Green"
                elif material_fullset is None:
                    color = "Yellow"
                else:
                    color = "Green" if elapsed <= target else "Red"
        else:
            if elapsed is None:
                color = "Grey"
            elif elapsed <= target:
                color = "Green"
            else:
                color = "Red"

        wp_kpis.append({
            "issueKey": wp["key"],
            "name": wp["summary"],
            "target": target,
            "elapsed": elapsed,
            "color": color,
            "state": state,
            "startDate": str(start_date) if start_date else None,
            "endDate": str(end_date) if end_date else None,
            "containerKey": wc_key,
        })

        if logger is not None:
            elapsed_str = "-" if elapsed is None else str(elapsed)
            logger.debug(
                "    WP %s %s: %s/%s %s [%s]",
                wp["key"], wp["summary"], elapsed_str, target, color, state,
            )

    return wp_kpis


# ═══════════════════════════════════════════════════════════════
# CONTAINER KPI
# ═══════════════════════════════════════════════════════════════

def compute_live_kpi(wc_issue, wp_list, today, logger=None):
    """Compute container-level live KPI for one open Work Container (pure).

    ``wc_issue`` is a JIRA issue dict; ``wp_list`` is a list of
    (wp_key, wp_fields) for its child Work Packages. Returns a cache entry
    dict or None if not computable.
    """
    wc_key = wc_issue["key"]
    wf = wc_issue["fields"]

    # ─── Location drives targets AND holiday calendar ───
    raw_location = _get_field_value(wf, CF_NPI_LOCATION, "")
    location = normalize_location(raw_location)
    target = targets_for(location)["T_NPI"]
    wp_config = build_wp_config(location)

    # ─── Parking (multi park/unpark cycles) ───
    parked_log_str = _get_field_value(wf, CF_PARKED_LOG)
    parking_pairs = parse_parked_log_multi(parked_log_str)
    if parking_pairs:
        _, last_end = parking_pairs[-1]
        parked_status = "Parked without Ending" if last_end is None else "Parked"
    else:
        parked_status = ""

    # ─── Filter to recognised WPs ───
    official_lower = {name.lower() for name in OVERLAY_WP_NAMES}
    official_wps = []
    all_wp_info = []
    for wp_key, wpf in wp_list:
        wp_summary = (wpf.get("summary") or "").strip()
        wp_status = ((wpf.get("status") or {}).get("name") or "").strip()
        wp_resolution = ((wpf.get("resolution") or {}).get("name") or "").strip()
        wp_created = wpf.get("created", "")
        wp_resolved = wpf.get("resolutiondate", "")
        is_official = wp_summary.lower() in official_lower

        all_wp_info.append({
            "key": wp_key,
            "name": wp_summary,
            "status": wp_status,
            "resolution": wp_resolution,
            "created": str(to_date(wp_created)) if to_date(wp_created) else None,
            "resolved": str(to_date(wp_resolved)) if to_date(wp_resolved) else None,
            "is_official": is_official,
        })
        if is_official:
            official_wps.append({
                "key": wp_key,
                "summary": wp_summary,
                "created": to_date(wp_created),
                "resolved": to_date(wp_resolved),
                "status": wp_status,
                "resolution": wp_resolution,
            })

    if not official_wps:
        if logger is not None:
            logger.warning("  %s: no official WPs found - skipping", wc_key)
        return None

    # ─── NPI Start = Min(active WP entry date) ───
    active_wps = [
        wp for wp in official_wps
        if not (wp["resolution"] not in (None, "", *DONE_RESOLUTIONS)
                and wp["resolved"] is not None)
    ]
    entry_dates = [wp["created"] for wp in active_wps if wp["created"] is not None]
    if not entry_dates:
        if logger is not None:
            logger.warning("  %s: no active WP entry dates - skipping", wc_key)
        return None
    npi_start = min(entry_dates)

    # ─── Container elapsed ───
    elapsed = elapsed_wd(npi_start, today, parking_pairs, location)
    if elapsed is None:
        if logger is not None:
            logger.warning("  %s: elapsed computed as None - skipping", wc_key)
        return None

    # ─── Color ───
    remaining = target - elapsed
    if elapsed > target:
        color = "Red"
    elif remaining <= YELLOW_THRESHOLD:
        color = "Yellow"
    else:
        color = "Green"

    # ─── WP progress ───
    wps_done = sum(1 for wp in official_wps if wp["resolution"] in DONE_RESOLUTIONS)
    wps_total = len(official_wps)

    # ─── Cache entry ───
    entry = {
        "issueKey": wc_key,
        "summary": wf.get("summary", ""),
        "status": ((wf.get("status") or {}).get("name") or ""),
        "assignee": ((wf.get("assignee") or {}).get("displayName")
                     or (wf.get("assignee") or {}).get("name", "")),
        "location": location,
        "orderType": _get_field_value(wf, CF_ORDER_TYPE, ""),
        "projectId": _get_field_value(wf, CF_PROJECT_ID, ""),
        "npiStart": str(npi_start),
        "elapsed": elapsed,
        "target": target,
        "remaining": remaining,
        "color": color,
        "parked": parked_status,
        "parkingPeriods": [
            {"start": str(ps) if ps else None, "end": str(pe) if pe else None}
            for ps, pe in parking_pairs
        ],
        "wpsDone": wps_done,
        "wpsTotal": wps_total,
        "workPackages": all_wp_info,
    }

    entry["wpKpis"] = compute_wp_kpis(
        official_wps, npi_start, parking_pairs, today, location, wp_config,
        logger=logger, wc_key=wc_key,
    )

    if logger is not None:
        logger.debug(
            "  %s [%s]: %s/%s -> %s (WPs: %s/%s, parked=%s)",
            wc_key, location, elapsed, target, color,
            wps_done, wps_total, parked_status or "No",
        )
    return entry
