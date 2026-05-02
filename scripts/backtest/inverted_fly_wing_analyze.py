#!/usr/bin/env python3.11
"""
IF wing-width: per-cell scorecard, walk-forward validation, gated vs ungated,
per-ticker recommendation lookup.

Mirrors the bull_put / bear_call moneyness pipeline.
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

CYCLES = ROOT / "data/profile/inverted_fly_wing_results.parquet"
SPY_RAW = ROOT / "data/orats/by_ticker/SPY.parquet"
SCORECARD_OUT = ROOT / "data/profile/inverted_fly_wing_scorecard.parquet"
WALKFORWARD_OUT = ROOT / "data/profile/inverted_fly_wing_walkforward.parquet"
RECOMMENDATION_OUT = ROOT / "data/profile/inverted_fly_wing_recommendation.parquet"

VARIANTS = ["narrow_2pct", "medium_5pct", "wide_10pct", "vwide_15pct"]
PAIRS = [("narrow_2pct", "medium_5pct"), ("narrow_2pct", "wide_10pct"),
         ("narrow_2pct", "vwide_15pct"), ("medium_5pct", "wide_10pct"),
         ("medium_5pct", "vwide_15pct"), ("wide_10pct", "vwide_15pct")]
ALPHA = 0.05
TRAIN_END_YEAR = 2022
MIN_N_TRAIN = 22
MIN_N_VAL = 12


def bootstrap_ci(x: np.ndarray, B: int = 1000, alpha: float = 0.05):
    if len(x) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(42)
    boots = np.empty(B)
    for i in range(B):
        boots[i] = rng.choice(x, size=len(x), replace=True).mean()
    return float(np.quantile(boots, alpha / 2)), float(np.quantile(boots, 1 - alpha / 2))


def per_cell_scorecard(df: pd.DataFrame, label: str = "all") -> pd.DataFrame:
    rows = []
    for variant in VARIANTS:
        sub = df[df["wing_variant"] == variant]
        if sub.empty:
            continue
        x = sub["mgd50_pnl"].to_numpy()
        ci_lo, ci_hi = bootstrap_ci(x)
        rows.append({
            "label": label, "variant": variant, "n": len(x),
            "mean": float(x.mean()), "median": float(np.median(x)),
            "win_rate": float(sub["mgd50_win"].mean()),
            "worst_5pct": float(np.quantile(x, 0.05)),
            "max_loss": float(x.min()),
            "ci_low": ci_lo, "ci_high": ci_hi,
        })
    return pd.DataFrame(rows)


def universe_wilcoxon(df: pd.DataFrame, label: str = "all") -> pd.DataFrame:
    wide = df.pivot_table(index=["ticker", "entry_date"], columns="wing_variant",
                          values="mgd50_pnl", aggfunc="first")
    rows = []
    for a, b in PAIRS:
        if a not in wide.columns or b not in wide.columns:
            continue
        complete = wide.dropna(subset=[a, b])
        if len(complete) < 30:
            continue
        diff = (complete[a] - complete[b]).to_numpy()
        try:
            _, p = stats.wilcoxon(diff)
        except ValueError:
            p = np.nan
        med = float(np.median(diff))
        winner = a if med > 0 else b
        rows.append({
            "label": label, "pair": f"{a} vs {b}",
            "n_paired": len(complete), "median_diff": med,
            "p_value": float(p) if not np.isnan(p) else np.nan,
            "winner": winner,
        })
    return pd.DataFrame(rows)


def per_ticker_walkforward(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    df["entry_year"] = df["entry_date"].dt.year

    rows = []
    for ticker, sub in df.groupby("ticker"):
        train = sub[sub["entry_year"] <= TRAIN_END_YEAR]
        val = sub[sub["entry_year"] > TRAIN_END_YEAR]
        train_wide = train.pivot_table(index="entry_date", columns="wing_variant",
                                        values="mgd50_pnl", aggfunc="first")
        val_wide = val.pivot_table(index="entry_date", columns="wing_variant",
                                    values="mgd50_pnl", aggfunc="first")
        for a, b in PAIRS:
            if a not in train_wide.columns or b not in train_wide.columns:
                continue
            t_complete = train_wide.dropna(subset=[a, b])
            v_complete = val_wide.dropna(subset=[a, b]) if a in val_wide.columns and b in val_wide.columns else pd.DataFrame()
            t_n, v_n = len(t_complete), len(v_complete)
            t_p, v_p, t_winner, v_winner = np.nan, np.nan, None, None
            t_mean_a, t_mean_b = np.nan, np.nan
            v_mean_a, v_mean_b = np.nan, np.nan
            if t_n >= 5:
                tdiff = (t_complete[a] - t_complete[b]).to_numpy()
                try:
                    _, t_p = stats.wilcoxon(tdiff)
                except ValueError:
                    t_p = np.nan
                t_med = float(np.median(tdiff))
                t_winner = a if t_med > 0 else b
                t_mean_a = float(t_complete[a].mean())
                t_mean_b = float(t_complete[b].mean())
            if v_n >= 5:
                vdiff = (v_complete[a] - v_complete[b]).to_numpy()
                try:
                    _, v_p = stats.wilcoxon(vdiff)
                except ValueError:
                    v_p = np.nan
                v_med = float(np.median(vdiff))
                v_winner = a if v_med > 0 else b
                v_mean_a = float(v_complete[a].mean())
                v_mean_b = float(v_complete[b].mean())
            same_dir = (t_winner is not None and v_winner is not None and t_winner == v_winner)
            train_pass = (t_n >= MIN_N_TRAIN and not np.isnan(t_p) and t_p < ALPHA)
            val_pass = (v_n >= MIN_N_VAL and not np.isnan(v_p) and v_p < ALPHA)
            promoted = train_pass and val_pass and same_dir
            rows.append({
                "ticker": ticker, "pair": f"{a} vs {b}",
                "train_n": t_n, "train_p": t_p, "train_winner": t_winner,
                "train_mean_a": t_mean_a, "train_mean_b": t_mean_b,
                "val_n": v_n, "val_p": v_p, "val_winner": v_winner,
                "val_mean_a": v_mean_a, "val_mean_b": v_mean_b,
                "promoted": promoted,
            })
    return pd.DataFrame(rows)


def build_recommendation_lookup(walkforward_df: pd.DataFrame) -> pd.DataFrame:
    """Per ticker, pick the wing variant with strongest evidence (smallest val_p
    among walk-forward-validated pairs where it wins)."""
    promoted = walkforward_df[walkforward_df["promoted"]].copy()
    if promoted.empty:
        return pd.DataFrame()
    rec_rows = []
    for ticker, sub in promoted.groupby("ticker"):
        # Score each variant by how often it wins promoted pairs (and avg p)
        winner_counts = sub["train_winner"].value_counts()
        best_winner = winner_counts.idxmax()
        best = sub[sub["train_winner"] == best_winner].sort_values("val_p").iloc[0]
        rec_rows.append({
            "ticker": ticker,
            "recommended_variant": best_winner,
            "evidence_pair": best["pair"],
            "train_p": best["train_p"], "val_p": best["val_p"],
            "train_n": int(best["train_n"]), "val_n": int(best["val_n"]),
        })
    return pd.DataFrame(rec_rows)


def main():
    df = pd.read_parquet(CYCLES)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    print(f"Loaded {len(df):,} cycles across {df['ticker'].nunique()} tickers")
    print(f"Per-variant counts: {df['wing_variant'].value_counts().to_dict()}")
    print()

    # ── Build term-spread series for IF gate ──
    print("Computing SPY term-spread for IF gate...")
    spy_raw = pd.read_parquet(SPY_RAW, columns=["trade_date", "expirDate", "strike", "stkPx", "delta", "cMidIv", "pMidIv"])
    term = build_term_structure(spy_raw)
    term["trade_date"] = pd.to_datetime(term["trade_date"])
    term["if_gate"] = term["term_spread"] > 0  # IF gate = term inverted

    # Map cycle entry_date → IF gate state
    df_dated = df.merge(term[["trade_date", "if_gate"]],
                         left_on="entry_date", right_on="trade_date", how="left")
    df_dated["if_gate"] = df_dated["if_gate"].fillna(False)
    print(f"  Cycles with IF gate ON: {df_dated['if_gate'].sum():,} of {len(df_dated):,} ({df_dated['if_gate'].mean()*100:.1f}%)")
    print()

    # ── Per-cell scorecard (universe-wide, gated, ungated) ──
    print("=" * 86)
    print("PER-CELL SCORECARD — universe-wide (50% managed exit)")
    print("=" * 86)
    sc_all = per_cell_scorecard(df_dated, "all")
    print(sc_all.to_string(index=False, float_format="%.3f"))
    print()
    sc_gated = per_cell_scorecard(df_dated[df_dated["if_gate"]], "if_gated")
    print("PER-CELL SCORECARD — IF gate ON (term-inverted)")
    print(sc_gated.to_string(index=False, float_format="%.3f"))
    print()

    # ── Universe-level Wilcoxon ──
    print("=" * 86)
    print("UNIVERSE WILCOXON (paired same-cycle diffs)")
    print("=" * 86)
    w_all = universe_wilcoxon(df_dated, "all")
    print(w_all.to_string(index=False, float_format="%.4f"))
    print()
    w_gated = universe_wilcoxon(df_dated[df_dated["if_gate"]], "if_gated")
    print("Same — IF GATE ON only:")
    print(w_gated.to_string(index=False, float_format="%.4f"))
    print()

    # Persist scorecard
    pd.concat([sc_all, sc_gated], ignore_index=True).to_parquet(SCORECARD_OUT, index=False)

    # ── Walk-forward per ticker ──
    print("=" * 86)
    print(f"WALK-FORWARD PER TICKER (train ≤{TRAIN_END_YEAR}, val 2023+, both p<{ALPHA}, same direction)")
    print("=" * 86)
    wf = per_ticker_walkforward(df_dated)
    wf.to_parquet(WALKFORWARD_OUT, index=False)
    promoted = wf[wf["promoted"]]
    print(f"Total promoted ticker × pair: {len(promoted)}")
    if not promoted.empty:
        winner_counts = promoted["train_winner"].value_counts()
        print(f"\nWinner distribution (across promoted ticker × pair cells):")
        print(winner_counts.to_string())
    print()

    # ── Per-ticker recommendation lookup ──
    rec = build_recommendation_lookup(wf)
    if not rec.empty:
        rec.to_parquet(RECOMMENDATION_OUT, index=False)
        print(f"Wrote {RECOMMENDATION_OUT}")
        print(f"\nPer-ticker recommendations: {len(rec)}")
        print(rec["recommended_variant"].value_counts().to_string())
        print()
        print("Top 12 by val_p:")
        for _, r in rec.sort_values("val_p").head(12).iterrows():
            print(f"  {r['ticker']:6s} → {r['recommended_variant']:12s} "
                  f"(via {r['evidence_pair']}, val_p={r['val_p']:.4f}, n={r['val_n']})")


if __name__ == "__main__":
    main()
