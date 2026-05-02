#!/usr/bin/env python3.11
"""
Bull-put moneyness backtest — sealed pre-reg at docs/BULL_PUT_MONEYNESS_PREREG.md.

Tests OTM (30Δ short), ATM (50Δ short), ITM (70Δ short) bull-put credit
verticals across the entire 163-ticker ORATS-historical universe, 45-DTE
entry on monthly OpEx, both held-to-expiry and 50% managed exit rules,
slip=0.50.

Output: data/profile/bull_put_moneyness_results.parquet (cycle-level)
"""
from __future__ import annotations

import logging
import sys
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

# Activate slip=0.50 (validated standard) for all entries
C.activate_slip(0.50)

BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_OUT = ROOT / "data/profile/bull_put_moneyness_results.parquet"

ENTRY_DTE = 45
MONEYNESS = {
    "OTM": 0.30,  # → C.VERTICAL_SHORT_DELTA = 0.30 → put delta -0.30
    "ATM": 0.50,
    "ITM": 0.70,
}
MGD_50_THRESHOLD = 0.5  # close when close_cost ≤ 0.5 × entry_credit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bp_moneyness")


def _parse_exp(s) -> pd.Timestamp | None:
    try:
        return pd.to_datetime(s, errors="coerce")
    except Exception:
        return None


def simulate_one(slice_by_day: dict, available_days: list, entry_date: date,
                 expiration: pd.Timestamp, ticker: str, moneyness: str) -> dict | None:
    """One bull-put cycle at a given moneyness. Returns dict with both exit-rule P&Ls."""
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None

    # Set the moneyness target (mutates module-level C.VERTICAL_SHORT_DELTA).
    # Caller serializes by-moneyness so this is safe sequentially.
    C.VERTICAL_SHORT_DELTA = MONEYNESS[moneyness]

    pos = open_bull_put(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None

    spot_entry = pos.underlying_entry
    entry_credit = pos.entry_credit
    short_strike = pos.legs[0].strike
    long_strike = pos.legs[1].strike
    short_delta_call = pos.legs[0].delta  # call-delta convention
    short_delta_put = short_delta_call - 1.0  # standard trader convention (negative)

    # ── Forward simulation: daily MTM until expiration or 50% managed trigger ──
    mgd_50_exit_date = None
    mgd_50_pnl = None

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]
    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        cost = close_cost(pos, chain_d)
        if cost is None:
            continue
        # 50% managed: close on first day where mark_credit ≤ 0.5 × entry_credit
        if mgd_50_exit_date is None and cost <= MGD_50_THRESHOLD * entry_credit:
            mgd_50_exit_date = d
            mgd_50_pnl = entry_credit - cost
            # Don't break — we still want to compute held-to-expiry result

    # Held-to-expiry: use last-available chain at expiration date
    last_chain = slice_by_day.get(expiration.date())
    if last_chain is not None and not last_chain.empty:
        S_exp = float(last_chain["stkPx"].iloc[0])
        held_pnl = entry_credit + intrinsic_value_at_expiry(pos, S_exp)
    else:
        # Use last-available day's intrinsic as proxy
        S_exp = float(slice_by_day[forward_days[-1]]["stkPx"].iloc[0]) if forward_days else spot_entry
        held_pnl = entry_credit + intrinsic_value_at_expiry(pos, S_exp)

    # If 50% managed never triggered, "managed" P&L = held-to-expiry
    if mgd_50_exit_date is None:
        mgd_50_pnl = held_pnl
        mgd_50_exit_date = expiration.date()

    return {
        "ticker": ticker,
        "moneyness": moneyness,
        "entry_date": pd.Timestamp(entry_date),
        "expiration": expiration,
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "short_delta_put": short_delta_put,
        "wing_width": short_strike - long_strike,
        "entry_credit": float(entry_credit),
        "held_pnl": float(held_pnl),
        "held_win": int(held_pnl > 0),
        "mgd50_pnl": float(mgd_50_pnl),
        "mgd50_win": int(mgd_50_pnl > 0),
        "mgd50_exit_date": pd.Timestamp(mgd_50_exit_date),
        "mgd50_triggered_early": int(mgd_50_exit_date < expiration.date()),
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

    # Build expirDate → Timestamp map and reverse Timestamp → expirDate string
    exp_to_str: dict[pd.Timestamp, str] = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None and ts not in exp_to_str:
            exp_to_str[ts] = s

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]

    sorted_dates = sorted(tdf["date_only"].unique())

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

        # Filter ONCE per cycle to the target expiration's slice
        cycle_df = tdf[tdf["expirDate"] == exp_str]
        if cycle_df.empty:
            continue
        cycle_slice_by_day = {d: g.sort_values("strike").reset_index(drop=True)
                              for d, g in cycle_df.groupby("date_only")}
        available_days = sorted(cycle_slice_by_day.keys())

        for moneyness in MONEYNESS:
            r = simulate_one(cycle_slice_by_day, available_days, entry_date,
                             opex_ts, ticker, moneyness)
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
    for i, t in enumerate(tickers, 1):
        try:
            rows = simulate_ticker(t)
        except Exception as e:
            log.warning("  %s failed: %s", t, e)
            rows = []
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(tickers):
            log.info("  [%d/%d] %s — total cycle rows: %d", i, len(tickers), t, len(all_rows))

    if not all_rows:
        log.error("Zero cycle rows produced")
        sys.exit(1)

    df = pd.DataFrame(all_rows)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d rows to %s", len(df), RESULTS_OUT)
    log.info("Per-moneyness counts: %s", df["moneyness"].value_counts().to_dict())


if __name__ == "__main__":
    main()
