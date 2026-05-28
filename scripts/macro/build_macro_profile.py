#!/usr/bin/env python3.11
"""
Macro profile builder — Phase 5 of macro-sensitivity profile.

Synthesizes the rolling-beta time series (Phase 2) and regime-stability
analysis (Phase 3) into a single per-ticker attribute table that the
qualifier, daily alert, and post-mortem can read.

The key design choice is the `*_use` flag on each macro factor: downstream
code should NOT re-litigate whether a β is trustworthy. The profile builder
makes that decision once, here, based on Phase 3 stability tagging plus
current-regime significance.

Output: data/macro/macro_profile.parquet  (1 row per cohort ticker, ~25 cols)

Usage:
    python3.11 build_macro_profile.py                # default
    python3.11 build_macro_profile.py --regime PLATEAU_CUTS  # explicit current regime
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BETA_252_PATH = ROOT / "data/macro/beta_rolling_252d.parquet"
SUMMARY_PATH = ROOT / "data/macro/beta_regime_summary.parquet"
TAGS_PATH = ROOT / "data/macro/beta_stability_tags.parquet"
OUT_PATH = ROOT / "data/macro/macro_profile.parquet"

CURRENT_REGIME_DEFAULT = "PLATEAU_CUTS"

# Tier band definitions — empirically tuned to the 162-name cohort distribution
MKT_TIERS = [
    (1.5, "HIGH"),         # super-high beta: ORCL/RIOT/HOOD/RMBS
    (1.0, "MED_HIGH"),     # MSFT/NVDA/AMZN range
    (0.5, "MED"),          # most S&P names
    (0.0, "LOW"),          # defensive
    (-np.inf, "NEG"),      # negatively market-correlated (XLU current, XLE current)
]

# Rate β tiers — based on cohort distribution (typical β ±0.05, leveraged TBT/TMF ±0.3)
RATE_TIERS = [
    (0.05,  "POS_HIGH"),
    (0.02,  "POS_MED"),
    (-0.02, "NEUTRAL"),
    (-0.05, "NEG_MED"),
    (-np.inf, "NEG_HIGH"),
]

# Credit / inflation β tiers — similar scale
CREDIT_TIERS = [
    (0.05,  "POS_HIGH"),
    (0.02,  "POS_MED"),
    (-0.02, "NEUTRAL"),
    (-0.05, "NEG_MED"),
    (-np.inf, "NEG_HIGH"),
]

# Dollar β tiers — magnitudes smaller (cohort range typically ±0.05, mostly ±0.02)
DOLLAR_TIERS = [
    (0.005,   "USD_POS"),
    (-0.005,  "NEUTRAL"),
    (-0.020,  "USD_INV_WEAK"),
    (-np.inf, "USD_INV_STRONG"),
]

# Oil β tiers
OIL_TIERS = [
    (0.005,   "OIL_POS"),
    (-0.005,  "NEUTRAL"),
    (-np.inf, "OIL_NEG"),
]

# Vol (VIX) β tiers — magnitudes tiny (±0.005 typical)
VOL_TIERS = [
    (0.001,   "VOL_POS"),
    (-0.001,  "NEUTRAL"),
    (-np.inf, "VOL_NEG"),
]


def tier(value: float, ladder: list[tuple[float, str]]) -> str:
    """Find the first tier whose threshold the value clears (descending ladder)."""
    if pd.isna(value):
        return "NA"
    for thresh, label in ladder:
        if value >= thresh:
            return label
    return ladder[-1][1]


def latest_beta_per_ticker(beta: pd.DataFrame) -> pd.DataFrame:
    """Return the last-date β snapshot per (ticker, factor) with t-stat, R², n_obs."""
    last_date = beta["date"].max()
    snap = beta[beta["date"] == last_date].copy()
    return snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regime", default=CURRENT_REGIME_DEFAULT,
                    help="current regime label for 'use' significance check")
    args = ap.parse_args()

    print(f"Loading β rolling {BETA_252_PATH.name}...")
    beta = pd.read_parquet(BETA_252_PATH)
    print(f"  {beta.shape}  last_date={beta['date'].max().date()}")

    print(f"Loading regime summary + stability tags...")
    summary = pd.read_parquet(SUMMARY_PATH)
    tags = pd.read_parquet(TAGS_PATH)

    # Current-regime per (ticker, factor): mean_beta + frac_significant
    cur = (summary[summary["regime"] == args.regime]
           .set_index(["ticker", "factor"])[["mean_beta", "frac_significant"]]
           .rename(columns={"mean_beta": "regime_mean",
                            "frac_significant": "regime_frac_sig"}))

    # Latest-date β snapshot
    snap = latest_beta_per_ticker(beta)
    snap_idx = snap.set_index(["ticker", "factor"])

    # Stability tags (indexed)
    tags_idx = tags.set_index(["ticker", "factor"])[["tag", "material", "magnitude_ratio"]]

    # R² and n_obs come from the snap rows where factor='alpha' (regression-level summary)
    r2 = snap[snap["factor"] == "alpha"].set_index("ticker")[["r2", "n_obs"]]

    as_of = beta["date"].max()
    tickers = sorted(beta["ticker"].unique())

    rows = []
    for tk in tickers:
        row: dict = {"ticker": tk, "as_of_date": as_of, "regime": args.regime}

        # Per-factor block builder
        def block(factor: str, tier_ladder, prefix: str, quantitative: bool):
            try:
                cur_b = float(snap_idx.loc[(tk, factor), "beta"])
            except KeyError:
                cur_b = np.nan
            try:
                stab = tags_idx.loc[(tk, factor), "tag"]
                material = bool(tags_idx.loc[(tk, factor), "material"])
            except KeyError:
                stab = "NA"
                material = False
            try:
                reg_sig = float(cur.loc[(tk, factor), "regime_frac_sig"])
            except KeyError:
                reg_sig = np.nan

            row[f"beta_{prefix}"] = cur_b
            row[f"beta_{prefix}_tier"] = tier(cur_b, tier_ladder)
            row[f"beta_{prefix}_stability"] = stab

            # The trust decision: stable, OR (material + significant in current regime)
            if quantitative:
                use = (stab == "STABLE") or (material and not np.isnan(reg_sig) and reg_sig > 0.5)
                row[f"beta_{prefix}_use"] = bool(use)

        block("mkt_d1",      MKT_TIERS,    "mkt",     quantitative=True)
        block("DGS10_d1",    RATE_TIERS,   "dgs10",   quantitative=True)
        block("credit_d1",   CREDIT_TIERS, "credit",  quantitative=True)
        block("T10YIE_d1",   CREDIT_TIERS, "t10yie",  quantitative=True)

        # Dollar/oil/vol: tier-only (Phase 3 said magnitudes too small for sizing)
        # Use REGIME-MEAN β for tier assignment, not latest snapshot — directional
        # consistency across regimes is the signal here
        def directional_tier(factor: str, ladder) -> str:
            try:
                reg_means = (summary[(summary["ticker"] == tk) & (summary["factor"] == factor)]
                             ["mean_beta"].values)
                if len(reg_means) == 0:
                    return "NA"
                # If signs are mixed, return NEUTRAL regardless of magnitudes
                signs = np.sign(reg_means[reg_means != 0])
                if len(np.unique(signs)) > 1:
                    return "NEUTRAL"
                return tier(float(np.mean(reg_means)), ladder)
            except Exception:
                return "NA"

        row["dollar_tier"] = directional_tier("DTWEXBGS_d1", DOLLAR_TIERS)
        row["oil_tier"]    = directional_tier("DCOILWTICO_d1", OIL_TIERS)
        row["vol_tier"]    = directional_tier("VIXCLS_d1", VOL_TIERS)

        # R² and window size
        if tk in r2.index:
            row["r2"] = float(r2.loc[tk, "r2"])
            row["n_obs"] = int(r2.loc[tk, "n_obs"])
        else:
            row["r2"] = np.nan
            row["n_obs"] = 0

        rows.append(row)

    profile = pd.DataFrame(rows)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    profile.to_parquet(OUT_PATH, index=False, compression="snappy")

    print(f"\nWrote {len(profile)} rows × {len(profile.columns)} cols → {OUT_PATH}")
    print(f"As of date: {as_of.date()}, regime: {args.regime}")

    # Cohort distribution report
    print("\n=== Cohort tier distributions ===")
    for col in ["beta_mkt_tier", "beta_dgs10_tier", "beta_credit_tier",
                "beta_t10yie_tier", "dollar_tier", "oil_tier", "vol_tier"]:
        counts = profile[col].value_counts().to_dict()
        print(f"  {col:24s} {counts}")

    print("\n=== 'use' rates (the β is trustworthy as a sizing input) ===")
    for col in ["beta_mkt_use", "beta_dgs10_use", "beta_credit_use", "beta_t10yie_use"]:
        if col in profile.columns:
            n_use = int(profile[col].sum())
            print(f"  {col:24s} {n_use}/{len(profile)}  ({100*n_use/len(profile):.1f}%)")

    # Spotlight a few key names
    print("\n=== Spotlight: ZEBRA tier-1 + bank set ===")
    spotlight = ["SPY","QQQ","MSFT","NVDA","GOOGL","META","AMZN","JPM","BAC","WFC","XLU","GLD","TBT"]
    cols_show = ["ticker","beta_mkt","beta_mkt_tier","beta_dgs10","beta_dgs10_tier",
                 "beta_dgs10_use","dollar_tier","oil_tier"]
    cohort = profile[profile["ticker"].isin(spotlight)][cols_show]
    print(cohort.to_string(index=False))


if __name__ == "__main__":
    main()
