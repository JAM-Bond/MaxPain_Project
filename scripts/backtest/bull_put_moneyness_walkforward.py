#!/usr/bin/env python3.11
"""
Bull-put moneyness — walk-forward validation per ticker.

Splits each ticker's cycles into TRAIN (entry_year ≤ 2022) and VAL
(entry_year 2023+). Promotes a per-ticker moneyness recommendation only
if BOTH windows pass:
  - paired Wilcoxon p < 0.05 in TRAIN
  - paired Wilcoxon p < 0.05 in VAL
  - same direction (winner same in both windows)
  - N_paired ≥ 22 in TRAIN (matches original ZEBRA pre-reg floor)
  - N_paired ≥ 12 in VAL (~1 year of cycles)

Output: data/profile/bull_put_moneyness_walkforward.parquet
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path.home() / "MaxPain_Project"
CYCLES = ROOT / "data/profile/bull_put_moneyness_results.parquet"
OUT = ROOT / "data/profile/bull_put_moneyness_walkforward.parquet"

EXIT_RULES = [("held_pnl", "held"), ("mgd50_pnl", "mgd50")]
PAIRS = [("OTM", "ATM"), ("OTM", "ITM"), ("ATM", "ITM")]
TRAIN_END_YEAR = 2022
MIN_N_TRAIN = 22
MIN_N_VAL = 12
ALPHA = 0.05


def wilcoxon_pair_window(wide: pd.DataFrame, a: str, b: str):
    if a not in wide.columns or b not in wide.columns:
        return {"n": 0, "med": np.nan, "p": np.nan, "winner": None, "mean_a": np.nan, "mean_b": np.nan}
    complete = wide.dropna(subset=[a, b])
    n = len(complete)
    if n < 5:
        return {"n": n, "med": np.nan, "p": np.nan, "winner": None, "mean_a": np.nan, "mean_b": np.nan}
    diff = (complete[a] - complete[b]).to_numpy()
    try:
        _, p = stats.wilcoxon(diff)
    except ValueError:
        p = np.nan
    med = float(np.median(diff))
    winner = a if med > 0 else (b if med < 0 else None)
    return {
        "n": n, "med": med, "p": float(p) if not np.isnan(p) else np.nan,
        "winner": winner, "mean_a": float(complete[a].mean()),
        "mean_b": float(complete[b].mean()),
    }


def main():
    df = pd.read_parquet(CYCLES)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["entry_year"] = df["entry_date"].dt.year
    print(f"Loaded {len(df):,} cycles across {df['ticker'].nunique()} tickers")
    print(f"Entry year range: {df['entry_year'].min()} → {df['entry_year'].max()}")

    rows = []
    for ticker, sub in df.groupby("ticker"):
        train = sub[sub["entry_year"] <= TRAIN_END_YEAR]
        val = sub[sub["entry_year"] > TRAIN_END_YEAR]
        for pnl_col, exit_label in EXIT_RULES:
            train_wide = train.pivot_table(index="entry_date", columns="moneyness",
                                            values=pnl_col, aggfunc="first")
            val_wide = val.pivot_table(index="entry_date", columns="moneyness",
                                        values=pnl_col, aggfunc="first")
            for a, b in PAIRS:
                t = wilcoxon_pair_window(train_wide, a, b)
                v = wilcoxon_pair_window(val_wide, a, b)
                # Promotion criteria
                same_dir = (t["winner"] is not None and v["winner"] is not None
                            and t["winner"] == v["winner"])
                train_pass = (t["n"] >= MIN_N_TRAIN and not np.isnan(t["p"])
                              and t["p"] < ALPHA)
                val_pass = (v["n"] >= MIN_N_VAL and not np.isnan(v["p"])
                            and v["p"] < ALPHA)
                promoted = train_pass and val_pass and same_dir
                rows.append({
                    "ticker": ticker, "exit_rule": exit_label, "pair": f"{a} vs {b}",
                    "train_n": t["n"], "train_p": t["p"], "train_winner": t["winner"],
                    "train_mean_a": t["mean_a"], "train_mean_b": t["mean_b"],
                    "val_n": v["n"], "val_p": v["p"], "val_winner": v["winner"],
                    "val_mean_a": v["mean_a"], "val_mean_b": v["mean_b"],
                    "same_direction": same_dir, "train_pass": train_pass,
                    "val_pass": val_pass, "promoted": promoted,
                })
    out = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)
    print(f"Wrote {OUT}")
    print()

    # ── Summary ──
    promoted = out[out["promoted"]]
    print("=" * 86)
    print(f"WALK-FORWARD VALIDATED PROMOTIONS (train ≤{TRAIN_END_YEAR}, val ≥{TRAIN_END_YEAR + 1}, both p<{ALPHA}, same direction)")
    print("=" * 86)
    print(f"\nTotal promoted ticker × pair × exit: {len(promoted)}")
    for (exit_rule, pair), gsub in promoted.groupby(["exit_rule", "pair"]):
        winners = gsub["train_winner"].value_counts().to_dict()
        winner_str = ", ".join(f"{k}: {v}" for k, v in sorted(winners.items()))
        print(f"\n  [{exit_rule:6s}] {pair:14s} N_promoted={len(gsub):3d}  winners → {winner_str}")
        for _, r in gsub.head(8).iterrows():
            print(f"     {r['ticker']:6s} winner={r['train_winner']:3s}  train_p={r['train_p']:.4f} (n={r['train_n']:3d})  "
                  f"val_p={r['val_p']:.4f} (n={r['val_n']:3d})")

    # ── Build per-ticker recommendation lookup (for downstream wiring) ──
    # Logic: for each ticker, prefer the PAIR with strongest evidence (smallest val_p)
    # restricted to the pair's winner. Fall back to OTM (current spec) if nothing promoted.
    rec_rows = []
    for ticker, sub in promoted.groupby("ticker"):
        for exit_rule, sub2 in sub.groupby("exit_rule"):
            if sub2.empty:
                continue
            # Pick the pair with smallest val_p
            best = sub2.loc[sub2["val_p"].idxmin()]
            rec_rows.append({
                "ticker": ticker,
                "exit_rule": exit_rule,
                "recommended_moneyness": best["train_winner"],
                "evidence_pair": best["pair"],
                "train_p": best["train_p"], "val_p": best["val_p"],
                "train_n": best["train_n"], "val_n": best["val_n"],
            })
    rec = pd.DataFrame(rec_rows)
    rec_out = ROOT / "data/profile/bull_put_moneyness_recommendation.parquet"
    rec.to_parquet(rec_out, index=False)
    print(f"\n\nWrote per-ticker recommendation lookup: {rec_out}")
    print(f"  {len(rec)} (ticker, exit_rule) → recommended moneyness")
    if len(rec):
        print(f"  Distribution by exit_rule x moneyness:")
        print(rec.groupby(["exit_rule", "recommended_moneyness"]).size().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    main()
