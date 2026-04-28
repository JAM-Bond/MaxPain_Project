"""
MaxPain — OpEx calendar + trading-day helpers
~/MaxPain_Project/lib/opex_calendar.py

US monthly OpEx = third Friday. Trading-day arithmetic uses NYSE business
days (Mon-Fri, no holiday calendar — within ±1 day, which is fine for
entry-window targeting since the plan already builds in tolerance).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


def third_friday(year: int, month: int) -> date:
    """Third Friday of (year, month) — the standard US monthly OpEx."""
    d = date(year, month, 1)
    offset = (4 - d.weekday()) % 7  # Friday = weekday 4
    return d + timedelta(days=offset + 14)


def monthly_opex_dates(start_year: int, end_year: int) -> list[date]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            out.append(third_friday(y, m))
    return out


def current_opex(today: date | None = None) -> date:
    """The next monthly OpEx ≥ today. If today is past this month's, returns next month's."""
    today = today or date.today()
    y, m = today.year, today.month
    opex = third_friday(y, m)
    if today > opex:
        m += 1
        if m > 12:
            m = 1
            y += 1
        opex = third_friday(y, m)
    return opex


def next_n_opexes(n: int, today: date | None = None) -> list[date]:
    """The next N monthly OpEx dates including the current one."""
    today = today or date.today()
    opexes = []
    cur = current_opex(today)
    opexes.append(cur)
    while len(opexes) < n:
        # Move to first day of month after cur
        y, m = cur.year, cur.month + 1
        if m > 12:
            m = 1
            y += 1
        cur = third_friday(y, m)
        opexes.append(cur)
    return opexes


def trading_days_between(start: date, end: date) -> int:
    """Count of trading days (Mon-Fri) strictly between start and end.

    If start <= end, returns positive count. If start > end, returns negative.
    Holidays are ignored (NYSE has ~9-10 holidays/yr, error well within
    the ENTRY_WINDOW_TOLERANCE of ±1 trading day).
    """
    if start == end:
        return 0
    sign = 1 if end > start else -1
    a, b = (start, end) if end > start else (end, start)
    count = 0
    d = a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return sign * count


def trading_day_offset(anchor: date, offset: int) -> date:
    """Return the trading day that is `offset` business days from anchor.

    offset > 0 means forward (later); offset < 0 means backward (earlier).
    """
    if offset == 0:
        return anchor
    direction = 1 if offset > 0 else -1
    remaining = abs(offset)
    d = anchor
    while remaining > 0:
        d += timedelta(days=direction)
        if d.weekday() < 5:
            remaining -= 1
    return d


def calendar_days_before(anchor: date, days: int) -> date:
    """Calendar-day arithmetic, no weekday awareness."""
    return anchor - timedelta(days=days)
