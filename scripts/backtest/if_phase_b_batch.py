"""
Inverted_fly Phase B — doable items on existing data (2026-04-24).

B1. T-5 near-OpEx entry test for inverted_fly at 10% wings
B3. Bull_put + inverted_fly pair recomputed with 50%-only IF exit

Deferred (require new infrastructure, not tested here):
B2. Rolling — undefined for long-vol without a motivating trigger rule
B4. Stop-loss — needs daily marks not in held-to-rule data
B5. Universe expansion — needs ORATS pull for new tickers
"""

from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path("/Users/josephmorris/MaxPain_Project")
IF_DATA = ROOT / "data/backtest/results_wide_wings_universe_slip025.parquet"
BP_DATA = ROOT / "data/backtest/results_slip025.parquet"
SIGNAL = ROOT / "data/profile/signal_vrp_termstruct_spy.parquet"
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"

# Core names for pair analysis (revised: DROP DIA per Phase A)
CORE_IF_PAIR = ["SPX", "SPY", "QQQ", "TSLA", "META"]
# Cluster-2 scope for full universe pair
CLUSTER_2_ONLY = True

REGIMES = {
    "covid_2020":       ("2020-02-15", "2020-04-30"),
    "bear_2022":        ("2022-01-01", "2022-10-15"),
    "dec_2018":         ("2018-10-01", "2018-12-24"),
    "aug_2015":         ("2015-08-01", "2015-10-01"),
    "volmageddon_2018": ("2018-01-20", "2018-02-20"),
}


def stats(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) == 0:
        return {"N": 0, "mean": np.nan, "median": np.nan,
                "win_rate": np.nan, "total": np.nan, "worst": np.nan, "best": np.nan}
    return {
        "N": len(s),
        "mean": round(s.mean(), 4),
        "median": round(s.median(), 4),
        "win_rate": round((s > 0).mean(), 3),
        "total": round(s.sum(), 2),
        "worst": round(s.min(), 2),
        "best": round(s.max(), 2),
    }


def compute_exit_variants(df: pd.DataFrame) -> pd.DataFrame:
    """For each (ticker, expiration, entry_date), compute pnl_50pct, pnl_21dte, pnl_managed."""
    df = df[df["exit_rule"].isin(["50_pct", "dte_21"])].copy()
    grouped = df.sort_values("exit_date").groupby(
        ["ticker", "expiration", "entry_date"], as_index=False)
    managed = grouped.first()
    pnl_50 = df[df["exit_rule"] == "50_pct"].set_index(
        ["ticker", "expiration", "entry_date"])["pnl"]
    pnl_21 = df[df["exit_rule"] == "dte_21"].set_index(
        ["ticker", "expiration", "entry_date"])["pnl"]
    managed["pnl_managed"] = managed["pnl"]
    idx = managed.set_index(["ticker", "expiration", "entry_date"]).index
    managed["pnl_50pct"] = idx.map(pnl_50)
    managed["pnl_21dte"] = idx.map(pnl_21)
    return managed


def main() -> None:
    print("=" * 72)
    print("B1 — T-5 near-OpEx entry test for inverted_fly at 10% wings")
    print("=" * 72)

    if_raw = pd.read_parquet(IF_DATA)
    if_10 = if_raw[if_raw["wing_pct"] == 0.10].copy()

    # Two entry labels side by side
    for entry_label in ["dte_45", "near_opex"]:
        sub = if_10[if_10["entry_label"] == entry_label]
        exits = compute_exit_variants(sub)
        print(f"\n-- entry={entry_label} --")
        for label, col in [("50%-only", "pnl_50pct"),
                            ("21-DTE only", "pnl_21dte"),
                            ("managed (first-trigger)", "pnl_managed")]:
            s = stats(exits[col])
            print(f"  {label:26s} N={s['N']:5d}  mean={s['mean']:+.4f}  "
                  f"median={s['median']:+.4f}  win={s['win_rate']:.3f}  "
                  f"worst={s['worst']:+.2f}")

    # Compare with term-inversion gate on near_opex 50%-only
    print("\n-- B1 Signal gate (term inverted) on near_opex 50%-only --")
    near = compute_exit_variants(if_10[if_10["entry_label"] == "near_opex"])
    sig = pd.read_parquet(SIGNAL).rename(columns={"trade_date": "entry_date"})[
        ["entry_date", "term_spread", "vrp", "iv_rank"]]
    near_sig = near.merge(sig, on="entry_date", how="left").dropna(
        subset=["term_spread", "vrp"])

    filters = {
        "Baseline near_opex 50%-only": pd.Series(True, index=near_sig.index),
        "Term inverted (spread > 0)": near_sig["term_spread"] > 0,
        "Term inv AND VRP<0": (near_sig["term_spread"] > 0) & (near_sig["vrp"] < 0),
        "Term inv AND IVR>0.5": (near_sig["term_spread"] > 0) & (near_sig["iv_rank"] > 0.5),
    }
    for name, mask in filters.items():
        s = stats(near_sig.loc[mask, "pnl_50pct"])
        print(f"  {name:38s} N={s['N']:5d}  mean={s['mean']:+.4f}  "
              f"win={s['win_rate']:.3f}  worst={s['worst']:+.2f}")

    print("\n" + "=" * 72)
    print("B3 — Bull_put + inverted_fly pair recomputed with 50%-only IF exit")
    print("=" * 72)

    # Load bull_put managed dte_45 cycles
    bp = pd.read_parquet(BP_DATA)
    bp = bp[(bp["structure"] == "bull_put") & (bp["entry_label"] == "dte_45")]
    bp = bp[bp["exit_rule"].isin(["50_pct", "dte_21"])]
    bp_managed = bp.sort_values("exit_date").groupby(
        ["ticker", "expiration", "entry_date"], as_index=False).first()
    bp_managed = bp_managed.rename(columns={"pnl": "pnl_bp"})[
        ["ticker", "expiration", "entry_date", "pnl_bp"]]

    # Load inverted_fly 10%-wing dte_45 50%-only cycles
    if_dte45 = if_10[(if_10["entry_label"] == "dte_45") &
                     (if_10["exit_rule"] == "50_pct")]
    if_slice = if_dte45[["ticker", "expiration", "entry_date", "pnl"]].rename(
        columns={"pnl": "pnl_if"})

    pair = bp_managed.merge(if_slice, on=["ticker", "expiration", "entry_date"], how="inner")
    pair["pnl_combined"] = pair["pnl_bp"] + pair["pnl_if"]
    pair["entry_date"] = pd.to_datetime(pair["entry_date"])

    # Join cluster
    uni = pd.read_parquet(UNIVERSE)[["ticker", "cluster"]]
    pair = pair.merge(uni, on="ticker", how="left")

    # Cluster 2 subset (matches prior pair memo scope)
    if CLUSTER_2_ONLY:
        c2 = pair[pair["cluster"] == 2].copy()
        print(f"\nCluster 2 pair cycles: {len(c2)}")
    else:
        c2 = pair

    # Overall combined stats
    print("\n-- B3 Cluster 2 full-sample combined --")
    print(f"  bull_put leg alone: {stats(c2['pnl_bp'])}")
    print(f"  inverted_fly leg alone (50%-only): {stats(c2['pnl_if'])}")
    print(f"  combined: {stats(c2['pnl_combined'])}")

    # Regime decomposition
    print("\n-- B3 Cluster 2 combined by regime --")
    regime_rows = [{"regime": "baseline (full)", **stats(c2["pnl_combined"]),
                    "bp_mean": round(c2["pnl_bp"].mean(), 4),
                    "if_mean": round(c2["pnl_if"].mean(), 4)}]
    for name, (start, end) in REGIMES.items():
        mask = (c2["entry_date"] >= start) & (c2["entry_date"] <= end)
        sub = c2[mask]
        regime_rows.append({
            "regime": name,
            **stats(sub["pnl_combined"]),
            "bp_mean": round(sub["pnl_bp"].mean(), 4) if len(sub) else np.nan,
            "if_mean": round(sub["pnl_if"].mean(), 4) if len(sub) else np.nan,
        })
    regime_df = pd.DataFrame(regime_rows)
    print(regime_df[["regime", "N", "mean", "median", "win_rate",
                      "worst", "best", "bp_mean", "if_mean"]].to_string(index=False))

    # Per-ticker combined
    print("\n-- B3 Per-ticker combined P&L (cluster 2, full sample, min N=30) --")
    per_ticker = []
    for tkr, g in c2.groupby("ticker"):
        if len(g) < 30:
            continue
        per_ticker.append({
            "ticker": tkr,
            "N": len(g),
            "bp_mean": round(g["pnl_bp"].mean(), 4),
            "if_mean": round(g["pnl_if"].mean(), 4),
            "combined_mean": round(g["pnl_combined"].mean(), 4),
            "combined_total": round(g["pnl_combined"].sum(), 2),
            "win": round((g["pnl_combined"] > 0).mean(), 3),
            "worst": round(g["pnl_combined"].min(), 2),
        })
    per_ticker_df = pd.DataFrame(per_ticker).sort_values(
        "combined_mean", ascending=False)
    print(per_ticker_df.head(15).to_string(index=False))
    print(f"\nBottom per-ticker:")
    print(per_ticker_df.tail(5).to_string(index=False))
    print(f"\nPositive per-ticker: {(per_ticker_df['combined_mean'] > 0).sum()}/"
          f"{len(per_ticker_df)}")

    # Core shortlist (revised, no DIA)
    core_in_pair = [t for t in CORE_IF_PAIR if t in per_ticker_df["ticker"].values]
    core_sub = c2[c2["ticker"].isin(core_in_pair)]
    print(f"\n-- B3 Revised core list (no DIA): {core_in_pair} --")
    print(f"  N={len(core_sub)}")
    print(f"  combined: {stats(core_sub['pnl_combined'])}")

    # Compare with term-inv filter
    core_sig = core_sub.merge(sig, on="entry_date", how="left")
    term_inv = core_sig["term_spread"] > 0
    print("\n-- B3 Core combined with term-inversion IF-entry filter --")
    print(f"  all (no filter): combined={stats(core_sig['pnl_combined'])}")
    # Hypothetical: use term-inv to SIZE UP the IF leg (entry gate for IF, BP always entered)
    # For comparison just show splits
    print(f"  term-inverted days: combined={stats(core_sig.loc[term_inv, 'pnl_combined'])}")
    print(f"  contango days: combined={stats(core_sig.loc[~term_inv, 'pnl_combined'])}")

    # Save
    out_dir = ROOT / "data/profile"
    regime_df.to_parquet(out_dir / "if_phase_b_pair_regime.parquet", index=False)
    per_ticker_df.to_parquet(out_dir / "if_phase_b_pair_per_ticker.parquet", index=False)
    print(f"\nOutputs saved to data/profile/if_phase_b_*.parquet")


if __name__ == "__main__":
    main()
