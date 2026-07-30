"""
Shared working-day / working-hours calendar for ExpressOPS tasks.

Centralises the Singapore public-holiday set (previously copy-pasted into
several tasks) and adds business-hours arithmetic used by mo_ref_order_monitor
for stage dwell time.

Working hours model: Mon-Fri, 08:00-17:00 local, minus SG public holidays.
One "working day" = 9 working hours.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

# Singapore public holidays — source: docs/LEGACY_REFERENCE.md (canonical set,
# also mirrored in tasks/mo_trigger_comment/logic.py). Extend as official dates
# are published.
SG_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 1),
    date(2025, 1, 29),
    date(2025, 1, 30),
    date(2025, 4, 1),
    date(2025, 4, 18),
    date(2025, 5, 1),
    date(2025, 6, 17),
    # 2026
    date(2026, 1, 1),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 3, 23),
    date(2026, 4, 3),
    date(2026, 5, 1),
    date(2026, 5, 25),
    date(2026, 6, 8),
    date(2026, 8, 10),
    date(2026, 10, 22),
    date(2026, 12, 25),
}

# Business-day window and derived length.
WORK_DAY_START = time(8, 0)
WORK_DAY_END = time(17, 0)
WORK_HOURS_PER_DAY = 9.0


def is_working_day(d: date, holidays: set[date] | None = None) -> bool:
    """Mon-Fri and not a SG public holiday."""
    if d.weekday() >= 5:
        return False
    return d not in (holidays if holidays is not None else SG_HOLIDAYS)


def business_seconds(start: datetime, end: datetime,
                     holidays: set[date] | None = None,
                     day_start: time = WORK_DAY_START,
                     day_end: time = WORK_DAY_END) -> float:
    """
    Seconds of the interval [start, end] that fall inside working hours
    (day_start..day_end) on working days only. Time outside the window, on
    weekends, or on holidays does not count.
    """
    if end <= start:
        return 0.0
    total = 0.0
    d = start.date()
    last = end.date()
    while d <= last:
        if is_working_day(d, holidays):
            ws = datetime.combine(d, day_start)
            we = datetime.combine(d, day_end)
            lo = max(start, ws)
            hi = min(end, we)
            if hi > lo:
                total += (hi - lo).total_seconds()
        d += timedelta(days=1)
    return total


def business_hours_by_day(start: datetime, end: datetime,
                          holidays: set[date] | None = None,
                          day_start: time = WORK_DAY_START,
                          day_end: time = WORK_DAY_END) -> dict[str, float]:
    """
    Break the working-hours overlap of [start, end] into a per-working-day map
    {'YYYY-MM-DD': seconds}, including only days that actually accrue > 0
    seconds inside the window. Time outside the window / on weekends /
    holidays is excluded (so a stage that only ran after 17:00 yields {}).

    This is the basis for the dwell metric: number of distinct days = len(map),
    total working hours = sum(values).
    """
    out: dict[str, float] = {}
    if end <= start:
        return out
    d = start.date()
    last = end.date()
    while d <= last:
        if is_working_day(d, holidays):
            ws = datetime.combine(d, day_start)
            we = datetime.combine(d, day_end)
            lo = max(start, ws)
            hi = min(end, we)
            if hi > lo:
                out[d.isoformat()] = (hi - lo).total_seconds()
        d += timedelta(days=1)
    return out


def fmt_hours(seconds: float) -> str:
    """Compact hours label: 3600 -> '1h', 12600 -> '3.5h'."""
    h = max(0.0, seconds) / 3600.0
    if abs(h - round(h)) < 0.05:
        return f"{int(round(h))}h"
    return f"{h:.1f}h"
