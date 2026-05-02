#!/usr/bin/env python3.11
"""
Bull-put moneyness — follow-up analyses #1 and #2.

#1 Per-cluster cross-tab: does ATM/ITM dominance vary by behavioral cluster?
   Joins clusters_k8.parquet onto cycle results and runs paired Wilcoxon
   per (cluster, exit_rule, pair).

#2 Per-ticker promotion: identifies tickers with a statistically significant
   moneyness preference (paired Wilcoxon per ticker, BH-FDR correction
   across the multi-test family). Outputs a candidate list per direction.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path.home() / "MaxPain_Project"
RESULTS = ROOT / "data/profile/bull_put_moneyness_results.parquet"
CLUSTERS = ROOT / "data/profile/clusters_k8.parquet"
PER_CLUSTER_OUT = ROOT / "data/profile/bull_put_moneyness_per_cluster.parquet"
PER_TICKER_SIG_OUT = ROOT / "data/profile/bull_put_moneyness_per_ticker_significance.parquet"

EXIT_RULES = [("held_pnl", "held"), ("mgd50_pnl", "mgd50")]
MONEYNESS_ORDER = ["OTM", "ATM", "ITM"]
PAIRS = [("OTM", "ATM"), ("OTM", "ITM"), ("ATM", "ITM")]
MIN_N_PAIRED = 30  # min same-cycle pairs to attempt a Wilcoxon


def benjamini_hochberg(pvals: np.ndarray, q: float = 0.05) -> np.ndarray:
    """Return boolean mask of p-values surviving BH-FDR at level q.
    NaN p-values pass through as False."""
    p = np.asarray(pvals, dtype=float)
    valid = ~np.isnan(p)
    if not valid.any():
        return np.zeros_like(p, dtype=bool)
    pv = p[valid]
    n = len(pv)
    order = np.argsort(pv)
    ranked = pv[order]
    thresholds = q * np.arange(1, n + 1) / n
    passed = ranked <= thresholds
    if not passed.any():
        return np.zeros_like(p, dtype=bool)
    cutoff_rank = np.where(passed)[0].max()
    cutoff_p = ranked[cutoff_rank]
    out = np.zeros_like(p, dtype=bool)
    out[valid] = pv <= cutoff_p
    return out


def per_cluster_cross_tab(df: pd.DataFrame, cluster_map: dict[str, int]) -> pd.DataFrame:
    df = df.copy()
    df["cluster"] = df["ticker"].map(cluster_map)
    df = df.dropna(subset=["cluster"])
    df["cluster"] = df["cluster"].astype(int)

    rows = []
    for cluster, csub in df.groupby("cluster"):
        n_tickers = csub["ticker"].nunique()
        for pnl_col, exit_label in EXIT_RULES:
            wide = csub.pivot_table(
                index=["ticker", "entry_date"], columns="moneyness",
                values=pnl_col, aggfunc="first",
            )
            complete = wide.dropna(subset=MONEYNESS_ORDER)
            n_pairs = len(complete)
            for a, b in PAIRS:
                if n_pairs < MIN_N_PAIRED:
                    p, w, med = np.nan, np.nan, np.nan
                else:
                    diff = (complete[a] - complete[b]).to_numpy()
                    try:
                        w, p = stats.wilcoxon(diff)
                    except ValueError:
                        w, p = np.nan, np.nan
                    med = float(np.median(diff))
                rows.append({
                    "cluster": cluster,
                    "n_tickers": n_tickers,
                    "exit_rule": exit_label,
                    "pair": f"{a} vs {b}",
                    "n_paired": n_pairs,
                    "median_diff": med,
                    "wilcoxon_stat": float(w) if not np.isnan(w) else np.nan,
                    "p_value": float(p) if not np.isnan(p) else np.nan,
                })
            # Also append per-cluster mean for each moneyness
            for m in MONEYNESS_ORDER:
                msub = csub[csub["moneyness"] == m]
                rows.append({
                    "cluster": cluster, "n_tickers": n_tickers,
                    "exit_rule": exit_label, "pair": f"mean_{m}",
                    "n_paired": len(msub),
                    "median_diff": float(msub[pnl_col].mean()) if len(msub) else np.nan,
                    "wilcoxon_stat": np.nan, "p_value": np.nan,
                })
    out = pd.DataFrame(rows)
    return out


def per_ticker_significance(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker, sub in df.groupby("ticker"):
        for pnl_col, exit_label in EXIT_RULES:
            wide = sub.pivot_table(
                index="entry_date", columns="moneyness", values=pnl_col, aggfunc="first",
            )
            # Skip tickers missing any moneyness column entirely
            present = [m for m in MONEYNESS_ORDER if m in wide.columns]
            if len(present) < 2:
                continue
            complete = wide.dropna(subset=present)
            n_pairs = len(complete)
            for a, b in PAIRS:
                if a not in wide.columns or b not in wide.columns:
                    continue
                if n_pairs < MIN_N_PAIRED:
                    p, med, mean_a, mean_b = np.nan, np.nan, np.nan, np.nan
                else:
                    diff = (complete[a] - complete[b]).to_numpy()
                    try:
                        _, p = stats.wilcoxon(diff)
                    except ValueError:
                        p = np.nan
                    med = float(np.median(diff))
                    mean_a = float(complete[a].mean())
                    mean_b = float(complete[b].mean())
                winner = a if not np.isnan(med) and med > 0 else (b if not np.isnan(med) and med < 0 else None)
                rows.append({
                    "ticker": ticker,
                    "exit_rule": exit_label,
                    "pair": f"{a} vs {b}",
                    "n_paired": n_pairs,
                    "mean_a": mean_a,
                    "mean_b": mean_b,
                    "median_diff": med,
                    "p_value": p,
                    "winner": winner,
                })
    out = pd.DataFrame(rows)
    # BH-FDR within each (exit_rule, pair) family — that's 6 families, ~162 tests each
    out["bh_fdr_significant"] = False
    for (exit_rule, pair), grp_idx in out.groupby(["exit_rule", "pair"]).groups.items():
        sub_p = out.loc[grp_idx, "p_value"].to_numpy()
        mask = benjamini_hochberg(sub_p, q=0.05)
        out.loc[grp_idx, "bh_fdr_significant"] = mask
    return out


def main():
    df = pd.read_parquet(RESULTS)
    clusters_df = pd.read_parquet(CLUSTERS)
    cluster_map = dict(zip(clusters_df["ticker"], clusters_df["cluster"]))
    n_with_cluster = df["ticker"].isin(cluster_map.keys()).sum()
    print(f"Cycle rows: {len(df):,} ({n_with_cluster:,} with cluster assigned)")
    print(f"Tickers in results: {df['ticker'].nunique()}")
    print(f"Tickers with cluster: {sum(1 for t in df['ticker'].unique() if t in cluster_map)}")
    print()

    # ── #1 Per-cluster cross-tab ──
    pc = per_cluster_cross_tab(df, cluster_map)
    PER_CLUSTER_OUT.parent.mkdir(parents=True, exist_ok=True)
    pc.to_parquet(PER_CLUSTER_OUT, index=False)
    print(f"Wrote {PER_CLUSTER_OUT}")

    # ── #2 Per-ticker significance ──
    pt = per_ticker_significance(df)
    pt.to_parquet(PER_TICKER_SIG_OUT, index=False)
    print(f"Wrote {PER_TICKER_SIG_OUT}")
    print()

    # ── PRINT: per-cluster summary ──
    print("=" * 90)
    print("PER-CLUSTER CROSS-TAB  (only mgd50 shown for brevity; held in parquet)")
    print("=" * 90)
    mgd = pc[pc["exit_rule"] == "mgd50"].copy()
    for cluster in sorted(mgd["cluster"].unique()):
        csub = mgd[mgd["cluster"] == cluster]
        if csub.empty:
            continue
        n_tickers = csub["n_tickers"].iloc[0]
        means = csub[csub["pair"].str.startswith("mean_")].set_index("pair")["median_diff"]
        print(f"\nCluster {cluster}  (n_tickers={n_tickers}):")
        for m in MONEYNESS_ORDER:
            mean_v = means.get(f"mean_{m}", np.nan)
            print(f"  {m} mean P/L: ${mean_v:+.3f}" if not np.isnan(mean_v) else f"  {m}: no data")
        wpairs = csub[~csub["pair"].str.startswith("mean_")]
        if not wpairs.empty:
            for _, r in wpairs.iterrows():
                p = r["p_value"]
                med = r["median_diff"]
                if pd.isna(p):
                    print(f"  {r['pair']:14s}: n={r['n_paired']:4d} (insufficient)")
                else:
                    sig = "**" if p < 0.05 else "  "
                    print(f"  {r['pair']:14s}: n_paired={r['n_paired']:4d}  median_diff={med:+.3f}  p={p:.4f} {sig}")

    # ── PRINT: per-ticker promotion ──
    print()
    print("=" * 90)
    print("PER-TICKER PROMOTION: tickers with statistically significant moneyness preference")
    print("(BH-FDR q=0.05 within each (exit_rule, pair) family)")
    print("=" * 90)
    sig = pt[pt["bh_fdr_significant"]].copy()
    print(f"\nTotal significant ticker × pair × exit combinations: {len(sig)}")
    for (pair, exit_rule), gsub in sig.groupby(["pair", "exit_rule"]):
        winners = gsub.groupby("winner").size().to_dict()
        winner_str = ", ".join(f"{k}: {v}" for k, v in sorted(winners.items()) if k)
        print(f"\n  [{exit_rule:6s}] {pair:14s} N_significant={len(gsub):3d}  winners → {winner_str}")
        # Show top 8 by absolute median_diff
        gsub = gsub.assign(abs_med=lambda x: x["median_diff"].abs()).sort_values("abs_med", ascending=False)
        for _, r in gsub.head(8).iterrows():
            print(f"     {r['ticker']:6s} winner={r['winner']:3s}  med_diff={r['median_diff']:+.3f}  "
                  f"mean_a={r['mean_a']:+.3f}  mean_b={r['mean_b']:+.3f}  p={r['p_value']:.4f}")


if __name__ == "__main__":
    main()
