#!/usr/bin/env python3.11
"""Track A backtest engine — simulate the 6×2×3 matrix across the 150-symbol universe.

For each (ticker, monthly-OpEx cycle, entry_DTE, structure):
    1. Find the entry chain snapshot at entry_date (~target DTE before expiry)
    2. Open the structure; if leg selection fails, skip cycle and record reason
    3. Walk forward day by day:
         - Compute current close_cost and running P&L
         - Check each exit rule: 50% profit, 21-DTE, T-3
         - Each rule produces its own exit event (first trigger date for that rule)
       If still open at expiry, settle on underlying intrinsic value.
    4. Emit one row per (ticker, cycle, structure, entry_dte, exit_rule)

Output: data/backtest/results_v1.parquet

Usage:
    python3.11 run.py                           # full universe
    python3.11 run.py --ticker SPY              # single ticker
    python3.11 run.py --ticker SPY --limit 3    # first 3 cycles
"""
import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before
from structures import (
    STRUCTURES, close_cost, intrinsic_value_at_expiry, max_profit,
)


C.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(C.LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("run")


ENTRIES = [
    ("near_opex", C.ENTRY_DTE_NEAR),
    ("dte_45",    C.ENTRY_DTE_LONG),
]


def simulate_cycle_fast(exp_slice_by_day: dict[object, pd.DataFrame],
                        available_days: list, entry_label: str, target_dte: int,
                        expiration: pd.Timestamp, ticker: str,
                        structure_name: str) -> list[dict]:
    """Simulate one cell using a pre-indexed per-(day) slice dict for the target expiration.

    exp_slice_by_day: mapping date → DataFrame rows for this ticker × this expiration × that day.
    available_days: sorted list of trade_dates (as datetime.date) for this expiration slice.
    """
    target_entry = (expiration - pd.Timedelta(days=target_dte)).date()
    entry_date = nearest_trading_day_on_or_before(target_entry, available_days)
    if entry_date is None:
        return []
    entry_chain = exp_slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return []

    open_fn = STRUCTURES[structure_name]
    pos = open_fn(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return [{
            "ticker": ticker, "expiration": expiration,
            "entry_label": entry_label, "entry_date": pd.Timestamp(entry_date),
            "structure": structure_name, "exit_rule": "entry_failed",
            "exit_date": pd.Timestamp(entry_date), "dte_at_exit": target_dte,
            "entry_credit": np.nan, "exit_cost": np.nan, "pnl": np.nan,
            "pnl_pct_of_max": np.nan, "underlying_entry": np.nan,
            "underlying_exit": np.nan,
        }]

    mp = max_profit(pos)
    exits_needed = {"50_pct": None, "dte_21": None, "t_minus_3": None}

    # Walk forward through days in this expiration's slice after entry_date
    forward_days = [d for d in available_days if d > entry_date and d <= expiration.date()]
    for d in forward_days:
        chain_today = exp_slice_by_day.get(d)
        if chain_today is None or chain_today.empty:
            continue
        days_to_exp = (expiration.date() - d).days
        cost_to_close = close_cost(pos, chain_today)
        if cost_to_close is None:
            continue
        pnl_now = pos.entry_credit - cost_to_close

        if exits_needed["50_pct"] is None and mp > 0 and pnl_now >= C.EXIT_PROFIT_FRAC * mp:
            exits_needed["50_pct"] = (d, days_to_exp, cost_to_close, pnl_now, chain_today["stkPx"].iloc[0])
        if exits_needed["dte_21"] is None and days_to_exp <= C.EXIT_DTE_RULE:
            exits_needed["dte_21"] = (d, days_to_exp, cost_to_close, pnl_now, chain_today["stkPx"].iloc[0])
        if exits_needed["t_minus_3"] is None and days_to_exp <= C.EXIT_T_MINUS:
            exits_needed["t_minus_3"] = (d, days_to_exp, cost_to_close, pnl_now, chain_today["stkPx"].iloc[0])

        if all(v is not None for v in exits_needed.values()):
            break

    # Settle unfilled rules via intrinsic at expiry
    last_day_chain = exp_slice_by_day.get(expiration.date())
    if last_day_chain is not None and not last_day_chain.empty:
        underlying_at_exp = float(last_day_chain["stkPx"].iloc[0])
    else:
        # fall back to the latest available date
        underlying_at_exp = np.nan
        for d in reversed(forward_days):
            chain = exp_slice_by_day.get(d)
            if chain is not None and not chain.empty:
                underlying_at_exp = float(chain["stkPx"].iloc[0])
                break

    if not np.isnan(underlying_at_exp):
        intrinsic_pnl = pos.entry_credit + intrinsic_value_at_expiry(pos, underlying_at_exp)
    else:
        intrinsic_pnl = np.nan
    for rule in exits_needed:
        if exits_needed[rule] is None:
            exits_needed[rule] = (expiration.date(), 0, np.nan, intrinsic_pnl, underlying_at_exp)

    rows = []
    for rule, (d, dte, ec, pnl, ue) in exits_needed.items():
        rows.append({
            "ticker": ticker, "expiration": expiration,
            "entry_label": entry_label, "entry_date": pd.Timestamp(entry_date),
            "structure": structure_name, "exit_rule": rule,
            "exit_date": pd.Timestamp(d), "dte_at_exit": int(dte),
            "entry_credit": pos.entry_credit, "exit_cost": ec, "pnl": pnl,
            "pnl_pct_of_max": (pnl / mp) if (mp and mp > 0 and pd.notna(pnl)) else np.nan,
            "underlying_entry": pos.underlying_entry, "underlying_exit": ue,
        })
    return rows


def simulate_ticker(ticker: str, limit: int | None = None) -> pd.DataFrame:
    by_ticker_path = C.BY_TICKER_ROOT / f"{ticker}.parquet"
    if not by_ticker_path.exists():
        log.warning("No per-ticker parquet for %s, skipping", ticker)
        return pd.DataFrame()
    tdf = pd.read_parquet(by_ticker_path)
    if tdf.empty:
        return pd.DataFrame()

    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"])
    first_date = tdf["trade_date"].min().date()
    last_date = tdf["trade_date"].max().date()

    # Parse all unique expirDates to real dates once
    exp_str_to_date: dict[str, pd.Timestamp] = {}
    for s in tdf["expirDate"].unique():
        try:
            parts = s.split("/")
            exp_str_to_date[s] = pd.Timestamp(year=int(parts[2]), month=int(parts[0]), day=int(parts[1]))
        except Exception:
            continue

    # For each target OpEx third-Friday, find the matching ORATS expirDate string
    opex_all = monthly_opex_dates(first_date.year, last_date.year + 1)
    opex_eligible = [d for d in opex_all if first_date <= d <= last_date]
    if limit:
        opex_eligible = opex_eligible[:limit]

    opex_to_exp_str: dict[pd.Timestamp, str] = {}
    for opex in opex_eligible:
        opex_ts = pd.Timestamp(opex)
        for exp_str, exp_ts in exp_str_to_date.items():
            if abs((exp_ts - opex_ts).days) <= 1:
                opex_to_exp_str[opex_ts] = exp_str
                break

    # Pre-group the full ticker_df by expirDate once — avoids re-filtering per cell
    tdf["date_only"] = tdf["trade_date"].dt.date
    exp_groups: dict[str, pd.DataFrame] = {e: sub for e, sub in tdf.groupby("expirDate", sort=False)}

    rows: list[dict] = []
    for opex_ts, exp_str in opex_to_exp_str.items():
        exp_df = exp_groups[exp_str]
        # Build per-day dict for this expiration — fast lookup during forward walk
        slice_by_day: dict[object, pd.DataFrame] = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}
        available_days = sorted(slice_by_day.keys())

        for entry_label, entry_dte in ENTRIES:
            for structure_name in STRUCTURES:
                cycle_rows = simulate_cycle_fast(
                    slice_by_day, available_days, entry_label, entry_dte,
                    opex_ts, ticker, structure_name)
                rows.extend(cycle_rows)

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, help="Simulate one ticker only")
    parser.add_argument("--limit", type=int, help="Limit cycles per ticker (for testing)")
    parser.add_argument("--version", choices=["v1", "v2"], default="v1",
                        help="v1: fixed wings, bid-ask pricing. v2: scaled wings, mid pricing.")
    parser.add_argument("--slippage", type=float, default=None,
                        help="Slippage fraction in [0, 1]. 0=mid (=v2), 1=full bid-ask (=v1). "
                             "When set, uses v2 scaled wings with this slip.")
    args = parser.parse_args()

    C.BACKTEST_ROOT.mkdir(parents=True, exist_ok=True)

    if args.slippage is not None:
        if not (0.0 <= args.slippage <= 1.0):
            parser.error("--slippage must be in [0, 1]")
        C.activate_slip(args.slippage)
        log.info("slip config: slip_frac=%.3f, v2 scaled wings", C.PRICING_SLIP_FRAC)
    elif args.version == "v2":
        C.activate_v2()
        log.info("v2 config: pricing_mode=%s, IC/BFLY wing pct=%.4f, vertical wing pct=%.4f",
                 C.PRICING_MODE, C.IC_WING_PCT_SPOT, C.VERTICAL_WING_PCT_SPOT)
    else:
        log.info("v1 config: pricing_mode=%s (fixed wings)", C.PRICING_MODE)

    if args.ticker:
        tickers = [args.ticker]
    else:
        universe = pd.read_parquet(C.UNIVERSE_PATH)
        tickers = universe["ticker"].tolist()
    log.info("Simulating %d tickers", len(tickers))

    all_rows = []
    for i, t in enumerate(tickers, 1):
        df = simulate_ticker(t, limit=args.limit)
        if not df.empty:
            all_rows.append(df)
            log.info("  [%d/%d] %s: %d result rows", i, len(tickers), t, len(df))
        else:
            log.info("  [%d/%d] %s: no rows", i, len(tickers), t)

    if not all_rows:
        log.warning("No results produced")
        return
    results = pd.concat(all_rows, ignore_index=True)
    if args.slippage is not None:
        slip_tag = f"slip{int(round(args.slippage * 100)):03d}"
        full_path = C.BACKTEST_ROOT / f"results_{slip_tag}.parquet"
    else:
        full_path = C.RESULTS_V2_PATH if args.version == "v2" else C.RESULTS_PATH
    out_path = full_path
    if args.ticker or args.limit:
        tag = f"slip{int(round(args.slippage * 100)):03d}" if args.slippage is not None else args.version
        out_path = C.BACKTEST_ROOT / f"results_{tag}_{args.ticker or 'limit'}.parquet"
    results.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)
    log.info("Wrote %d total rows to %s", len(results), out_path)


if __name__ == "__main__":
    main()
