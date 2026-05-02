#!/usr/bin/env python3.11
"""
Bull-put moneyness — follow-ups (a) and (b).

(a) ATM-vs-OTM on the 72 BH-FDR winners under the LIVE bull_put gate
    (SPY VRP > 0 AND term-structure contango). Tests whether the
    universe-level ATM advantage holds when filtered to the same regime
    conditions the live trading plan requires.

(b) ATM-vs-ITM on cluster 2 (blue-chip premium-sellers) only.
    Cluster 2 had the strongest moneyness signal in the per-cluster
    cross-tab; this isolates the cohort that's already validated for
    bull_put trading.

Inputs:
  - data/profile/bull_put_moneyness_results.parquet (cycle-level)
  - data/profile/bull_put_moneyness_per_ticker_significance.parquet
  - data/profile/clusters_k8.parquet
  - data/profile/vrp_series.parquet (SPY VRP daily history)
  - data/orats/by_ticker/SPY.parquet (for term spread)

Outputs:
  - data/profile/bull_put_moneyness_followup_a_gated.parquet
  - data/profile/bull_put_moneyness_followup_b_cluster2.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT / "scripts/backtest"))
from signal_test_vrp_termstruct import build_term_structure  # noqa: E402

CYCLES = ROOT / "data/profile/bull_put_moneyness_results.parquet"
PER_TICKER_SIG = ROOT / "data/profile/bull_put_moneyness_per_ticker_significance.parquet"
CLUSTERS = ROOT / "data/profile/clusters_k8.parquet"
VRP_SERIES = ROOT / "data/profile/vrp_series.parquet"
SPY_RAW = ROOT / "data/orats/by_ticker/SPY.parquet"

OUT_A = ROOT / "data/profile/bull_put_moneyness_followup_a_gated.parquet"
OUT_B = ROOT / "data/profile/bull_put_moneyness_followup_b_cluster2.parquet"

EXIT_RULES = [("held_pnl", "held"), ("mgd50_pnl", "mgd50")]


def build_spy_regime() -> pd.DataFrame:
    """Daily SPY regime series with bull_put_gate column."""
    vrp = pd.read_parquet(VRP_SERIES)
    spy_vrp = vrp[vrp["ticker"] == "SPY"][["trade_date", "vrp"]].copy()
    spy_vrp["trade_date"] = pd.to_datetime(spy_vrp["trade_date"])

    spy_raw = pd.read_parquet(SPY_RAW, columns=["trade_date", "expirDate", "strike", "stkPx", "delta", "cMidIv", "pMidIv"])
    term = build_term_structure(spy_raw)
    term["trade_date"] = pd.to_datetime(term["trade_date"])

    out = spy_vrp.merge(term[["trade_date", "term_spread"]], on="trade_date", how="left")
    # Live gate: VRP > 0 AND term_spread > 0 (contango)
    out["bull_put_gate"] = (out["vrp"] > 0) & (out["term_spread"] > 0)
    return out


def wilcoxon_pair(df: pd.DataFrame, a: str, b: str, pnl_col: str) -> dict:
    wide = df.pivot_table(index=["ticker", "entry_date"], columns="moneyness",
                          values=pnl_col, aggfunc="first")
    if a not in wide.columns or b not in wide.columns:
        return {"n_paired": 0, "median_diff": np.nan, "p_value": np.nan, "winner": None}
    complete = wide.dropna(subset=[a, b])
    n = len(complete)
    if n < 30:
        return {"n_paired": n, "median_diff": np.nan, "p_value": np.nan, "winner": None}
    diff = (complete[a] - complete[b]).to_numpy()
    try:
        _, p = stats.wilcoxon(diff)
    except ValueError:
        p = np.nan
    med = float(np.median(diff))
    winner = a if med > 0 else b
    return {"n_paired": n, "median_diff": med, "p_value": float(p), "winner": winner}


def cohort_scorecard(df: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    for moneyness in ["OTM", "ATM", "ITM"]:
        sub = df[df["moneyness"] == moneyness]
        if sub.empty:
            continue
        for pnl_col, exit_label in EXIT_RULES:
            x = sub[pnl_col].to_numpy()
            win_col = "held_win" if exit_label == "held" else "mgd50_win"
            rows.append({
                "label": label,
                "moneyness": moneyness,
                "exit_rule": exit_label,
                "n": len(x),
                "mean": float(np.mean(x)),
                "median": float(np.median(x)),
                "win_rate": float(sub[win_col].mean()),
                "worst_5pct": float(np.quantile(x, 0.05)),
                "max_loss": float(np.min(x)),
            })
    return pd.DataFrame(rows)


def main():
    print("Loading cycle results...")
    cycles = pd.read_parquet(CYCLES)
    cycles["entry_date"] = pd.to_datetime(cycles["entry_date"])
    print(f"  {len(cycles):,} cycle rows")

    # ── Build SPY regime series ──
    print("Building SPY regime series (VRP + term structure)...")
    spy_regime = build_spy_regime()
    print(f"  {len(spy_regime):,} regime days, {spy_regime['bull_put_gate'].sum():,} with gate ON ({spy_regime['bull_put_gate'].mean()*100:.1f}%)")

    # Map entry_date to gate state (forward-fill: gate state of nearest prior day if exact missing)
    cycles_dated = cycles.merge(
        spy_regime[["trade_date", "bull_put_gate"]],
        left_on="entry_date", right_on="trade_date", how="left",
    )
    # If entry_date has no exact regime row (weekend?), fill from prior day
    if cycles_dated["bull_put_gate"].isna().any():
        spy_regime = spy_regime.sort_values("trade_date")
        cycles_dated["bull_put_gate"] = cycles_dated["bull_put_gate"].fillna(method="ffill")
    cycles_dated["bull_put_gate"] = cycles_dated["bull_put_gate"].fillna(False)
    print(f"  Cycles with gate ON: {cycles_dated['bull_put_gate'].sum():,} of {len(cycles_dated):,} ({cycles_dated['bull_put_gate'].mean()*100:.1f}%)")

    # ── Load 72 BH-FDR winners (mgd50, OTM vs ATM, winner=ATM) ──
    print("\nLoading per-ticker significance to identify 72 ATM-winners...")
    pt = pd.read_parquet(PER_TICKER_SIG)
    winners_72 = pt[
        (pt["exit_rule"] == "mgd50")
        & (pt["pair"] == "OTM vs ATM")
        & (pt["bh_fdr_significant"])
        & (pt["winner"] == "ATM")
    ]["ticker"].unique().tolist()
    print(f"  {len(winners_72)} winners: {sorted(winners_72)[:10]}...")

    # ── (a) Gated test on 72 winners ──
    print("\n" + "=" * 86)
    print("(a) ATM vs OTM on 72 winners — gated on SPY VRP>0 & term contango")
    print("=" * 86)

    gated_72 = cycles_dated[
        (cycles_dated["ticker"].isin(winners_72))
        & (cycles_dated["bull_put_gate"])
    ]
    ungated_72 = cycles_dated[cycles_dated["ticker"].isin(winners_72)]

    sc_gated = cohort_scorecard(gated_72, "gated_72")
    sc_ungated = cohort_scorecard(ungated_72, "ungated_72_baseline")
    sc_a = pd.concat([sc_ungated, sc_gated], ignore_index=True)
    sc_a.to_parquet(OUT_A, index=False)
    print(sc_a.to_string(index=False, float_format="%.3f"))
    print()
    for pnl_col, exit_label in EXIT_RULES:
        gated_w = wilcoxon_pair(gated_72, "ATM", "OTM", pnl_col)
        ungated_w = wilcoxon_pair(ungated_72, "ATM", "OTM", pnl_col)
        print(f"  Wilcoxon ATM vs OTM ({exit_label}):")
        print(f"    UNGATED:  n_paired={ungated_w['n_paired']:5d}  med_diff={ungated_w['median_diff']:+.3f}  p={ungated_w['p_value']:.4f}  winner={ungated_w['winner']}")
        print(f"    GATED:    n_paired={gated_w['n_paired']:5d}  med_diff={gated_w['median_diff']:+.3f}  p={gated_w['p_value']:.4f}  winner={gated_w['winner']}")

    # ── (b) Cluster 2 only ──
    print("\n" + "=" * 86)
    print("(b) ATM vs ITM on cluster 2 only (blue-chip premium-sellers)")
    print("=" * 86)
    clusters_df = pd.read_parquet(CLUSTERS)
    cluster_map = dict(zip(clusters_df["ticker"], clusters_df["cluster"]))
    cluster_2_tickers = [t for t in cycles_dated["ticker"].unique() if cluster_map.get(t) == 2]
    print(f"  {len(cluster_2_tickers)} cluster-2 tickers in cycle results: {sorted(cluster_2_tickers)[:10]}...")

    c2 = cycles_dated[cycles_dated["ticker"].isin(cluster_2_tickers)]
    c2_gated = c2[c2["bull_put_gate"]]

    sc_c2 = cohort_scorecard(c2, "cluster_2_ungated")
    sc_c2g = cohort_scorecard(c2_gated, "cluster_2_gated")
    sc_b = pd.concat([sc_c2, sc_c2g], ignore_index=True)
    sc_b.to_parquet(OUT_B, index=False)
    print(sc_b.to_string(index=False, float_format="%.3f"))
    print()
    for pnl_col, exit_label in EXIT_RULES:
        for a, b in [("OTM", "ATM"), ("OTM", "ITM"), ("ATM", "ITM")]:
            w_un = wilcoxon_pair(c2, a, b, pnl_col)
            w_g = wilcoxon_pair(c2_gated, a, b, pnl_col)
            print(f"  Wilcoxon {a} vs {b} ({exit_label}):")
            print(f"    cluster_2 UNGATED: n={w_un['n_paired']:5d}  med_diff={w_un['median_diff']:+.3f}  p={w_un['p_value']:.4f}  winner={w_un['winner']}")
            print(f"    cluster_2 GATED:   n={w_g['n_paired']:5d}  med_diff={w_g['median_diff']:+.3f}  p={w_g['p_value']:.4f}  winner={w_g['winner']}")
        print()


if __name__ == "__main__":
    main()
