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


def _refresh_cache(symbols: list[str]) -> tuple[pd.DataFrame, set[str]]:
    """Fetch upcoming earnings dates from yfinance for each symbol.

    Returns (DataFrame, failed) where the DataFrame has columns
    ticker, earnings_date (date object or None), refreshed_at, and `failed`
    is the set of symbols whose fetch RAISED (vs. legitimately returning no
    events). A fetch-OK-but-no-events symbol (ETFs) gets a SENTINEL row with
    earnings_date=None — this distinguishes "verified: no earnings" from
    "fetch failed: earnings unknown", which the qualifier's earnings gate
    needs (go-live audit C5: a silent yfinance outage must not silently
    disable the binary-earnings SKIP). Sentinels also let the cache
    subset-check cover ETFs, so a fresh cache is no longer re-fetched every
    run just because ETF symbols never produced rows.
    Includes both past + upcoming events; caller filters by date.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed; earnings calendar empty")
        return (pd.DataFrame(columns=["ticker", "earnings_date", "refreshed_at"]),
                set(symbols))

    rows = []
    failed: set[str] = set()
    now = datetime.now()
    for sym in symbols:
        try:
            ed = yf.Ticker(sym).earnings_dates
            if ed is None or ed.empty:
                # Verified no-earnings (ETF / no coverage) — sentinel row
                rows.append({
                    "ticker": sym,
                    "earnings_date": None,
                    "refreshed_at": now.isoformat(timespec="seconds"),
                })
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
            failed.add(sym)
            continue

    df = pd.DataFrame(rows)
    if df.empty:
        return df, failed
    df = df.drop_duplicates(["ticker", "earnings_date"]).sort_values(["ticker", "earnings_date"])
    return df, failed


def _load_calendar(symbols: list[str],
                   force_refresh: bool = False) -> tuple[pd.DataFrame, set[str]]:
    """Shared loader: returns (event rows only — sentinels dropped, failed set).

    failed = symbols whose data could NOT be obtained this load (fetch raised
    and no fresh cache entry covers them). Empty set on a clean cache hit.
    """
    if not force_refresh and _cache_is_fresh(CACHE_PATH):
        df = pd.read_parquet(CACHE_PATH)
        # Sentinel rows (earnings_date=None) count as coverage: the symbol was
        # successfully checked and verified to have no events.
        cached_syms = set(df["ticker"].unique())
        if set(symbols).issubset(cached_syms):
            sub = df[df["ticker"].isin(symbols)].copy()
            return sub[sub["earnings_date"].notna()].reset_index(drop=True), set()

    df, failed = _refresh_cache(symbols)
    if not df.empty:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(CACHE_PATH, index=False)
    events = df[df["earnings_date"].notna()].reset_index(drop=True) if not df.empty else df
    return events, failed


def load_earnings_calendar(symbols: list[str], force_refresh: bool = False) -> pd.DataFrame:
    """Return earnings calendar DataFrame for the given symbols (events only).

    Cache hit: read parquet, filter to requested symbols, return.
    Cache miss: fetch from yfinance, write parquet, return.
    """
    return _load_calendar(symbols, force_refresh)[0]


def upcoming_earnings_with_status(symbols: list[str], today: date,
                                  window_days: int = 30) -> tuple[pd.DataFrame, set[str]]:
    """Like upcoming_earnings, but also reports which symbols FAILED to fetch.

    A symbol in the failed set has UNKNOWN earnings status — callers applying
    an earnings gate must not treat it as 'no earnings'.
    """
    df, failed = _load_calendar(symbols)
    if df.empty:
        return df, failed
    horizon = today + timedelta(days=window_days)
    mask = (df["earnings_date"] >= today) & (df["earnings_date"] <= horizon)
    return df[mask].copy().reset_index(drop=True), failed


def upcoming_earnings(symbols: list[str], today: date,
                      window_days: int = 30) -> pd.DataFrame:
    """Return earnings events within window_days after today, per symbol."""
    return upcoming_earnings_with_status(symbols, today, window_days)[0]
