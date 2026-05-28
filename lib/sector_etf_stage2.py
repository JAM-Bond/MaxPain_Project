"""Sector-ETF Stage-2 Break — signal definition.

Pure-function implementation of the sealed signal from
`docs/SECTOR_ETF_STAGE2_BEARCALL_PREREG.md` §3:

A sector ETF matches STAGE2_BREAK on trading date D if BOTH:
  - ETF close price was above its trailing 200-DMA on date D - 30 trading days
  - ETF close price is below its trailing 200-DMA on date D

Used by the validation script + (if promoted) the live qualifier path
for sector-ETF bear-call entries independent of the H1 gate.

Definition matches H2 Phase 2's R3 (`lib/h2_phase2_definitions.py:r3_stage2_break`)
— preserved separately here for cohort isolation and to avoid coupling the
two use cases.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"

# Frozen sector-ETF cohort per pre-reg §4
SECTOR_ETF_COHORT = [
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
    "XLP", "XLU", "XLV", "XLY", "IYR", "SMH",
]

LOOKBACK_TRADING_DAYS = 30
MA_WINDOW = 200
MA_MIN_PERIODS = 100


def _daily_close(ticker: str) -> pd.Series | None:
    """Return daily-close series indexed by trade_date for one ticker."""
    p = BY_TICKER / f"{ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
    if df.empty:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    closes = (df.dropna(subset=["stkPx"])
                .drop_duplicates("trade_date")
                .sort_values("trade_date")
                .set_index("trade_date")["stkPx"])
    return closes if len(closes) >= MA_MIN_PERIODS else None


def stage2_series(ticker: str) -> pd.DataFrame | None:
    """Daily series with close, ma200, stage2_active for one ticker.

    Returns None if not enough history to compute the signal."""
    closes = _daily_close(ticker)
    if closes is None:
        return None
    daily = pd.DataFrame({"close": closes})
    daily["ma200"] = daily["close"].rolling(MA_WINDOW, min_periods=MA_MIN_PERIODS).mean()
    daily["close_lag"] = daily["close"].shift(LOOKBACK_TRADING_DAYS)
    daily["ma200_lag"] = daily["ma200"].shift(LOOKBACK_TRADING_DAYS)
    daily["above_lag"] = daily["close_lag"] > daily["ma200_lag"]
    daily["below_now"] = daily["close"] < daily["ma200"]
    daily["stage2_active"] = (daily["above_lag"] & daily["below_now"]).fillna(False).astype(int)
    return daily


def is_stage2_break_active(ticker: str, asof: date | str | pd.Timestamp) -> bool:
    """True if the sector ETF's Stage-2 break signal is active on `asof`.

    Returns False if the ticker has no data, no MA history, or the as-of
    date is not in the daily series (uses forward-fill from the most
    recent prior trading day).
    """
    daily = stage2_series(ticker)
    if daily is None or daily.empty:
        return False
    ts = pd.Timestamp(asof)
    # If asof is after the last trading date, use the last available day
    if ts > daily.index.max():
        ts = daily.index.max()
    # Find the most recent trading day on or before asof
    sub = daily.loc[:ts]
    if sub.empty:
        return False
    return bool(sub.iloc[-1]["stage2_active"])


def stage2_active_dates(ticker: str) -> pd.DatetimeIndex:
    """All trading dates on which Stage-2 was active for this ticker."""
    daily = stage2_series(ticker)
    if daily is None:
        return pd.DatetimeIndex([])
    return daily.index[daily["stage2_active"] == 1]


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        # Print current status for the full cohort
        print(f"{'Ticker':6s}  {'last_date':>12s}  {'close':>8s}  {'ma200':>8s}  "
              f"{'stage2_now':>11s}")
        for t in SECTOR_ETF_COHORT:
            s = stage2_series(t)
            if s is None or s.empty:
                print(f"{t:6s}  (no data)")
                continue
            last = s.dropna(subset=["ma200"]).iloc[-1]
            print(f"{t:6s}  {str(s.index[-1].date()):>12s}  {last['close']:>8.2f}  "
                  f"{last['ma200']:>8.2f}  {int(last['stage2_active']):>11d}")
    else:
        for t in sys.argv[1:]:
            s = stage2_series(t)
            if s is None:
                print(f"{t}: no data")
                continue
            fires = stage2_active_dates(t)
            print(f"{t}: {len(fires)} historical stage-2 days, "
                  f"last fire: {fires[-1].date() if len(fires) else 'never'}")
