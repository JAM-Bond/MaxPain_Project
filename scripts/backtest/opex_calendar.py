"""OpEx calendar and trading-day utilities. US monthly OpEx = 3rd Friday."""
from datetime import date, timedelta
from typing import Iterable


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    offset = (4 - d.weekday()) % 7  # Friday = weekday 4
    return d + timedelta(days=offset + 14)


def monthly_opex_dates(start_year: int, end_year: int) -> list[date]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            out.append(third_friday(y, m))
    return out


def nearest_trading_day_on_or_before(target: date, available: Iterable[date]) -> date | None:
    """Given a target date and an iterable of available trading dates,
    return the latest trading date <= target. Used for entry-day selection
    when the calendar target is a weekend or holiday."""
    candidates = [d for d in available if d <= target]
    return max(candidates) if candidates else None
