"""
Forward-looking earnings calendar for the qualifier earnings track.

yfinance.Ticker.earnings_dates returns past + upcoming events. We cache the
result daily in earnings_calendar_cache.parquet so we don't hit yfinance on
every qualifier run; the cache refreshes when the parquet file is older
than 24 hours.
"""
from __future__ import annotations

import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
CACHE_PATH = ROOT / "data/profile/earnings_calendar_cache.parquet"
CACHE_TTL_HOURS = 24

log = logging.getLogger(__name__)


def _cache_is_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=CACHE_TTL_HOURS)


def _refresh_cache(symbols: list[str]) -> pd.DataFrame:
    """Fetch upcoming earnings dates from yfinance for each symbol.

    Returns a DataFrame: ticker, earnings_date (date object), refreshed_at.
    Includes both past + upcoming events; caller filters by date.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed; earnings calendar empty")
        return pd.DataFrame(columns=["ticker", "earnings_date", "refreshed_at"])

    rows = []
    now = datetime.now()
    for sym in symbols:
        try:
            ed = yf.Ticker(sym).earnings_dates
            if ed is None or ed.empty:
                continue
            for d in ed.index:
                ts = pd.Timestamp(d).tz_localize(None) if pd.Timestamp(d).tz else pd.Timestamp(d)
                rows.append({
                    "ticker": sym,
                    "earnings_date": ts.normalize().date(),
                    "refreshed_at": now.isoformat(timespec="seconds"),
                })
        except Exception as e:
            log.debug("yfinance earnings_dates error for %s: %s", sym, e)
            continue

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(["ticker", "earnings_date"]).sort_values(["ticker", "earnings_date"])
    return df


def load_earnings_calendar(symbols: list[str], force_refresh: bool = False) -> pd.DataFrame:
    """Return earnings calendar DataFrame for the given symbols.

    Cache hit: read parquet, filter to requested symbols, return.
    Cache miss: fetch from yfinance, write parquet, return.
    """
    if not force_refresh and _cache_is_fresh(CACHE_PATH):
        df = pd.read_parquet(CACHE_PATH)
        # If the cache covers all requested symbols, use it; otherwise refresh
        cached_syms = set(df["ticker"].unique())
        if set(symbols).issubset(cached_syms):
            return df[df["ticker"].isin(symbols)].copy()

    # Refresh
    df = _refresh_cache(symbols)
    if not df.empty:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(CACHE_PATH, index=False)
    return df


def upcoming_earnings(symbols: list[str], today: date,
                      window_days: int = 30) -> pd.DataFrame:
    """Return earnings events within window_days after today, per symbol."""
    df = load_earnings_calendar(symbols)
    if df.empty:
        return df
    horizon = today + timedelta(days=window_days)
    mask = (df["earnings_date"] >= today) & (df["earnings_date"] <= horizon)
    return df[mask].copy().reset_index(drop=True)
