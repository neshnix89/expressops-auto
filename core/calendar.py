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


def format_working(seconds: float) -> str:
    """
    Working-hours duration as days+hours, where 1 day = 9 working hours.
    e.g. 4h -> '4h', 10 working hours -> '1d 1h', 24 -> '2d 6h'.
    """
    wh = max(0.0, seconds) / 3600.0
    wd = int(wh // WORK_HOURS_PER_DAY)
    rem = wh - wd * WORK_HOURS_PER_DAY

    def _h(x: float) -> str:
        return f"{x:.1f}h".replace(".0h", "h")

    if wd and rem >= 0.05:
        return f"{wd}d {_h(rem)}"
    if wd:
        return f"{wd}d"
    return _h(rem)
