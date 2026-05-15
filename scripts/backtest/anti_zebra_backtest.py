"""Anti-ZEBRA backtest — bearish mirror of ZEBRA.

Buy 2× ITM put at ~−0.70 put delta + sell 1× ATM put at ~−0.50 put delta.
Same zero-theta extrinsic rule. Synthetic short-stock with capped downside.

Test design mirrors zebra_backtest.py:
  - Per (ticker, monthly OpEx) entry at ~75 DTE, held to expiration
  - Tier-1 / tier-2 / v1.5 cohort, slip=0.25 + slip=0.50
  - Per-cycle output: P&L, win rate, capture vs short-stock, capital efficiency,
    extrinsic-rule fire rate

Output:
  data/profile/anti_zebra_results.parquet
  data/profile/anti_zebra_daily_mtm.parquet
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
from structures import open_anti_zebra, close_cost, intrinsic_value_at_expiry, max_loss

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
COHORT_PATH = ROOT / "data/profile/research_cohort_v15.parquet"
RESULTS_OUT = ROOT / "data/profile/anti_zebra_results.parquet"
DAILY_OUT = ROOT / "data/profile/anti_zebra_daily_mtm.parquet"

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
    pos = open_anti_zebra(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        # Track fire-rate failures as an explicit summary row so we can count them
        return {"ticker": ticker, "expiration": expiration,
                "entry_date": pd.Timestamp(entry_date), "slip": slip,
                "fired": False}, []

    n = pos.notes
    spot_entry = pos.underlying_entry
    debit = n["debit"]

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]

    daily_rows = []
    prev_mtm = -debit
    prev_spot = spot_entry

    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        cost = close_cost(pos, chain_d)
        if cost is None:
            continue
        pnl_if_closed = pos.entry_credit - cost
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
        pnl = pos.entry_credit + intrinsic_value_at_expiry(pos, S_exp)
    else:
        S_exp = prev_spot
        pnl = prev_mtm

    pnl_short_stock = (spot_entry - S_exp)  # short-stock per-share P&L equivalent

    flat_days = [r for r in daily_rows if r["flat_day"]]
    flat_mean_change = float(np.mean([r["mtm_change"] for r in flat_days])) if flat_days else np.nan

    summary = {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "slip": slip,
        "fired": True,
        "dte_at_entry": (expiration.date() - entry_date).days,
        "long_strike": n["long_strike"],
        "short_strike": n["short_strike"],
        "long_put_delta": float(pos.legs[0].delta),
        "short_put_delta": float(pos.legs[2].delta),
        "entry_delta": n["entry_delta"],
        "debit": float(debit),
        "long_extrinsic_total": n["long_extrinsic_total"],
        "short_extrinsic": n["short_extrinsic"],
        "extrinsic_cushion": n["extrinsic_cushion"],
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "capital_outlay": n["capital_outlay"],
        "capital_efficiency": debit / spot_entry,
        "pnl_anti_zebra": float(pnl),
        "pnl_short_stock": float(pnl_short_stock),
        "capture_ratio": (
            float(pnl / pnl_short_stock) if pnl_short_stock > 0 else np.nan
        ),
        "flat_day_n": len(flat_days),
        "flat_day_mean_mtm_change": flat_mean_change,
        "max_loss": float(max_loss(pos)),
    }
    return summary, daily_rows


def simulate_ticker(ticker: str):
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
            if summary is not None:
                summaries.append(summary)
                dailies.extend(daily)
    return summaries, dailies


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", nargs="+")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    cohort = pd.read_parquet(COHORT_PATH)["ticker"].tolist()
    cohort = [t for t in cohort if t != "SPX"]
    if args.ticker:
        cohort = [t for t in cohort if t in args.ticker]
    if args.limit:
        cohort = cohort[:args.limit]

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("anti_zebra")
    log.info("Anti-ZEBRA backtest on %d cohort tickers (entry=%d-DTE)",
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
    if not ddf.empty:
        ddf.to_parquet(DAILY_OUT, index=False)
    log.info("Wrote %d cycles to %s", len(sdf), RESULTS_OUT)

    # ─────────────────────────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────────────────────────
    fired = sdf[sdf["fired"]].copy()
    n_fired = len(fired)
    n_total = len(sdf)
    print(f"\n=== Anti-ZEBRA backtest summary ({n_total} ticker-cycle-slip rows) ===")
    print(f"Extrinsic-rule fire rate: {n_fired}/{n_total} = {n_fired/n_total*100:.1f}%")
    print()
    if fired.empty:
        print("No cycles fired the extrinsic rule — anti-ZEBRA structurally fails on this cohort.")
        return

    # Headline at slip=0.50 (matches ZEBRA primary reporting)
    for slip in SLIPS:
        sub = fired[fired["slip"] == slip]
        if sub.empty:
            continue
        m = sub["pnl_anti_zebra"].mean()
        w = (sub["pnl_anti_zebra"] > 0).mean()
        mn = sub["pnl_anti_zebra"].min()
        mx = sub["pnl_anti_zebra"].max()
        ss = sub["pnl_short_stock"].mean()
        cap = sub["capture_ratio"].median()
        cap_eff = sub["capital_efficiency"].mean()
        print(f"slip={slip}: N={len(sub):4d}  mean=${m:+.2f}  win={w:.1%}  "
              f"worst=${mn:+.2f}  best=${mx:+.2f}  short_stock_mean=${ss:+.2f}  "
              f"median_capture={cap:.3f}  cap_eff={cap_eff:.3f}")
    print()

    # Per-ticker leaderboard at slip=0.50
    s50 = fired[fired["slip"] == 0.50]
    if not s50.empty:
        agg = s50.groupby("ticker").agg(
            n=("pnl_anti_zebra", "size"),
            mean_az=("pnl_anti_zebra", "mean"),
            mean_short=("pnl_short_stock", "mean"),
            win=("pnl_anti_zebra", lambda x: (x > 0).mean()),
            worst=("pnl_anti_zebra", "min"),
            cap_eff=("capital_efficiency", "mean"),
        ).round(3)
        agg["az_minus_short"] = (agg["mean_az"] - agg["mean_short"]).round(2)
        agg = agg.sort_values("mean_az", ascending=False)
        print("=== Per-ticker at slip=0.50 (sorted by mean anti-ZEBRA P/L) ===")
        print(agg.to_string())
        print()
        print(f"Tickers positive at slip=0.50: {(agg['mean_az'] > 0).sum()}/{len(agg)}")
        print(f"Tickers beating short-stock (az > short): {(agg['az_minus_short'] > 0).sum()}/{len(agg)}")


if __name__ == "__main__":
    main()
