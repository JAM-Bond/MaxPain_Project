"""Jade Lizard backtest on the skew-rich universe.

Pre-registered in docs/JADE_LIZARD_PREREG.md (sealed 2026-04-25 BEFORE code).

Per (ticker, monthly OpEx cycle, entry-day=45-DTE):
  1. Open jade_lizard (practitioner variant: search for long-call strike that
     minimizes wing while satisfying credit > wing).
  2. Open the BP+BC baseline combo on the same chain + entry day for direct
     comparison.
  3. Walk forward; record P&L for both at:
       - exp (held to expiration)
       - 50% managed (first-trigger 50% max profit OR DTE <= 21)
  4. Emit per-cycle paired rows.

Output:
  data/backtest/jade_lizard_raw.parquet         # one row per (cycle, structure, exit_rule, slip)
  data/profile/jade_lizard_scorecard.parquet    # cohort + per-ticker aggregate
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before
from structures import (
    STRUCTURES, close_cost, intrinsic_value_at_expiry, max_profit,
)

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
SKEW_UNIVERSE = ROOT / "data/profile/skew_universe.parquet"
RAW_OUT = ROOT / "data/backtest/jade_lizard_raw.parquet"

ENTRY_DTE = 45
SLIPS = [0.25, 0.50]
EXIT_DTE_RULE = 21
EXIT_PROFIT_FRAC = 0.50

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("jade")


def _parse_exp(s: str):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def simulate_cycle_one_structure(slice_by_day, available_days, entry_date,
                                 expiration, structure_name):
    """Open structure at entry_date; return (exp_pnl, mgd_pnl) or (nan, nan)
    if entry fails."""
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    open_fn = STRUCTURES[structure_name]
    pos = open_fn(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None

    mp = max_profit(pos)
    underlying_entry = pos.underlying_entry

    # Walk forward to find first-trigger 50% / DTE 21
    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]
    mgd_exit = None  # tuple (date, dte, cost, pnl, S)
    for d in forward_days:
        chain_today = slice_by_day.get(d)
        if chain_today is None or chain_today.empty:
            continue
        days_to_exp = (expiration.date() - d).days
        cost_to_close = close_cost(pos, chain_today)
        if cost_to_close is None:
            continue
        pnl_now = pos.entry_credit - cost_to_close
        if mgd_exit is None and (
            (mp > 0 and pnl_now >= EXIT_PROFIT_FRAC * mp) or
            (days_to_exp <= EXIT_DTE_RULE)
        ):
            mgd_exit = (d, days_to_exp, cost_to_close, pnl_now,
                        float(chain_today["stkPx"].iloc[0]))
            break

    # Compute held-to-expiration pnl
    last_chain = slice_by_day.get(expiration.date())
    if last_chain is not None and not last_chain.empty:
        S_at_exp = float(last_chain["stkPx"].iloc[0])
        exp_pnl = pos.entry_credit + intrinsic_value_at_expiry(pos, S_at_exp)
    else:
        # fallback: latest available date in the slice
        exp_pnl = np.nan
        for d in reversed(forward_days):
            cc = slice_by_day.get(d)
            if cc is not None and not cc.empty:
                exp_pnl = pos.entry_credit + intrinsic_value_at_expiry(
                    pos, float(cc["stkPx"].iloc[0]))
                break

    if mgd_exit is None:
        mgd_pnl = exp_pnl  # never triggered → settled at expiry
    else:
        mgd_pnl = mgd_exit[3]

    return {
        "structure": structure_name,
        "entry_credit": float(pos.entry_credit),
        "max_profit": float(mp),
        "underlying_entry": float(underlying_entry),
        "pnl_exp": float(exp_pnl) if pd.notna(exp_pnl) else np.nan,
        "pnl_mgd": float(mgd_pnl) if pd.notna(mgd_pnl) else np.nan,
        "call_wing": float(pos.notes.get("call_wing", np.nan)),
        "credit_minus_wing": float(pos.notes.get("credit_minus_wing", np.nan)),
        "short_put_k": float(pos.notes.get("short_put_k", np.nan)) if pos.notes.get("short_put_k") else np.nan,
        "short_call_k": float(pos.notes.get("short_call_k", np.nan)) if pos.notes.get("short_call_k") else np.nan,
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

    # Map ORATS expirDate strings to Timestamps
    exp_str_to_date = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None:
            exp_str_to_date[s] = ts

    opex_all = monthly_opex_dates(first_date.year, last_date.year + 1)
    opex_eligible = [d for d in opex_all if first_date <= d <= last_date]

    opex_to_exp = {}
    for opex in opex_eligible:
        ts = pd.Timestamp(opex)
        for s, d in exp_str_to_date.items():
            if abs((d - ts).days) <= 1:
                opex_to_exp[ts] = s
                break

    exp_groups = {s: sub for s, sub in tdf.groupby("expirDate", sort=False)}
    rows = []

    for opex_ts, exp_str in opex_to_exp.items():
        exp_df = exp_groups[exp_str]
        slice_by_day = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}
        available_days = sorted(slice_by_day.keys())

        target_entry = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target_entry, available_days)
        if entry_date is None:
            continue

        for slip in SLIPS:
            C.activate_slip(slip)
            for sname in ("jade_lizard", "bull_put", "bear_call"):
                result = simulate_cycle_one_structure(
                    slice_by_day, available_days, entry_date, opex_ts, sname)
                if result is None:
                    continue
                rows.append({
                    "ticker": ticker, "expiration": opex_ts,
                    "entry_date": pd.Timestamp(entry_date),
                    "slip": slip, **result,
                })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", nargs="+", help="Subset of universe")
    parser.add_argument("--limit", type=int, help="First N tickers (for testing)")
    args = parser.parse_args()

    skew = pd.read_parquet(SKEW_UNIVERSE)
    universe = skew["ticker"].tolist()
    if args.ticker:
        universe = [t for t in universe if t in args.ticker]
    elif args.limit:
        universe = universe[:args.limit]

    log.info("Running on %d skew-rich tickers (entry=45-DTE)", len(universe))

    all_rows = []
    for i, t in enumerate(universe, 1):
        rows = simulate_ticker(t)
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(universe):
            log.info("  [%d/%d] %s: %d total rows so far", i, len(universe), t, len(all_rows))

    if not all_rows:
        log.error("No rows produced")
        return

    df = pd.DataFrame(all_rows)
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RAW_OUT, index=False)
    log.info("Wrote %d raw rows to %s", len(df), RAW_OUT)

    # Print quick summary while raw file is fresh
    print("\n=== Quick fire-rate / mean-pnl summary ===")
    n_jade = (df["structure"] == "jade_lizard").sum()
    n_bp = (df["structure"] == "bull_put").sum()
    n_bc = (df["structure"] == "bear_call").sum()
    print(f"jade_lizard cycles: {n_jade}")
    print(f"bull_put cycles:    {n_bp}")
    print(f"bear_call cycles:   {n_bc}")


if __name__ == "__main__":
    main()
