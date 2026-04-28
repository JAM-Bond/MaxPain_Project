"""Pairing analysis: bull_put + inverted_fly on cluster 2.

Hypothesis: bull_put cluster 2 (short-vol, bull-regime) and inverted_fly cluster 2
at 10% wings (long-vol, crash-regime) share a universe and have opposite regime
signatures. Running both simultaneously should collect theta in quiet markets
and flip convex in crashes, without needing a regime detector.

Does the pair actually cancel regime risk, or are there gaps where both bleed?

Join cycles by (ticker, expiration, entry_label) between:
  - results_slip025.parquet (bull_put, slip=0.25)
  - results_wide_wings_universe_slip025.parquet (inverted_fly, wing_pct=0.10, slip=0.25)

Both use the same exit-rule machinery so 50_pct managed view matches.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BP_PATH = ROOT / "data/backtest/results_slip025.parquet"
IF_PATH = ROOT / "data/backtest/results_wide_wings_universe_slip025.parquet"
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"
OUT_DIR = ROOT / "data/profile"

REGIMES = {
    "covid_2020":       ("2020-02-15", "2020-04-30"),
    "bear_2022":        ("2022-01-01", "2022-10-15"),
    "dec_2018":         ("2018-10-01", "2018-12-24"),
    "aug_2015":         ("2015-08-01", "2015-10-01"),
    "volmageddon_2018": ("2018-01-20", "2018-02-20"),
}


def tag_regime(ts: pd.Series) -> pd.Series:
    out = pd.Series(["quiet"] * len(ts), index=ts.index)
    for name, (start, end) in REGIMES.items():
        mask = (ts >= start) & (ts <= end)
        out.loc[mask] = name
    return out


def summarize(df: pd.DataFrame, value_col: str, group_cols: list[str]) -> pd.DataFrame:
    g = df.groupby(group_cols, observed=True)
    out = g[value_col].agg(
        n="count", mean="mean", median="median",
        win_rate=lambda s: (s > 0).mean(),
        total="sum", std="std", worst="min", best="max",
    ).reset_index()
    return out


def main() -> None:
    bp = pd.read_parquet(BP_PATH)
    inv = pd.read_parquet(IF_PATH)
    uni = pd.read_parquet(UNIVERSE)
    cluster2 = uni.loc[uni["cluster"] == 2, "ticker"].tolist()

    # Filter to cluster 2, managed exit (50_pct), bull_put / inverted_fly respectively
    bp_c2 = bp[(bp["ticker"].isin(cluster2)) & (bp["structure"] == "bull_put")
               & (bp["exit_rule"] == "50_pct")].copy()
    inv_c2 = inv[(inv["ticker"].isin(cluster2)) & (inv["wing_pct"] == 0.10)
                 & (inv["exit_rule"] == "50_pct")].copy()

    bp_c2 = bp_c2.rename(columns={"pnl": "pnl_bp"})[
        ["ticker", "expiration", "entry_label", "entry_date", "pnl_bp"]
    ]
    inv_c2 = inv_c2.rename(columns={"pnl": "pnl_if"})[
        ["ticker", "expiration", "entry_label", "pnl_if"]
    ]

    # Inner join on cycle keys
    pair = bp_c2.merge(inv_c2, on=["ticker", "expiration", "entry_label"], how="inner")
    pair = pair.dropna(subset=["pnl_bp", "pnl_if"])
    pair["pnl_combined"] = pair["pnl_bp"] + pair["pnl_if"]
    pair["entry_date"] = pd.to_datetime(pair["entry_date"])
    pair["regime"] = tag_regime(pair["entry_date"])

    print(f"Paired cycles (bull_put + inverted_fly cluster 2, managed exit, slip=0.25): {len(pair):,}")
    print(f"  entry_date range: {pair['entry_date'].min().date()} → {pair['entry_date'].max().date()}")
    print()

    # ── Per-regime summary of the three P&L paths ──
    regimes_order = ["quiet"] + list(REGIMES.keys())
    rows = []
    for regime in regimes_order:
        sub = pair[pair["regime"] == regime]
        if len(sub) == 0:
            continue
        rows.append({
            "regime": regime, "n": len(sub),
            "bp_mean": sub["pnl_bp"].mean(),
            "if_mean": sub["pnl_if"].mean(),
            "combined_mean": sub["pnl_combined"].mean(),
            "bp_win": (sub["pnl_bp"] > 0).mean(),
            "if_win": (sub["pnl_if"] > 0).mean(),
            "combined_win": (sub["pnl_combined"] > 0).mean(),
            "combined_median": sub["pnl_combined"].median(),
            "combined_worst": sub["pnl_combined"].min(),
            "combined_best": sub["pnl_combined"].max(),
        })
    report = pd.DataFrame(rows)
    pd.options.display.float_format = lambda x: f"{x:+.3f}" if isinstance(x, float) else str(x)
    print("=== Per-regime mean P&L ===")
    print(report[["regime", "n", "bp_mean", "if_mean", "combined_mean"]].to_string(
        index=False, float_format=lambda x: f"{x:+.3f}"))
    print()
    print("=== Per-regime win rate ===")
    print(report[["regime", "n", "bp_win", "if_win", "combined_win"]].to_string(
        index=False, float_format=lambda x: f"{x:.3f}"))
    print()
    print("=== Per-regime combined tail ===")
    print(report[["regime", "n", "combined_median", "combined_mean", "combined_worst", "combined_best"]].to_string(
        index=False, float_format=lambda x: f"{x:+.3f}"))
    print()

    # ── Full-sample combined stats ──
    total_mean = pair["pnl_combined"].mean()
    total_win = (pair["pnl_combined"] > 0).mean()
    total_std = pair["pnl_combined"].std()
    print("=== Full-sample combined stats ===")
    print(f"  N: {len(pair):,}")
    print(f"  mean: {total_mean:+.4f}")
    print(f"  median: {pair['pnl_combined'].median():+.4f}")
    print(f"  win rate: {total_win:.3f}")
    print(f"  std: {total_std:.3f}")
    print(f"  worst: {pair['pnl_combined'].min():+.3f}")
    print(f"  best: {pair['pnl_combined'].max():+.3f}")
    print()

    # ── Gap detection: cycles where BOTH legs lost ──
    both_lost = pair[(pair["pnl_bp"] < 0) & (pair["pnl_if"] < 0)]
    print(f"=== Gap detection: cycles where BOTH legs lost ===")
    print(f"  {len(both_lost):,} of {len(pair):,} ({len(both_lost)/len(pair):.1%}) — combined mean on these: ${both_lost['pnl_combined'].mean():+.3f}, worst ${both_lost['pnl_combined'].min():+.3f}")
    print()

    by_regime_both_lost = both_lost["regime"].value_counts().rename("both_lost_count")
    by_regime_total = pair["regime"].value_counts().rename("total_count")
    gap_tbl = pd.concat([by_regime_both_lost, by_regime_total], axis=1).fillna(0).astype(int)
    gap_tbl["both_lost_pct"] = (gap_tbl["both_lost_count"] / gap_tbl["total_count"]).round(3)
    print("=== Both-lost cycles by regime ===")
    print(gap_tbl.sort_values("both_lost_pct", ascending=False).to_string())
    print()

    # ── Per-ticker combined stats ──
    per_ticker = summarize(pair, "pnl_combined", ["ticker"]).sort_values("mean", ascending=False)
    print("=== Per-ticker combined stats (top 10 by mean) ===")
    print(per_ticker.head(10).to_string(index=False, float_format=lambda x: f"{x:+.3f}" if isinstance(x, float) else str(x)))
    print()
    print("=== Per-ticker combined stats (bottom 5 by mean) ===")
    print(per_ticker.tail(5).to_string(index=False, float_format=lambda x: f"{x:+.3f}" if isinstance(x, float) else str(x)))

    # Save artifacts
    pair.to_parquet(OUT_DIR / "pair_bp_if_cluster2_cycles.parquet", index=False)
    report.to_parquet(OUT_DIR / "pair_bp_if_cluster2_by_regime.parquet", index=False)
    per_ticker.to_parquet(OUT_DIR / "pair_bp_if_cluster2_by_ticker.parquet", index=False)
    print()
    print("wrote:")
    for p in ["pair_bp_if_cluster2_cycles.parquet",
              "pair_bp_if_cluster2_by_regime.parquet",
              "pair_bp_if_cluster2_by_ticker.parquet"]:
        print(f"  data/profile/{p}")


if __name__ == "__main__":
    main()
