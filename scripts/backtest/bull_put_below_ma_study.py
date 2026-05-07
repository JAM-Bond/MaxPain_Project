#!/usr/bin/env python3.11
"""
Bull-put × spot-vs-200-DMA study.

Question: do bull_put credit verticals work on tickers whose underlying is
trading BELOW its 200-DMA at entry? The MSFT-on-2026-05-04 case is the
precipitating example — the qualifier said GO on a name the cascade flagged
🔴. We never split the bull_put backtest by per-name MA position; this fills
that gap.

Reuses the cycle-level output from bull_put_moneyness_backtest.py (one row
per ticker × cycle × moneyness, already simulated). Joins each row with the
underlying's 200-DMA on entry_date and buckets:

  ABOVE_3PCT   spot_entry / ma200 - 1  >  +0.03
  AT_ZONE      -0.03 ≤ pct ≤ +0.03
  BELOW_3PCT   pct < -0.03
  BELOW_10PCT  pct < -0.10   (the deep-stress sub-bucket — MSFT was here)

Output: data/profile/bull_put_below_ma_study.parquet (per-cell aggregates)
        + console report.

Slip is whatever the upstream backtest used (0.50 per the moneyness study).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_IN = ROOT / "data/profile/bull_put_moneyness_results.parquet"
RESULTS_OUT = ROOT / "data/profile/bull_put_below_ma_study.parquet"

ABOVE_THRESHOLD = 0.03
BELOW_THRESHOLD = -0.03
DEEP_BELOW = -0.10


# ── 200-DMA per ticker (cached) ─────────────────────────────────────────────

def _ticker_ma200_series(ticker: str) -> pd.Series | None:
    p = BY_TICKER / f"{ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
    df = (df.dropna(subset=["stkPx"])
            .drop_duplicates("trade_date")
            .sort_values("trade_date"))
    if len(df) < 200:
        return None
    df["ma200"] = df["stkPx"].rolling(200).mean()
    df = df.dropna(subset=["ma200"]).set_index("trade_date")
    return df["ma200"]


def _bucket(pct: float) -> str:
    if pct < DEEP_BELOW:
        return "BELOW_10PCT"
    if pct < BELOW_THRESHOLD:
        return "BELOW_3_TO_10PCT"
    if pct <= ABOVE_THRESHOLD:
        return "AT_ZONE"
    return "ABOVE_3PCT"


def attach_ma_buckets(df: pd.DataFrame) -> pd.DataFrame:
    """For every cycle row, look up the underlying's 200-DMA on entry_date."""
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    out_pct = np.full(len(df), np.nan)
    out_bucket = np.array(["UNKNOWN"] * len(df), dtype=object)

    # Group by ticker so we load each parquet once
    for tk, idx in df.groupby("ticker").groups.items():
        ma = _ticker_ma200_series(tk)
        if ma is None:
            continue
        # Reindex to the unique entry dates in this ticker's group, forward-fill
        # the 200-DMA to the nearest prior trading day (handles cycles entered
        # on bank holidays etc.)
        sub = df.loc[idx]
        ma_reindex = ma.reindex(
            ma.index.union(sub["entry_date"].unique())
        ).ffill()
        m = ma_reindex.loc[sub["entry_date"]].values
        spot = sub["spot_entry"].values
        with np.errstate(divide="ignore", invalid="ignore"):
            pct = np.where(m > 0, spot / m - 1.0, np.nan)
        out_pct[idx] = pct
        out_bucket[idx] = [_bucket(p) if np.isfinite(p) else "UNKNOWN"
                            for p in pct]

    df["pct_to_ma200_at_entry"] = out_pct
    df["ma_bucket"] = out_bucket
    return df


# ── Aggregation helpers ────────────────────────────────────────────────────

def adequacy(n: int) -> str:
    if n < 10:
        return "PRELIMINARY"
    if n < 20:
        return "SUGGESTIVE"
    if n < 30:
        return "DEVELOPING"
    return "ADEQUATE"


def aggregate(df: pd.DataFrame, group_cols: list[str], pnl_col: str) -> pd.DataFrame:
    g = df.groupby(group_cols, dropna=False)[pnl_col]
    out = pd.DataFrame({
        "n": g.count(),
        "mean": g.mean().round(4),
        "median": g.median().round(4),
        "win_rate": (df.assign(_w=df[pnl_col] > 0)
                       .groupby(group_cols, dropna=False)["_w"].mean()
                       .round(3)),
        "total": g.sum().round(2),
        "worst": g.min().round(2),
        "best": g.max().round(2),
    }).reset_index()
    out["adequacy"] = out["n"].apply(adequacy)
    return out


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    if not RESULTS_IN.exists():
        print(f"ERROR: input parquet missing: {RESULTS_IN}")
        print("Run scripts/backtest/bull_put_moneyness_backtest.py first.")
        sys.exit(1)

    print(f"Loading {RESULTS_IN.name} ...")
    df = pd.read_parquet(RESULTS_IN)
    print(f"  {len(df):,} cycle rows × {df['ticker'].nunique()} tickers")

    print("Attaching 200-DMA bucket per cycle ...")
    df = attach_ma_buckets(df)
    matched = df["ma_bucket"].ne("UNKNOWN").sum()
    print(f"  matched: {matched:,} of {len(df):,} cycles "
          f"({matched/len(df)*100:.1f}%)")

    df = df[df["ma_bucket"] != "UNKNOWN"]

    print()
    print("=" * 78)
    print("  BUCKET DISTRIBUTION")
    print("=" * 78)
    print(df["ma_bucket"].value_counts().to_string())

    print()
    print("=" * 78)
    print("  HEADLINE — bucket × moneyness, MANAGED-50% exit")
    print("=" * 78)
    head_mgd = aggregate(df, ["ma_bucket", "moneyness"], "mgd50_pnl")
    # Order buckets and moneyness for readability
    bucket_order = ["ABOVE_3PCT", "AT_ZONE", "BELOW_3_TO_10PCT", "BELOW_10PCT"]
    moneyness_order = ["OTM", "ATM", "ITM"]
    head_mgd["ma_bucket"] = pd.Categorical(head_mgd["ma_bucket"],
                                             bucket_order, ordered=True)
    head_mgd["moneyness"] = pd.Categorical(head_mgd["moneyness"],
                                             moneyness_order, ordered=True)
    head_mgd = head_mgd.sort_values(["ma_bucket", "moneyness"])
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 220)
    print(head_mgd.to_string(index=False))

    print()
    print("=" * 78)
    print("  HEADLINE — bucket × moneyness, HELD-TO-EXPIRY")
    print("=" * 78)
    head_held = aggregate(df, ["ma_bucket", "moneyness"], "held_pnl")
    head_held["ma_bucket"] = pd.Categorical(head_held["ma_bucket"],
                                              bucket_order, ordered=True)
    head_held["moneyness"] = pd.Categorical(head_held["moneyness"],
                                              moneyness_order, ordered=True)
    head_held = head_held.sort_values(["ma_bucket", "moneyness"])
    print(head_held.to_string(index=False))

    print()
    print("=" * 78)
    print("  BUCKET TOTALS — collapsed across moneyness, managed-50%")
    print("=" * 78)
    tot = aggregate(df, ["ma_bucket"], "mgd50_pnl")
    tot["ma_bucket"] = pd.Categorical(tot["ma_bucket"], bucket_order,
                                       ordered=True)
    tot = tot.sort_values("ma_bucket")
    print(tot.to_string(index=False))

    # Per-ticker × bucket breakdown for the BELOW_10PCT slice (MSFT-style)
    deep = df[df["ma_bucket"] == "BELOW_10PCT"]
    if not deep.empty:
        print()
        print("=" * 78)
        print(f"  BELOW_10PCT cells by ticker (managed-50%) — N={len(deep):,}")
        print("=" * 78)
        per_t = (deep.groupby(["ticker", "moneyness"])["mgd50_pnl"]
                       .agg(["count", "mean", "sum"]).round(3)
                       .rename(columns={"count": "n", "mean": "mean_pnl",
                                          "sum": "total_pnl"}))
        per_t = per_t.sort_values("total_pnl", ascending=False)
        # Show only tickers with N≥3 in the bucket to limit noise
        eligible_tickers = (deep.groupby("ticker").size() >= 3)
        keep = eligible_tickers[eligible_tickers].index.tolist()
        per_t = per_t.reset_index()
        per_t = per_t[per_t["ticker"].isin(keep)]
        print(per_t.to_string(index=False))

        # Specifically MSFT
        msft = deep[deep["ticker"] == "MSFT"]
        if not msft.empty:
            print()
            print(f"  MSFT below-10% bull_put cycles: N={len(msft)}")
            print(msft[["entry_date", "moneyness", "spot_entry",
                        "pct_to_ma200_at_entry", "mgd50_pnl",
                        "held_pnl"]].to_string(index=False))

    # Persist
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    print()
    print(f"Wrote: {RESULTS_OUT}")


if __name__ == "__main__":
    main()
