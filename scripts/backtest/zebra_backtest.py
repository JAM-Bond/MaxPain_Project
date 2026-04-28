"""ZEBRA backtest — stock-replacement audit on the v1.5 deployable cohort.

Pre-registered in docs/ZEBRA_PREREG.md (sealed 2026-04-25 BEFORE code).

Per (ticker, monthly OpEx cycle):
  1. Find target expiration ~75 DTE before opex_date.
  2. Open ZEBRA (practitioner variant satisfying extrinsic rule).
  3. Walk forward day-by-day to expiration; record:
     - daily MTM
     - underlying close
     - daily MTM change (theta + delta drag)
     - "flat day" flag (|underlying daily return| < 0.005)
  4. At expiration, settle on intrinsic.
  5. Compare with long stock = (S_exp - S_entry) × 100 over same period.
  6. Compute capture ratio + capital efficiency.

Output:
  data/profile/zebra_results.parquet         # one row per cycle
  data/profile/zebra_daily_mtm.parquet       # daily MTM per cycle (long file)
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
from structures import open_zebra, close_cost, intrinsic_value_at_expiry, max_loss

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
COHORT_PATH = ROOT / "data/profile/research_cohort_v15.parquet"
RESULTS_OUT = ROOT / "data/profile/zebra_results.parquet"
DAILY_OUT = ROOT / "data/profile/zebra_daily_mtm.parquet"

ENTRY_DTE = 75
SLIPS = [0.25, 0.50]


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def simulate_cycle(slice_by_day, available_days, entry_date, expiration, ticker, slip):
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None, []
    pos = open_zebra(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None, []

    n = pos.notes
    spot_entry = pos.underlying_entry
    debit = n["debit"]

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]

    daily_rows = []
    prev_mtm = -debit  # entry MTM = -debit (book value of position equals -debit)
    prev_spot = spot_entry

    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        cost = close_cost(pos, chain_d)
        if cost is None:
            continue
        # P&L if you closed at this snapshot
        pnl_if_closed = pos.entry_credit - cost  # entry_credit is negative for ZEBRA
        # Position value (mark-to-market) = entry_credit - close_cost = pnl_if_closed
        mtm = pnl_if_closed
        spot_today = float(chain_d["stkPx"].iloc[0])
        ret = (spot_today / prev_spot - 1) if prev_spot > 0 else 0.0
        flat_day = abs(ret) < 0.005
        daily_rows.append({
            "ticker": ticker, "expiration": expiration,
            "entry_date": pd.Timestamp(entry_date),
            "trade_date": pd.Timestamp(d),
            "slip": slip,
            "spot": spot_today,
            "spot_return": ret,
            "mtm": mtm,
            "mtm_change": mtm - prev_mtm,
            "flat_day": flat_day,
            "dte": (expiration.date() - d).days,
        })
        prev_mtm = mtm
        prev_spot = spot_today

    last_chain = slice_by_day.get(expiration.date())
    if last_chain is not None and not last_chain.empty:
        S_exp = float(last_chain["stkPx"].iloc[0])
        pnl_zebra = pos.entry_credit + intrinsic_value_at_expiry(pos, S_exp)
    else:
        # Use last available day's MTM as a proxy
        S_exp = prev_spot
        pnl_zebra = prev_mtm

    # Per-share scale (consistent with existing engine).
    # ZEBRA controls 100 shares notional per contract; long-stock equivalent is
    # also 100 shares. So per-share comparison is apples-to-apples.
    pnl_stock = (S_exp - spot_entry)  # per-share P&L of holding 1 share

    flat_days = [r for r in daily_rows if r["flat_day"]]
    flat_mean_change = float(np.mean([r["mtm_change"] for r in flat_days])) if flat_days else np.nan
    flat_n = len(flat_days)

    summary = {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "slip": slip,
        "dte_at_entry": (expiration.date() - entry_date).days,
        "long_strike": n["long_strike"],
        "short_strike": n["short_strike"],
        "long_delta": float(pos.legs[0].delta),
        "short_delta": float(pos.legs[2].delta),
        "entry_delta": n["entry_delta"],
        "debit": float(debit),
        "long_extrinsic_total": n["long_extrinsic_total"],
        "short_extrinsic": n["short_extrinsic"],
        "extrinsic_cushion": n["extrinsic_cushion"],
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "capital_outlay": n["capital_outlay"],
        "capital_efficiency": n["debit"] / spot_entry,
        "pnl_zebra": float(pnl_zebra),
        "pnl_stock": float(pnl_stock),
        "capture_ratio": (
            float(pnl_zebra / pnl_stock) if pnl_stock > 0 else np.nan
        ),
        "flat_day_n": flat_n,
        "flat_day_mean_mtm_change": flat_mean_change,
        "max_loss": float(max_loss(pos)),
    }
    return summary, daily_rows


def simulate_ticker(ticker: str) -> tuple[list, list]:
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return [], []
    tdf = pd.read_parquet(path)
    if tdf.empty:
        return [], []
    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"])
    tdf["date_only"] = tdf["trade_date"].dt.date
    first_date = tdf["trade_date"].min().date()
    last_date = tdf["trade_date"].max().date()

    exp_str_to_date = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None:
            exp_str_to_date[s] = ts

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]
    opex_to_exp = {}
    for opex in opex_eligible:
        ts = pd.Timestamp(opex)
        for s, d in exp_str_to_date.items():
            if abs((d - ts).days) <= 1:
                opex_to_exp[ts] = s
                break

    exp_groups = {s: sub for s, sub in tdf.groupby("expirDate", sort=False)}
    summaries = []
    dailies = []

    for opex_ts, exp_str in opex_to_exp.items():
        exp_df = exp_groups[exp_str]
        slice_by_day = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}
        available_days = sorted(slice_by_day.keys())

        target = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target, available_days)
        if entry_date is None:
            continue
        for slip in SLIPS:
            C.activate_slip(slip)
            summary, daily = simulate_cycle(slice_by_day, available_days,
                                             entry_date, opex_ts, ticker, slip)
            if summary is None:
                continue
            summaries.append(summary)
            dailies.extend(daily)
    return summaries, dailies


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", nargs="+")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    cohort = pd.read_parquet(COHORT_PATH)["ticker"].tolist()
    cohort = [t for t in cohort if t != "SPX"]  # ORATS has SPX but skip per cohort design
    if args.ticker:
        cohort = [t for t in cohort if t in args.ticker]
    if args.limit:
        cohort = cohort[:args.limit]

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("zebra")
    log.info("ZEBRA backtest on %d cohort tickers (entry=%d-DTE)",
             len(cohort), ENTRY_DTE)

    all_sums = []
    all_dailies = []
    for i, t in enumerate(cohort, 1):
        s, d = simulate_ticker(t)
        all_sums.extend(s)
        all_dailies.extend(d)
        if i % 5 == 0 or i == len(cohort):
            log.info("  [%d/%d] %s: %d cycles, %d daily rows",
                     i, len(cohort), t, len(all_sums), len(all_dailies))

    if not all_sums:
        log.error("No cycles produced")
        return

    sdf = pd.DataFrame(all_sums)
    ddf = pd.DataFrame(all_dailies)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    sdf.to_parquet(RESULTS_OUT, index=False)
    ddf.to_parquet(DAILY_OUT, index=False)
    log.info("Wrote %d cycles to %s", len(sdf), RESULTS_OUT)
    log.info("Wrote %d daily rows to %s", len(ddf), DAILY_OUT)


if __name__ == "__main__":
    main()
