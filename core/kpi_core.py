"""
kpi_core.py — Shared KPI primitives for the ExpressOPS NPI reporting stack.

Migrated from the legacy standalone script
``C:\\Users\\tmoghanan\\Documents\\AI\\LiveKPI_Overlay\\kpi_core.py``.

Single source of truth for:
  - Public holidays / factory-shutdown dates (per location)
  - Per-location NPI targets (TARGETS_V5)
  - Official Work Package names
  - Working-day arithmetic (fNetWorkdays — the "KPI method" of day-1)
  - Parking-adjusted workday calculation (9-case matrix)
  - Tech-prep Green/Red determination
  - JIRA custom-field IDs and small safe-accessors

RULE: Any change to KPI semantics must happen here, not in a caller. The daily
live overlay (tasks/kpi_overlay) and any future weekly pipeline both read from
this module so their numbers never drift.

Dependency-light on purpose: the legacy version imported numpy/pandas (for the
Excel weekly pipeline). The live overlay only ever passes JIRA date strings, and
numpy/pandas are not installed on the VPS, so the pandas/numpy paths were dropped
here. `to_date` still accepts datetime/date/str/Excel-serial inputs.
"""

from __future__ import annotations

import re
from datetime import datetime, date, timedelta


# ═══════════════════════════════════════════════════════════════
# WORK PACKAGE NAMES
# ═══════════════════════════════════════════════════════════════

# Official Work Package names. Matching against these must be
# case-insensitive (JIRA data has inconsistent casing like "SMT build"
# vs "SMT Build").
OFFICIAL_WP_NAMES = [
    "Material", "PCB", "Routing - TechnPrep", "PE - TechnPrep",
    "TE - TechnPrep", "SMT Build", "QM P+L", "Logistics", "Documentation",
]


# ═══════════════════════════════════════════════════════════════
# TARGETS (v5 — flat by location)
# ═══════════════════════════════════════════════════════════════
#
# NOTE ON TWO CORRECTIONS vs the legacy kpi_core.py (both intentional):
#   1. Singapore T_Documentation was 1 in the legacy TARGETS_V5 — that was the
#      long-suspected bug flagged in docs/LEGACY_REFERENCE.md. Singapore
#      Documentation is 4 working days (the legacy live overlay hardcoded 4 in
#      its own WP_CONFIG, bypassing the buggy value). Restored to 4 here.
#   2. Trutnov T_Logistic was 4 in the legacy TARGETS_V5; the confirmed Trutnov
#      target is 1 working day. Set to 1 here.
#
# Trutnov container-level target (T_NPI) is 21 vs Singapore's 24.
TARGETS_V5 = {
    "Singapore": {"T_NPI": 24, "T_Material": 15, "T_PCB": 15, "T_Routing": 5,
                  "T_PE": 5, "T_TE": 5, "T_SMT Build": 5, "T_Logistic": 4,
                  "T_Documentation": 4},
    "Trutnov":   {"T_NPI": 21, "T_Material": 15, "T_PCB": 15, "T_Routing": 5,
                  "T_PE": 5, "T_TE": 5, "T_SMT Build": 5, "T_Logistic": 1,
                  "T_Documentation": 1},
}

DEFAULT_LOCATION = "Singapore"


def targets_for(location: str | None) -> dict:
    """Return the TARGETS_V5 entry for a location, falling back to Singapore.

    NPI Location comes from JIRA customfield_13906 and is normally exactly
    "Singapore" or "Trutnov"; anything else (blank, unexpected) falls back to
    the Singapore target set so a container is never dropped for a bad label.
    """
    if location and location.strip() in TARGETS_V5:
        return TARGETS_V5[location.strip()]
    return TARGETS_V5[DEFAULT_LOCATION]


def normalize_location(location: str | None) -> str:
    """Map a raw NPI Location value to a known target/holiday key."""
    if location and location.strip() in TARGETS_V5:
        return location.strip()
    return DEFAULT_LOCATION


# ═══════════════════════════════════════════════════════════════
# HOLIDAYS
# ═══════════════════════════════════════════════════════════════
#
# Public holidays AND P+F company in-lieu days. fNetWorkdays() treats these as
# non-working days. Trutnov (Czech) and Singapore have distinct calendars — the
# overlay must use the container's own location so elapsed-day math is correct.
# ═══════════════════════════════════════════════════════════════

HOLIDAYS = {
    "Trutnov": {
        date(2026, 1, 1),    # New Year
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 6),    # Easter Monday
        date(2026, 5, 1),    # Labour Day
        date(2026, 5, 8),
        date(2026, 7, 5),
        date(2026, 7, 6),
        date(2026, 9, 28),
        date(2026, 11, 17),
        date(2026, 12, 24),  # Christmas Eve
        date(2026, 12, 25),  # Christmas Day
        date(2026, 12, 26),  # 2nd Christmas Day
    },
    "Singapore": {
        date(2026, 1, 1),    # New Year
        date(2026, 2, 16),   # Block Leave CNY
        date(2026, 2, 17),   # Chinese New Year
        date(2026, 2, 18),   # Chinese New Year
        date(2026, 2, 19),   # Block Leave CNY
        date(2026, 2, 20),   # Block Leave CNY
        date(2026, 3, 23),   # Hari Raya Puasa - PH Replacement (gazetted 21 Mar = Sat)
        date(2026, 4, 3),    # Good Friday
        date(2026, 5, 1),    # Labour Day
        date(2026, 5, 27),   # Hari Raya Haji
        date(2026, 6, 1),    # Vesak Day - PH Replacement (gazetted 31 May = Sun)
        date(2026, 8, 10),   # National Day - PH Replacement (gazetted 9 Aug = Sun)
        date(2026, 11, 9),   # Deepavali - PH Replacement (gazetted 8 Nov = Sun)
        date(2026, 12, 25),  # Christmas Day
    },
}


# ═══════════════════════════════════════════════════════════════
# JIRA CUSTOM FIELD IDs
# ═══════════════════════════════════════════════════════════════

CF_ORDER_TYPE      = "customfield_13905"   # e.g. "QS - Qualification sample"
CF_NPI_LOCATION    = "customfield_13906"   # "Singapore" / "Trutnov"
CF_PRODUCT_TYPE    = "customfield_13904"   # e.g. "SMT PCBA"
CF_REQUEST_TYPE    = "customfield_13903"   # e.g. "NPI Request"
CF_PARKED_LOG      = "customfield_15800"   # "Start:...;End:...;"
CF_PROJECT_ID      = "customfield_13907"   # e.g. "PTSG-AAM4"
CF_PROJECT_STATUS  = "customfield_13700"
CF_NPI_WC_STATUS   = "customfield_15400"
CF_AGG_PROGRESS    = "customfield_11906"   # Task progress


# ═══════════════════════════════════════════════════════════════
# DATE PARSING
# ═══════════════════════════════════════════════════════════════

def to_date(val):
    """Convert value to a ``date`` (strip time component). None if null/blank.

    Accepts datetime, date, Excel serial numbers, and the JIRA/BIRT string
    formats. Milliseconds and timezone offsets are stripped before parsing.
    """
    if val is None:
        return None
    # NaN (float) — without importing numpy: NaN != NaN.
    if isinstance(val, float) and val != val:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        val = val.strip()
        if val == "" or val.lower() == "nan" or val == "#NV":
            return None
        # Strip milliseconds and timezone offset
        val = re.sub(r'\.\d+([+-]\d{2}:?\d{2})?$', '', val)
        val = re.sub(r'[+-]\d{2}:?\d{2}$', '', val)
        # Excel serial date
        try:
            num = float(val)
            if num > 40000:
                return (datetime(1899, 12, 30) + timedelta(days=num)).date()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
    return None


# ═══════════════════════════════════════════════════════════════
# WORKING-DAY ARITHMETIC
# ═══════════════════════════════════════════════════════════════

def fNetWorkdays(start, end, location="Singapore"):
    """Replicate VBA fNetWorkdays: count weekdays start→end inclusive, minus
    holidays, then subtract 1 ("KPI method"). None if either date is None,
    0 if start > end.
    """
    start_d = to_date(start)
    end_d = to_date(end)

    if start_d is None or end_d is None:
        return None
    if start_d > end_d:
        return 0

    holidays = HOLIDAYS.get(location, set())
    days = 0
    current = start_d
    while current <= end_d:
        if current.weekday() < 5 and current not in holidays:  # Mon-Fri, not holiday
            days += 1
        current += timedelta(days=1)

    return days - 1  # KPI method


def netWorkdaysRaw(start, end, location="Singapore"):
    """Count weekdays start→end INCLUSIVE excluding holidays. No '-1' adjustment.

    Used for per-parking-pair overlap math where we need the actual working
    days inside the overlap window, not the "start day = 0" KPI convention.
    """
    s = to_date(start)
    e = to_date(end)
    if s is None or e is None:
        return 0
    if s > e:
        return 0

    holidays = HOLIDAYS.get(location, set())
    days = 0
    current = s
    while current <= e:
        if current.weekday() < 5 and current not in holidays:
            days += 1
        current += timedelta(days=1)
    return days


def parking_adjusted_workdays(start_date, end_date, parked_start, parked_end,
                              parked_status, location):
    """Replicate the 9-case parking algorithm from Access (single park pair).

    Retained for parity with the weekly pipeline. The live overlay uses the
    multi-pair implementation in tasks/kpi_overlay/logic.py.
    """
    s = to_date(start_date)
    e = to_date(end_date)
    ps = to_date(parked_start)
    pe = to_date(parked_end)

    if s is None or e is None:
        return None

    if parked_status not in ("Parked", "Parked without Ending"):
        return fNetWorkdays(s, e, location)
    if ps is None:
        return fNetWorkdays(s, e, location)

    if s < ps and e < ps:
        return fNetWorkdays(s, e, location)
    if s < ps and e >= ps and (pe is None or e < pe):
        return (fNetWorkdays(s, ps, location) or 0) + 0
    if s < ps and pe is not None and e >= pe:
        v1 = fNetWorkdays(s, ps, location) or 0
        v2 = fNetWorkdays(pe, e, location) or 0
        return v1 + v2
    if ps is not None and s >= ps and (pe is None or s < pe) and e < ps:
        return None
    if ps is not None and s >= ps and (pe is None or s < pe) and e >= ps and (pe is None or e < pe):
        return 0
    if ps is not None and s >= ps and (pe is not None and s < pe) and e >= pe:
        return 0 + (fNetWorkdays(pe, e, location) or 0)
    if pe is not None and s >= pe and e < ps:
        return None
    if pe is not None and s >= pe and e >= ps and e < pe:
        return None
    if pe is not None and s >= pe and e >= pe:
        return fNetWorkdays(s, e, location)
    return None


# ═══════════════════════════════════════════════════════════════
# HIT/MISS DETERMINATION (tech-prep secondary rule)
# ═══════════════════════════════════════════════════════════════

def kpi_hit(duration, wp_resolution_date, material_fullset):
    """Green/Red for tech-prep KPIs (Routing, PE, TE).
    Green if duration <= 5 days OR WP resolved before Material_Fullset.
    """
    if duration is None or (isinstance(duration, float) and duration != duration):
        return None
    try:
        dur = float(duration)
    except (TypeError, ValueError):
        return None

    if dur <= 5:
        return "Green"

    res_d = to_date(wp_resolution_date)
    mf_d = to_date(material_fullset)

    if res_d is not None and mf_d is not None and res_d <= mf_d:
        return "Green"
    elif res_d is not None and mf_d is not None and res_d > mf_d:
        return "Red"
    return None


# ═══════════════════════════════════════════════════════════════
# JIRA FIELD HELPERS
# ═══════════════════════════════════════════════════════════════

def _parse_parked_log(parked_str):
    """Parse Issue_parked_log into (parked_start, parked_end) ISO strings
    (BIRT format) or (None, None). If only a Start is present, returns
    (start_str, None) — the "Parked without Ending" case.
    """
    if not parked_str or not isinstance(parked_str, str):
        return None, None

    start = None
    end = None
    start_match = re.search(r'Start:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', parked_str)
    end_match = re.search(r'End:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', parked_str)
    if start_match:
        start = start_match.group(1).replace(" ", "T") + ".000"
    if end_match:
        end = end_match.group(1).replace(" ", "T") + ".000"
    return start, end


def _get_field_value(fields, field_id, default=None):
    """Safely get a JIRA field value, handling dict/list/string types."""
    val = fields.get(field_id)
    if val is None:
        return default
    if isinstance(val, dict):
        return val.get("value", val.get("name", str(val)))
    if isinstance(val, list):
        return ", ".join(
            v.get("value", v.get("name", str(v))) if isinstance(v, dict) else str(v)
            for v in val
        )
    return str(val)
