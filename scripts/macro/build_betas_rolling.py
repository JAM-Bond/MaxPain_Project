#!/usr/bin/env python3.11
"""
Rolling per-name macro betas — Phase 2 of macro-sensitivity profile.

For every ticker in the cohort, regress daily log returns onto 8 factor
shocks over a rolling window and store the time series of betas, t-stats,
R², and residual sigma.

Model (per ticker, per window ending at date t):
    log_ret_1d_i,t = α + Σ β_k · factor_k_t + ε
    factors_k ∈ {
        DGS10_d1, T10Y2Y_d1, T10YIE_d1, DTWEXBGS_d1,
        VIXCLS_d1, DCOILWTICO_d1, credit_d1, mkt_d1
    }
where:
    credit_d1 = (DBAA − DAAA).diff(1)   # 13y credit-stress proxy
    mkt_d1    = SPY log_ret_1d           # market factor

Including the market factor means each macro beta is the marginal
sensitivity beyond market beta — the right interpretation for
"is this name macro-sensitive in a way SPY isn't".

Output (long-format parquet):
    date        date     window end-date
    ticker      str
    factor      str      'alpha', 'DGS10_d1', ..., 'mkt_d1'
    beta        float64  coefficient
    t_stat      float64
    r2          float64  window R²
    resid_sigma float64  σ of in-window residuals
    n_obs       int      typically equals --window

Usage:
    python3.11 build_betas_rolling.py                    # 252d (default)
    python3.11 build_betas_rolling.py --window 63        # quarterly / stress
    python3.11 build_betas_rolling.py --window 252 --tickers MSFT,JPM,XLU  # subset
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.regression.rolling import RollingOLS
import statsmodels.api as sm

ROOT = Path.home() / "MaxPain_Project"
JOIN_PATH = ROOT / "data/macro/macro_join_13y.parquet"
OUT_DIR = ROOT / "data/macro"

FACTORS = [
    "DGS10_d1",
    "T10Y2Y_d1",
    "T10YIE_d1",
    "DTWEXBGS_d1",
    "VIXCLS_d1",
    "DCOILWTICO_d1",
    "credit_d1",
    "mkt_d1",
]


def build_factor_frame(join: pd.DataFrame) -> pd.DataFrame:
    """One row per trading day with all 8 factor columns. Derives mkt_d1 from
    SPY and credit_d1 from DBAA-DAAA."""
    spy = (join[join["ticker"] == "SPY"][["date", "log_ret_1d"]]
           .rename(columns={"log_ret_1d": "mkt_d1"})
           .sort_values("date"))
    macro = (join.drop_duplicates(subset=["date"])
             [["date", "DGS10_d1", "T10Y2Y_d1", "T10YIE_d1",
               "DTWEXBGS_d1", "VIXCLS_d1", "DCOILWTICO_d1", "DBAA", "DAAA"]]
             .sort_values("date"))
    macro["credit_d1"] = (macro["DBAA"] - macro["DAAA"]).diff(1)
    macro = macro.drop(columns=["DBAA", "DAAA"])
    out = macro.merge(spy, on="date", how="left")
    return out[["date"] + FACTORS]


def run_one_ticker(ticker: str, returns: pd.Series, X_full: pd.DataFrame,
                   window: int) -> pd.DataFrame:
    """Roll OLS for a single ticker. Returns long-format frame."""
    # Align returns to X
    df = X_full.join(returns.rename("y"), how="inner").dropna()
    if len(df) < window:
        return pd.DataFrame()

    X = sm.add_constant(df[FACTORS].values)
    y = df["y"].values

    model = RollingOLS(endog=y, exog=X, window=window, min_nobs=window).fit()

    params = model.params  # (n_dates, n_factors+1)
    tvals = model.tvalues
    rsq = model.rsquared
    # Residuals σ — derive from RSS / (n - k)
    # statsmodels exposes .mse_resid which is RSS / (n - k)
    resid_sigma = np.sqrt(model.mse_resid)

    n = len(df)
    col_names = ["alpha"] + FACTORS
    rows = []
    for col_idx, fname in enumerate(col_names):
        b = params[:, col_idx]
        t = tvals[:, col_idx]
        # Only emit rows where regression actually ran (post-warmup)
        mask = ~np.isnan(b)
        if not mask.any():
            continue
        sub = pd.DataFrame({
            "date":        df.index.values[mask],
            "ticker":      ticker,
            "factor":      fname,
            "beta":        b[mask],
            "t_stat":      t[mask],
            "r2":          rsq[mask],
            "resid_sigma": resid_sigma[mask],
            "n_obs":       window,
        })
        rows.append(sub)
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=252,
                    help="rolling window in trading days (252=1y, 63=quarter)")
    ap.add_argument("--tickers", default=None,
                    help="comma-separated subset (default: all cohort tickers)")
    ap.add_argument("--out", default=None,
                    help="output parquet path (default: data/macro/beta_rolling_{N}d.parquet)")
    args = ap.parse_args()

    out_path = Path(args.out) if args.out else OUT_DIR / f"beta_rolling_{args.window}d.parquet"

    print(f"Loading {JOIN_PATH}...")
    join = pd.read_parquet(JOIN_PATH)
    print(f"  join: {join.shape}  tickers={join['ticker'].nunique()}  "
          f"{join['date'].min().date()} → {join['date'].max().date()}")

    print("Building factor frame (includes mkt_d1 from SPY, credit_d1 from DBAA-DAAA)...")
    X_full = build_factor_frame(join).set_index("date")
    print(f"  factor frame: {X_full.shape}  factors={list(X_full.columns)}")

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        tickers = sorted(join["ticker"].unique())
    print(f"Running {len(tickers)} tickers × window={args.window}d...")

    t0 = time.time()
    parts = []
    skipped = []
    for i, t in enumerate(tickers, 1):
        sub = join[join["ticker"] == t][["date", "log_ret_1d"]].set_index("date")
        if sub.empty:
            skipped.append(t)
            continue
        res = run_one_ticker(t, sub["log_ret_1d"], X_full, args.window)
        if res.empty:
            skipped.append(t)
            continue
        parts.append(res)
        if i % 20 == 0 or i == len(tickers):
            elapsed = time.time() - t0
            print(f"  [{i:3d}/{len(tickers)}] {t:6s} "
                  f"rows={len(res):6d}  ({elapsed:5.1f}s elapsed)")

    if not parts:
        print("No results — aborting.")
        sys.exit(1)

    out = pd.concat(parts, ignore_index=True)
    out = out.sort_values(["ticker", "factor", "date"]).reset_index(drop=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False, compression="snappy")

    print(f"\nWrote {len(out):,} rows × {len(out.columns)} cols → {out_path}")
    print(f"Tickers: {out['ticker'].nunique()}  Factors: {out['factor'].nunique()}  "
          f"Date range: {out['date'].min().date()} → {out['date'].max().date()}")
    print(f"Total time: {time.time()-t0:.1f}s")
    if skipped:
        print(f"Skipped (insufficient history): {skipped}")


if __name__ == "__main__":
    main()
