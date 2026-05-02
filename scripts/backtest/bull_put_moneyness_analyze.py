#!/usr/bin/env python3.11
"""
Bull-put moneyness analysis — sealed test plan from BULL_PUT_MONEYNESS_PREREG.md.

Reads bull_put_moneyness_results.parquet, computes per-cell scorecard,
runs paired Wilcoxon tests on (OTM vs ATM, OTM vs ITM, ATM vs ITM) for
each exit rule with Bonferroni correction (α' = 0.0167), and writes
scorecard + per-ticker tables.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path.home() / "MaxPain_Project"
RESULTS = ROOT / "data/profile/bull_put_moneyness_results.parquet"
SCORECARD_OUT = ROOT / "data/profile/bull_put_moneyness_scorecard.parquet"
PER_TICKER_OUT = ROOT / "data/profile/bull_put_moneyness_per_ticker.parquet"

EXIT_RULES = [("held_pnl", "held_win", "held"), ("mgd50_pnl", "mgd50_win", "mgd50")]
MONEYNESS_ORDER = ["OTM", "ATM", "ITM"]
PAIRS = [("OTM", "ATM"), ("OTM", "ITM"), ("ATM", "ITM")]
ALPHA_RAW = 0.05
ALPHA_BONF = ALPHA_RAW / len(PAIRS)


def bootstrap_ci(x: np.ndarray, B: int = 1000, alpha: float = 0.05) -> tuple[float, float]:
    if len(x) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(42)
    boots = np.empty(B)
    for i in range(B):
        boots[i] = rng.choice(x, size=len(x), replace=True).mean()
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def per_cell_scorecard(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for moneyness in MONEYNESS_ORDER:
        sub = df[df["moneyness"] == moneyness]
        if sub.empty:
            continue
        for pnl_col, win_col, exit_label in EXIT_RULES:
            x = sub[pnl_col].to_numpy()
            ci_lo, ci_hi = bootstrap_ci(x)
            worst5 = np.quantile(x, 0.05) if len(x) else np.nan
            rows.append({
                "moneyness": moneyness,
                "exit_rule": exit_label,
                "n": len(x),
                "mean": float(np.mean(x)),
                "median": float(np.median(x)),
                "win_rate": float(sub[win_col].mean()),
                "worst_5pct": float(worst5),
                "max_loss": float(np.min(x)),
                "ci_low": ci_lo,
                "ci_high": ci_hi,
            })
    return pd.DataFrame(rows)


def paired_wilcoxon_per_pair(df: pd.DataFrame) -> pd.DataFrame:
    """For each exit rule, paired Wilcoxon on cycles where ALL THREE moneyness opened."""
    rows = []
    # Pivot: index = (ticker, entry_date), columns = moneyness
    for pnl_col, _, exit_label in EXIT_RULES:
        wide = df.pivot_table(
            index=["ticker", "entry_date"], columns="moneyness", values=pnl_col, aggfunc="first"
        )
        complete = wide.dropna(subset=MONEYNESS_ORDER)
        n_complete = len(complete)
        for a, b in PAIRS:
            if n_complete < 30:
                rows.append({
                    "exit_rule": exit_label, "pair": f"{a} vs {b}",
                    "n_paired": n_complete, "median_diff": np.nan,
                    "wilcoxon_stat": np.nan, "p_value": np.nan,
                    "significant_bonf": False,
                })
                continue
            diff = (complete[a] - complete[b]).to_numpy()
            try:
                w, p = stats.wilcoxon(diff)
            except ValueError:
                w, p = np.nan, np.nan
            rows.append({
                "exit_rule": exit_label,
                "pair": f"{a} vs {b}",
                "n_paired": n_complete,
                "median_diff": float(np.median(diff)),
                "wilcoxon_stat": float(w) if not np.isnan(w) else np.nan,
                "p_value": float(p) if not np.isnan(p) else np.nan,
                "significant_bonf": bool(p < ALPHA_BONF) if not np.isnan(p) else False,
            })
    return pd.DataFrame(rows)


def per_ticker_scorecard(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ticker, sub in df.groupby("ticker"):
        for pnl_col, win_col, exit_label in EXIT_RULES:
            cell = {"ticker": ticker, "exit_rule": exit_label}
            for moneyness in MONEYNESS_ORDER:
                m_sub = sub[sub["moneyness"] == moneyness]
                cell[f"{moneyness}_n"] = len(m_sub)
                cell[f"{moneyness}_mean"] = float(m_sub[pnl_col].mean()) if len(m_sub) else np.nan
                cell[f"{moneyness}_win"] = float(m_sub[win_col].mean()) if len(m_sub) else np.nan
            rows.append(cell)
    return pd.DataFrame(rows)


def main():
    if not RESULTS.exists():
        print(f"ERROR: missing {RESULTS}")
        sys.exit(1)
    df = pd.read_parquet(RESULTS)
    print(f"Loaded {len(df):,} cycle rows across {df['ticker'].nunique()} tickers")
    print(f"Per-moneyness counts: {df['moneyness'].value_counts().to_dict()}")
    print()

    scorecard = per_cell_scorecard(df)
    wilcoxon = paired_wilcoxon_per_pair(df)
    per_ticker = per_ticker_scorecard(df)

    SCORECARD_OUT.parent.mkdir(parents=True, exist_ok=True)
    scorecard.to_parquet(SCORECARD_OUT, index=False)
    per_ticker.to_parquet(PER_TICKER_OUT, index=False)
    print(f"Wrote {SCORECARD_OUT}")
    print(f"Wrote {PER_TICKER_OUT}")
    print()

    print("=" * 78)
    print("PER-CELL SCORECARD  (mean, median, win-rate, tail, bootstrap CI on mean)")
    print("=" * 78)
    print(scorecard.to_string(index=False, float_format="%.3f"))
    print()
    print("=" * 78)
    print(f"PAIRED WILCOXON  (Bonferroni α' = {ALPHA_BONF:.4f}, paired same-cycle diffs)")
    print("=" * 78)
    print(wilcoxon.to_string(index=False, float_format="%.4f"))
    print()
    print("=" * 78)
    print("PER-TICKER WINNER (held-to-expiry)")
    print("=" * 78)
    held = per_ticker[per_ticker["exit_rule"] == "held"]
    held_means = held.set_index("ticker")[[f"{m}_mean" for m in MONEYNESS_ORDER]]
    held_means.columns = MONEYNESS_ORDER
    winner = held_means.idxmax(axis=1)
    win_dist = winner.value_counts().reindex(MONEYNESS_ORDER, fill_value=0)
    print(f"Tickers where each moneyness wins by mean P/L (held-to-expiry):")
    for m in MONEYNESS_ORDER:
        print(f"  {m}: {win_dist[m]} tickers")
    print()
    print("PER-TICKER WINNER (50% managed)")
    mgd = per_ticker[per_ticker["exit_rule"] == "mgd50"]
    mgd_means = mgd.set_index("ticker")[[f"{m}_mean" for m in MONEYNESS_ORDER]]
    mgd_means.columns = MONEYNESS_ORDER
    winner_m = mgd_means.idxmax(axis=1)
    win_dist_m = winner_m.value_counts().reindex(MONEYNESS_ORDER, fill_value=0)
    print(f"Tickers where each moneyness wins by mean P/L (50% managed):")
    for m in MONEYNESS_ORDER:
        print(f"  {m}: {win_dist_m[m]} tickers")


if __name__ == "__main__":
    main()
