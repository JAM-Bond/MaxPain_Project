#!/usr/bin/env python3.11
"""
Beta stability validation — Phase 3 of macro-sensitivity profile.

Takes the 252d rolling betas (build_betas_rolling.py output) and slices them
into 5 Fed-funds-rate regime windows. For each (ticker × factor), reports
how the beta behaves across regimes — stable, magnitude-dependent, or
sign-flipping.

Answers the memo's critical question: "are betas stable across regimes or
do they flip?" Phase 5 sizing rules should NOT use current-regime β for
names tagged SIGN_FLIP — they need regime-conditional β.

Regime windows are pegged to FOMC decision dates (Fed funds rate state):
    PRE_HIKE_ZIRP  2014-01-02 → 2015-12-16   first-cycle ZIRP
    HIKE_2018     2015-12-17 → 2019-07-30   first hike cycle + pause
    ZIRP_COVID    2019-07-31 → 2022-03-16   2019 cuts + COVID ZIRP
    HIKE_2022     2022-03-17 → 2023-07-26   fastest hike cycle in history
    PLATEAU_CUTS  2023-07-27 → present      higher-for-longer + 2024 cuts

Outputs:
    data/macro/beta_regime_summary.parquet  (ticker × factor × regime grain)
        ticker | factor | regime | mean_beta | median_beta | std_beta |
        mean_t | n_dates | frac_significant

    data/macro/beta_stability_tags.parquet  (ticker × factor grain)
        ticker | factor | tag | regimes_seen | sign_flips |
        max_abs | min_abs | magnitude_ratio | mean_overall

Usage:
    python3.11 build_beta_stability.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BETA_PATH = ROOT / "data/macro/beta_rolling_252d.parquet"
OUT_SUMMARY = ROOT / "data/macro/beta_regime_summary.parquet"
OUT_TAGS = ROOT / "data/macro/beta_stability_tags.parquet"

# Regimes tagged at the END of each rolling window — i.e., the regime label
# applies to the date the β estimate is associated with, which represents
# the 252-day lookback ending on that date. So a β labeled HIKE_2022 was
# estimated using mostly HIKE_2022-period data.
REGIMES = [
    ("PRE_HIKE_ZIRP", "2014-01-02", "2015-12-16"),
    ("HIKE_2018",     "2015-12-17", "2019-07-30"),
    ("ZIRP_COVID",    "2019-07-31", "2022-03-16"),
    ("HIKE_2022",     "2022-03-17", "2023-07-26"),
    ("PLATEAU_CUTS",  "2023-07-27", "2099-12-31"),
]

MAGNITUDE_THRESHOLD = 3.0   # max(|β|) / min(|β|) — above this = MAGNITUDE_DEPENDENT
T_STAT_SIG = 2.0            # |t| > 2 = "significant" for the frac_significant column
MATERIAL_THRESHOLD = 0.05   # max(|β|) > this = "material" sensitivity; below = noise floor


def tag_regime(dates: pd.Series) -> pd.Series:
    out = pd.Series(index=dates.index, dtype="object")
    for name, start, end in REGIMES:
        mask = (dates >= start) & (dates <= end)
        out.loc[mask] = name
    return out


def classify(group: pd.DataFrame) -> pd.Series:
    """Given a sub-df (one ticker × one factor, one row per regime),
    return a single-row classification."""
    regimes_seen = len(group)
    if regimes_seen < 2:
        return pd.Series({
            "tag": "INSUFFICIENT_REGIMES",
            "regimes_seen": regimes_seen,
            "sign_flips": 0,
            "max_abs": np.nan,
            "min_abs": np.nan,
            "magnitude_ratio": np.nan,
            "mean_overall": group["mean_beta"].iloc[0] if regimes_seen else np.nan,
        })

    means = group["mean_beta"].values
    abs_means = np.abs(means)
    signs = np.sign(means)
    sign_flips = int((signs[1:] != signs[:-1]).sum())  # count of adjacent regime sign changes

    max_abs = abs_means.max()
    min_abs = abs_means.min()
    mag_ratio = max_abs / min_abs if min_abs > 0 else np.inf

    same_sign = (np.unique(signs[signs != 0]).size <= 1)

    if not same_sign:
        tag = "SIGN_FLIP"
    elif mag_ratio > MAGNITUDE_THRESHOLD:
        tag = "MAGNITUDE_DEPENDENT"
    else:
        tag = "STABLE"

    material = max_abs > MATERIAL_THRESHOLD

    return pd.Series({
        "tag": tag,
        "material": material,
        "regimes_seen": regimes_seen,
        "sign_flips": sign_flips,
        "max_abs": max_abs,
        "min_abs": min_abs,
        "magnitude_ratio": mag_ratio,
        "mean_overall": means.mean(),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beta", default=str(BETA_PATH))
    args = ap.parse_args()

    print(f"Loading {args.beta}...")
    b = pd.read_parquet(args.beta)
    print(f"  {b.shape}  tickers={b['ticker'].nunique()}  factors={b['factor'].nunique()}")

    b["regime"] = tag_regime(b["date"].dt.strftime("%Y-%m-%d"))
    pre_count = len(b)
    b = b[b["regime"].notna()].copy()
    print(f"  Tagged {len(b):,}/{pre_count:,} rows with a regime")
    print(f"  Per-regime row counts:\n{b.groupby('regime').size().to_string()}")

    # Per (ticker × factor × regime) aggregation
    print("\nAggregating per ticker × factor × regime...")
    agg = (b.groupby(["ticker", "factor", "regime"], observed=True)
           .agg(mean_beta=("beta", "mean"),
                median_beta=("beta", "median"),
                std_beta=("beta", "std"),
                mean_t=("t_stat", "mean"),
                n_dates=("beta", "size"),
                frac_significant=("t_stat", lambda x: float((x.abs() > T_STAT_SIG).mean())))
           .reset_index())
    print(f"  {agg.shape}")

    OUT_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    agg.to_parquet(OUT_SUMMARY, index=False, compression="snappy")
    print(f"  → {OUT_SUMMARY}")

    # Per (ticker × factor) classification
    print("\nClassifying per ticker × factor...")
    # Exclude 'alpha' from stability tagging — it's the regression intercept,
    # not a macro sensitivity. Keep it in the summary for completeness.
    tag_input = agg[agg["factor"] != "alpha"]
    tags = (tag_input.groupby(["ticker", "factor"], observed=True)
            .apply(classify, include_groups=False)
            .reset_index())
    print(f"  {tags.shape}")

    tags.to_parquet(OUT_TAGS, index=False, compression="snappy")
    print(f"  → {OUT_TAGS}")

    # Cohort report
    print("\n" + "=" * 70)
    print("COHORT STABILITY REPORT")
    print("=" * 70)

    print("\nTag distribution per factor (count of tickers):")
    pivot = (tags.groupby(["factor", "tag"], observed=True).size()
             .unstack(fill_value=0))
    pivot["TOTAL"] = pivot.sum(axis=1)
    print(pivot.to_string())

    print("\nMATERIAL (max|β|>0.05) tag distribution per factor:")
    mat_pivot = (tags[tags["material"]].groupby(["factor", "tag"], observed=True).size()
                 .unstack(fill_value=0))
    if not mat_pivot.empty:
        mat_pivot["TOTAL_MATERIAL"] = mat_pivot.sum(axis=1)
        print(mat_pivot.to_string())

    print("\nPer-factor % MATERIAL SIGN_FLIP (the names Phase 5 must NOT use current-regime β for):")
    mat_flip = tags[(tags["material"]) & (tags["tag"] == "SIGN_FLIP")].groupby("factor").size()
    total = tags.groupby("factor").size()
    pct = (mat_flip / total * 100).fillna(0).round(1).sort_values(ascending=False)
    print(pct.to_string())

    # The most actionable output: names where the most-traded factor (mkt_d1)
    # flips, plus names where rate β flips
    for fac in ["mkt_d1", "DGS10_d1", "VIXCLS_d1", "credit_d1"]:
        flips = tags[(tags["factor"] == fac) & (tags["tag"] == "SIGN_FLIP")]
        if len(flips) == 0:
            print(f"\n[{fac}] no SIGN_FLIP names")
            continue
        print(f"\n[{fac}] SIGN_FLIP names ({len(flips)}):")
        # Show their per-regime means
        for tk in flips["ticker"].tolist()[:15]:
            row = agg[(agg["ticker"] == tk) & (agg["factor"] == fac)]
            piv = row.set_index("regime")["mean_beta"].round(3)
            print(f"  {tk:6s}: " + "  ".join(f"{r}={v:+.3f}" for r, v in piv.items()))


if __name__ == "__main__":
    main()
