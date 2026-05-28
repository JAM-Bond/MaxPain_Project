"""Bull-put with 200-DMA cross-exit rule — per-cycle simulation.

Implements the sealed exit rule from
`docs/BULL_PUT_MA200_CROSS_EXIT_PREREG.md` §3:

  Close on first day D during the hold where:
    - spot(D) < 0.98 × own 200-DMA(D)
    - AND ≥ 5 trading days have elapsed since entry
    - AND spot was ≥ own 200-DMA at entry (entry-above filter)
    - AND mgd50 has not already fired (existing rule wins on time priority)

Output: data/profile/bull_put_ma200_cross_results.parquet
  One row per (ticker, entry_date, moneyness) — same key as the existing
  bull_put_moneyness_results.parquet — with these new fields:
    - entry_above_ma200    : 0/1 was spot ≥ 200-DMA at entry
    - cross_exit_triggered : 0/1 did the rule actually fire
    - cross_exit_date      : date the rule fired (or NaT)
    - cross_exit_pnl       : per-share P/L at the cross-exit
    - combined_pnl         : min-time-of-fire between mgd50 and cross-exit
                              (the "current rule stack + cross-exit" P/L)
    - mgd50_pnl, held_pnl : preserved from upstream simulation for cross-check
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BACKTEST_DIR = ROOT / "scripts/backtest"
sys.path.insert(0, str(BACKTEST_DIR))

import config as C  # noqa: E402
from structures import open_bull_put, close_cost, intrinsic_value_at_expiry  # noqa: E402
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before  # noqa: E402

C.activate_slip(0.50)

BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_OUT = ROOT / "data/profile/bull_put_ma200_cross_results.parquet"

ENTRY_DTE = 45
MONEYNESS = {"OTM": 0.30, "ATM": 0.50, "ITM": 0.70}
MGD_50_THRESHOLD = 0.5

# Sealed cross-exit parameters (pre-reg §3)
CROSS_BUFFER = 0.98             # spot must be < 0.98 × MA200 to trigger
CROSS_MIN_HOLD_TDAYS = 5        # ≥ 5 trading days since entry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bp_cross")


def _parse_exp(s) -> pd.Timestamp | None:
    try:
        return pd.to_datetime(s, errors="coerce")
    except Exception:
        return None


def _ticker_underlying_ma200(ticker: str) -> pd.DataFrame | None:
    """Daily series indexed by date with close + 200-DMA. None if insufficient history."""
    p = BY_TICKER / f"{ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    daily = (df.dropna(subset=["stkPx"])
                .drop_duplicates("trade_date")
                .sort_values("trade_date")
                .set_index("trade_date"))
    if len(daily) < 200:
        return None
    daily["ma200"] = daily["stkPx"].rolling(200, min_periods=100).mean()
    return daily[["stkPx", "ma200"]]


def simulate_one(slice_by_day: dict, available_days: list, entry_date: date,
                 expiration: pd.Timestamp, ticker: str, moneyness: str,
                 ma_lookup: pd.DataFrame | None) -> dict | None:
    """One bull-put cycle with cross-exit + mgd50 + held all tracked."""
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None

    C.VERTICAL_SHORT_DELTA = MONEYNESS[moneyness]
    pos = open_bull_put(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None

    spot_entry = pos.underlying_entry
    entry_credit = pos.entry_credit

    # Entry MA200 check
    entry_ts = pd.Timestamp(entry_date)
    entry_above_ma200 = 0
    if ma_lookup is not None and entry_ts in ma_lookup.index:
        ma_row = ma_lookup.loc[entry_ts]
        if not pd.isna(ma_row["ma200"]) and not pd.isna(ma_row["stkPx"]):
            entry_above_ma200 = int(ma_row["stkPx"] >= ma_row["ma200"])

    # Forward simulation: track mgd50 + cross-exit simultaneously
    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]

    mgd_50_exit_date = None
    mgd_50_pnl = None
    cross_exit_date = None
    cross_exit_pnl = None
    tdays_since_entry = 0
    earliest_exit_date = None
    earliest_exit_pnl = None
    earliest_exit_type = None

    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        cost = close_cost(pos, chain_d)
        if cost is None:
            continue
        tdays_since_entry += 1

        # Check mgd50 trigger (existing rule)
        if mgd_50_exit_date is None and cost <= MGD_50_THRESHOLD * entry_credit:
            mgd_50_exit_date = d
            mgd_50_pnl = entry_credit - cost
            if earliest_exit_date is None:
                earliest_exit_date = d
                earliest_exit_pnl = mgd_50_pnl
                earliest_exit_type = "mgd50"

        # Check cross-exit trigger (new rule from sealed pre-reg)
        if (cross_exit_date is None
                and entry_above_ma200 == 1
                and tdays_since_entry >= CROSS_MIN_HOLD_TDAYS
                and ma_lookup is not None):
            d_ts = pd.Timestamp(d)
            if d_ts in ma_lookup.index:
                ma_row = ma_lookup.loc[d_ts]
                spot_d = ma_row["stkPx"]
                ma_d = ma_row["ma200"]
                if (not pd.isna(spot_d) and not pd.isna(ma_d)
                        and spot_d < CROSS_BUFFER * ma_d):
                    cross_exit_date = d
                    cross_exit_pnl = entry_credit - cost
                    if earliest_exit_date is None:
                        earliest_exit_date = d
                        earliest_exit_pnl = cross_exit_pnl
                        earliest_exit_type = "cross"

    # Held-to-expiry
    last_chain = slice_by_day.get(expiration.date())
    if last_chain is not None and not last_chain.empty:
        S_exp = float(last_chain["stkPx"].iloc[0])
        held_pnl = entry_credit + intrinsic_value_at_expiry(pos, S_exp)
    else:
        S_exp = float(slice_by_day[forward_days[-1]]["stkPx"].iloc[0]) if forward_days else spot_entry
        held_pnl = entry_credit + intrinsic_value_at_expiry(pos, S_exp)

    # If mgd50 never triggered, mgd50_pnl falls back to held_pnl (existing convention)
    if mgd_50_exit_date is None:
        mgd_50_pnl = held_pnl
        mgd_50_exit_date = expiration.date()

    # If cross-exit never triggered, cross_exit_pnl falls back to held
    if cross_exit_date is None:
        cross_exit_pnl = held_pnl

    # Combined-rule P/L: whichever fires first between mgd50 and cross-exit,
    # else held
    if earliest_exit_date is not None:
        combined_pnl = earliest_exit_pnl
        combined_exit_type = earliest_exit_type
    else:
        combined_pnl = held_pnl
        combined_exit_type = "held"

    return {
        "ticker": ticker,
        "moneyness": moneyness,
        "entry_date": pd.Timestamp(entry_date),
        "expiration": expiration,
        "spot_entry": spot_entry,
        "entry_credit": float(entry_credit),
        "entry_above_ma200": entry_above_ma200,
        "held_pnl": float(held_pnl),
        "mgd50_pnl": float(mgd_50_pnl),
        "mgd50_exit_date": pd.Timestamp(mgd_50_exit_date),
        "cross_exit_triggered": int(cross_exit_date is not None),
        "cross_exit_date": pd.Timestamp(cross_exit_date) if cross_exit_date else pd.NaT,
        "cross_exit_pnl": float(cross_exit_pnl),
        "combined_pnl": float(combined_pnl),
        "combined_exit_type": combined_exit_type,
    }


def simulate_ticker(ticker: str) -> list[dict]:
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return []
    tdf = pd.read_parquet(path)
    if tdf.empty:
        return []
    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"])
    tdf["date_only"] = tdf["trade_date"].dt.date
    first_date = tdf["trade_date"].min().date()
    last_date = tdf["trade_date"].max().date()

    exp_to_str: dict[pd.Timestamp, str] = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None and ts not in exp_to_str:
            exp_to_str[ts] = s

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]
    sorted_dates = sorted(tdf["date_only"].unique())

    # Underlying daily + MA200 (computed once per ticker)
    ma_lookup = _ticker_underlying_ma200(ticker)

    rows = []
    for opex in opex_eligible:
        opex_ts = pd.Timestamp(opex)
        if opex_ts not in exp_to_str:
            continue
        exp_str = exp_to_str[opex_ts]
        target_entry = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target_entry, sorted_dates)
        if entry_date is None:
            continue
        cycle_df = tdf[tdf["expirDate"] == exp_str]
        if cycle_df.empty:
            continue
        cycle_slice_by_day = {d: g.sort_values("strike").reset_index(drop=True)
                                for d, g in cycle_df.groupby("date_only")}
        available_days = sorted(cycle_slice_by_day.keys())
        for moneyness in MONEYNESS:
            r = simulate_one(cycle_slice_by_day, available_days, entry_date,
                              opex_ts, ticker, moneyness, ma_lookup)
            if r is not None:
                rows.append(r)
    return rows


def main():
    if not BY_TICKER.exists():
        log.error("by_ticker dir missing: %s", BY_TICKER)
        sys.exit(1)
    tickers = sorted(p.stem for p in BY_TICKER.glob("*.parquet"))
    log.info("Universe: %d tickers", len(tickers))

    all_rows = []
    t0 = time.time()
    for i, t in enumerate(tickers, 1):
        try:
            rows = simulate_ticker(t)
        except Exception as e:
            log.warning("  %s failed: %s", t, e)
            rows = []
        all_rows.extend(rows)
        if i % 25 == 0 or i == len(tickers):
            el = time.time() - t0
            log.info("  [%d/%d] %s — %d rows so far (%.0fs)", i, len(tickers), t,
                     len(all_rows), el)

    if not all_rows:
        log.error("Zero cycle rows produced")
        sys.exit(1)

    df = pd.DataFrame(all_rows)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d rows to %s", len(df), RESULTS_OUT)
    log.info("Cross-exit trigger rate: %.1f%% (%d/%d cycles)",
              df["cross_exit_triggered"].mean() * 100,
              int(df["cross_exit_triggered"].sum()),
              len(df))


if __name__ == "__main__":
    main()
