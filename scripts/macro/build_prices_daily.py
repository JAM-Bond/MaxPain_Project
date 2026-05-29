#!/usr/bin/env python3.11
"""
Prices spine — Phase 1 of macro-sensitivity profile.

For each ticker in the current operational cohort (the union of all
COHORT_* lists in scripts/qualifier/gate_config.py), extract the daily
close (stkPx) from data/orats/by_ticker/{TICKER}.parquet and compute
1d/5d/20d log returns. Writes a long-format parquet at
data/macro/prices_daily_13y.parquet.

The ORATS by_ticker parquets are option chains with ~3M rows each
(one row per (trade_date, expirDate, strike)). For the macro regression we
only need the daily close — one row per (trade_date) per ticker. We dedupe
on trade_date taking the first stkPx (the close is constant within a day).

Output schema (long):
    date         date
    ticker       str
    close        float64       stkPx
    log_ret_1d   float64
    log_ret_5d   float64
    log_ret_20d  float64

Usage:
    python3.11 build_prices_daily.py                 # cohort union (default, ~162)
    python3.11 build_prices_daily.py --universe v1   # backtest universe (150)
    python3.11 build_prices_daily.py --universe v2   # expansion candidates (163)
    python3.11 build_prices_daily.py --universe all  # v1 ∪ v2 ∪ cohorts (~355)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
UNIVERSE_V1 = ROOT / "data/profile/universe_v1.parquet"
UNIVERSE_V2 = ROOT / "data/profile/universe_v2_liquidity_pool.parquet"
OUT_PATH = ROOT / "data/macro/prices_daily_13y.parquet"

sys.path.insert(0, str(ROOT))
from lib.adjusted_close import load_adjusted_close  # noqa: E402


def load_cohort_union() -> set[str]:
    """Union of every COHORT_* list in scripts/qualifier/gate_config.py."""
    sys.path.insert(0, str(ROOT / "scripts/qualifier"))
    import gate_config as gc
    union: set[str] = set()
    for name in dir(gc):
        if not name.startswith("COHORT_"):
            continue
        val = getattr(gc, name)
        if isinstance(val, list):
            union.update(val)
    return union


def load_universe(version: str) -> list[str]:
    if version == "cohort":
        return sorted(load_cohort_union())
    if version == "v1":
        return sorted(pd.read_parquet(UNIVERSE_V1)["ticker"].unique().tolist())
    if version == "v2":
        return sorted(pd.read_parquet(UNIVERSE_V2)["ticker"].unique().tolist())
    if version == "all":
        v1 = set(pd.read_parquet(UNIVERSE_V1)["ticker"])
        v2 = set(pd.read_parquet(UNIVERSE_V2)["ticker"])
        return sorted(v1 | v2 | load_cohort_union())
    raise ValueError(f"unknown universe: {version}")


def extract_one(ticker: str) -> pd.DataFrame | None:
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return None
    # Split-adjusted close — raw ORATS stkPx is unadjusted for splits, which
    # corrupts the rolling betas over any 252d window spanning a split. See
    # lib/adjusted_close. (Option backtests stay on the raw archive.)
    s = load_adjusted_close(ticker)
    if s.empty:
        return None
    df = s.rename("close").reset_index().rename(columns={"trade_date": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df["ticker"] = ticker

    df["log_close"] = np.log(df["close"])
    df["log_ret_1d"]  = df["log_close"].diff(1)
    df["log_ret_5d"]  = df["log_close"].diff(5)
    df["log_ret_20d"] = df["log_close"].diff(20)
    df = df.drop(columns=["log_close"])

    # Backstop: any residual single-day move > ~123% (|log|>0.80, i.e. price
    # >2.2x or <0.45x in a day) that survived split-adjustment is a data
    # artifact (missed/large split, merger, ticker change, IPO first-print) —
    # no real equity moves that much. NaN it so it can't poison the betas
    # (which regress on 1d returns). See lib/adjusted_close for the split layer.
    bad = df["log_ret_1d"].abs() > 0.80
    df.loc[bad, ["log_ret_1d", "log_ret_5d", "log_ret_20d"]] = np.nan
    return df[["date", "ticker", "close", "log_ret_1d", "log_ret_5d", "log_ret_20d"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["cohort", "v1", "v2", "all"], default="cohort")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    tickers = load_universe(args.universe)
    print(f"universe_{args.universe}: {len(tickers)} tickers")

    parts = []
    missing = []
    for i, t in enumerate(tickers, 1):
        df = extract_one(t)
        if df is None or df.empty:
            missing.append(t)
            print(f"  [{i:3d}/{len(tickers)}] {t:6s} MISSING")
            continue
        parts.append(df)
        if i % 20 == 0 or i == len(tickers):
            print(f"  [{i:3d}/{len(tickers)}] {t:6s} n={len(df):5d}  "
                  f"{df['date'].min().date()} → {df['date'].max().date()}")

    if not parts:
        print("No tickers extracted — aborting.")
        sys.exit(1)

    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["ticker", "date"]).reset_index(drop=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False, compression="snappy")

    print(f"\nWrote {len(out):,} rows × {len(out.columns)} cols → {out_path}")
    print(f"Tickers: {out['ticker'].nunique()}  "
          f"Date range: {out['date'].min().date()} → {out['date'].max().date()}")
    if missing:
        print(f"Missing from ORATS: {missing}")


if __name__ == "__main__":
    main()
