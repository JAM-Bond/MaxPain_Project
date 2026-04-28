"""Regime-window cut of the inverted_fly wide-wings universe sweep.

Reads `data/backtest/results_wide_wings_universe_slip025.parquet` (produced by
wide_wings_sweep.py) and aggregates per (wing_pct × cluster) and (wing_pct × ticker)
inside the same pre-registered bear windows used in regime_cut.py.

Answers: does inverted_fly at wide wings actually become attractive in crash windows,
and which wing width + subgroup is the right cell?
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
RESULTS = ROOT / "data/backtest/results_wide_wings_universe_slip025.parquet"
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"
OUT_DIR = ROOT / "data/profile"

REGIMES = {
    "covid_2020":       ("2020-02-15", "2020-04-30"),
    "bear_2022":        ("2022-01-01", "2022-10-15"),
    "dec_2018":         ("2018-10-01", "2018-12-24"),
    "aug_2015":         ("2015-08-01", "2015-10-01"),
    "volmageddon_2018": ("2018-01-20", "2018-02-20"),
}


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    g = df.groupby(group_cols, observed=True)
    out = g["pnl"].agg(
        n="count",
        mean_pnl="mean",
        median_pnl="median",
        win_rate=lambda s: (s > 0).mean(),
        total_pnl="sum",
    ).reset_index()
    return out


def tag_regime(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["regime"] = "other"
    for name, (start, end) in REGIMES.items():
        mask = (df["entry_date"] >= start) & (df["entry_date"] <= end)
        df.loc[mask, "regime"] = name
    return df


def main() -> None:
    df = pd.read_parquet(RESULTS)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    # Skip entry_failed rows (they have NaN pnl and are not tradeable)
    df = df[df["exit_rule"] != "entry_failed"].copy()
    # Use 50_pct exit as the canonical managed-exit view
    df_mgd = df[df["exit_rule"] == "50_pct"].copy()

    universe = pd.read_parquet(UNIVERSE)
    df_mgd = df_mgd.merge(universe[["ticker", "cluster"]], on="ticker", how="left")

    baseline = summarize(df_mgd, ["wing_pct", "cluster"]).assign(regime="baseline")
    baseline_all = summarize(df_mgd, ["wing_pct"]).assign(regime="baseline")

    per_regime_cluster = [baseline]
    per_regime_wing = [baseline_all]
    per_regime_ticker: list[pd.DataFrame] = []

    for name, (start, end) in REGIMES.items():
        sub = df_mgd[(df_mgd["entry_date"] >= start) & (df_mgd["entry_date"] <= end)]
        if len(sub) == 0:
            continue
        per_regime_cluster.append(summarize(sub, ["wing_pct", "cluster"]).assign(regime=name))
        per_regime_wing.append(summarize(sub, ["wing_pct"]).assign(regime=name))
        per_regime_ticker.append(summarize(sub, ["wing_pct", "ticker"]).assign(regime=name))

    wing_tbl = pd.concat(per_regime_wing, ignore_index=True)
    cluster_tbl = pd.concat(per_regime_cluster, ignore_index=True)
    ticker_tbl = pd.concat(per_regime_ticker, ignore_index=True) if per_regime_ticker else pd.DataFrame()

    regimes_order = ["baseline"] + list(REGIMES.keys())

    print("=== Per-wing mean P&L (managed exit, slip=0.25) ===")
    print(wing_tbl.pivot_table(
        index="wing_pct", columns="regime", values="mean_pnl", aggfunc="first",
    )[[r for r in regimes_order if r in wing_tbl["regime"].unique()]].to_string(float_format=lambda x: f"{x:8.3f}"))

    print("\n=== Per-wing win rate ===")
    print(wing_tbl.pivot_table(
        index="wing_pct", columns="regime", values="win_rate", aggfunc="first",
    )[[r for r in regimes_order if r in wing_tbl["regime"].unique()]].to_string(float_format=lambda x: f"{x:8.3f}"))

    print("\n=== Per-wing cycle count ===")
    print(wing_tbl.pivot_table(
        index="wing_pct", columns="regime", values="n", aggfunc="first",
    )[[r for r in regimes_order if r in wing_tbl["regime"].unique()]].to_string(float_format=lambda x: f"{x:8.0f}"))

    print("\n=== Per wing × cluster mean P&L ===")
    print(cluster_tbl.pivot_table(
        index=["wing_pct", "cluster"], columns="regime", values="mean_pnl", aggfunc="first",
    )[[r for r in regimes_order if r in cluster_tbl["regime"].unique()]].to_string(float_format=lambda x: f"{x:8.3f}"))

    # Top tickers per regime at wing=0.10 (the middle that prior analysis suggested was the sweet spot)
    if not ticker_tbl.empty:
        sweet = ticker_tbl[ticker_tbl["wing_pct"] == 0.10].copy()
        if not sweet.empty:
            print("\n=== Top inverted_fly tickers by mean P&L at wing=10%, per regime (min N=3) ===")
            for regime in sweet["regime"].unique():
                sub = sweet[(sweet["regime"] == regime) & (sweet["n"] >= 3)].sort_values("mean_pnl", ascending=False)
                print(f"\n[{regime}] top 8:")
                print(sub.head(8)[["ticker", "n", "mean_pnl", "win_rate", "median_pnl"]].to_string(index=False, float_format=lambda x: f"{x:8.3f}"))

    wing_tbl.to_parquet(OUT_DIR / "regime_inverted_fly_wide_by_wing.parquet", index=False)
    cluster_tbl.to_parquet(OUT_DIR / "regime_inverted_fly_wide_by_wing_cluster.parquet", index=False)
    if not ticker_tbl.empty:
        ticker_tbl.to_parquet(OUT_DIR / "regime_inverted_fly_wide_by_wing_ticker.parquet", index=False)
    print("\nwrote:")
    for p in ["regime_inverted_fly_wide_by_wing.parquet",
              "regime_inverted_fly_wide_by_wing_cluster.parquet",
              "regime_inverted_fly_wide_by_wing_ticker.parquet"]:
        print(f"  data/profile/{p}")


if __name__ == "__main__":
    main()
