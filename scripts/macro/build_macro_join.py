#!/usr/bin/env python3.11
"""
Macro join — Phase 1 of macro-sensitivity profile.

Joins the FRED long-format parquet (build_fred_daily.py) with the prices
long-format parquet (build_prices_daily.py) on date, producing the wide
table that Phase 2 (rolling regression) consumes.

Pipeline:
  1. Load FRED long → pivot to wide (date × series_id).
  2. Build a trading-day grid from SPY's price history (the most complete
     ticker in the universe). Reindex FRED to that grid, forward-fill
     monthly/quarterly series.
  3. Compute change-in-factor columns for the macro covariates that matter
     in changes, not levels (yields, dollar, OAS):
       d1, d5, d20 (1d, 5d, 20d differences in basis points / index points)
  4. Left-join onto prices (date × ticker).

Output schema (wide, one row per (date, ticker)):
    date, ticker, close, log_ret_1d, log_ret_5d, log_ret_20d,
    DFF, DGS2, DGS10, ..., VIXCLS, NFCI, ...                     # ffilled levels
    DGS10_d1, DGS10_d5, DGS10_d20, DTWEXBGS_d1, ...              # changes

Usage:
    python3.11 build_macro_join.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
FRED_PATH   = ROOT / "data/macro/fred_daily_13y.parquet"
PRICES_PATH = ROOT / "data/macro/prices_daily_13y.parquet"
OUT_PATH    = ROOT / "data/macro/macro_join_13y.parquet"

# Series where the level matters less than the change (regression covariates
# should be roughly stationary; yields / spreads / dollar are persistent levels)
CHANGE_SERIES = [
    "DFF", "DTB3", "DGS2", "DGS5", "DGS10", "DGS30",
    "T10Y2Y", "T10YIE",
    "DTWEXBGS", "DCOILWTICO",
    "VIXCLS", "BAMLC0A0CM", "BAMLH0A0HYM2",
    "DAAA", "DBAA",
    "NFCI",
]
CHANGE_HORIZONS = [1, 5, 20]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    print("Loading FRED...")
    fred_long = pd.read_parquet(FRED_PATH)
    fred_wide = (fred_long
                 .pivot(index="date", columns="series_id", values="value")
                 .sort_index())
    print(f"  fred_wide: {fred_wide.shape}  {fred_wide.index.min().date()} → {fred_wide.index.max().date()}")

    print("Loading prices...")
    prices = pd.read_parquet(PRICES_PATH)
    print(f"  prices: {prices.shape}  {prices['date'].min().date()} → {prices['date'].max().date()}  "
          f"tickers={prices['ticker'].nunique()}")

    # Trading-day grid: union of all dates that appear in prices
    trading_days = pd.DatetimeIndex(sorted(prices["date"].unique()))
    print(f"  trading_days: {len(trading_days)}  ({trading_days.min().date()} → {trading_days.max().date()})")

    # Reindex FRED to trading-day grid, forward-fill monthly/quarterly
    print("Aligning FRED to trading-day grid + ffill...")
    fred_aligned = fred_wide.reindex(trading_days).ffill()

    # Compute change-in-factor columns
    print(f"Computing changes for {len(CHANGE_SERIES)} series × {len(CHANGE_HORIZONS)} horizons...")
    change_frames = []
    for sid in CHANGE_SERIES:
        if sid not in fred_aligned.columns:
            print(f"  WARN: {sid} not in FRED — skipping changes")
            continue
        for h in CHANGE_HORIZONS:
            change_frames.append(fred_aligned[sid].diff(h).rename(f"{sid}_d{h}"))
    if change_frames:
        changes = pd.concat(change_frames, axis=1)
        fred_aligned = pd.concat([fred_aligned, changes], axis=1)
    fred_aligned.index.name = "date"
    fred_aligned = fred_aligned.reset_index()

    print(f"  fred_aligned: {fred_aligned.shape}")

    print("Joining prices × FRED on date...")
    merged = prices.merge(fred_aligned, on="date", how="left")
    print(f"  merged: {merged.shape}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(out_path, index=False, compression="snappy")

    print(f"\nWrote {len(merged):,} rows × {len(merged.columns)} cols → {out_path}")
    print(f"Tickers: {merged['ticker'].nunique()}  "
          f"Date range: {merged['date'].min().date()} → {merged['date'].max().date()}")

    # Quick sanity: per-column non-null counts on a sample slice (last date)
    last = merged[merged["date"] == merged["date"].max()]
    null_pct = (last.isna().sum() / len(last) * 100).round(1)
    print(f"\nLast-date null % by column (cohort-wide, n={len(last)}):")
    for col, pct in null_pct.items():
        if pct > 0:
            print(f"  {col:30s} {pct:5.1f}%")


if __name__ == "__main__":
    main()
