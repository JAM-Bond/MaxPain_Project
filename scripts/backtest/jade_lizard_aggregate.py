"""Aggregate jade_lizard_raw.parquet into the scorecard required by the
pre-registration. For each (ticker, exit_rule, slip):

  - jade_lizard mean P&L, win rate, etc.
  - matched BP+BC baseline (sum of bull_put + bear_call P&L on the same
    (ticker, expiration, slip) cells)
  - matched bull_put-alone baseline
  - lift_vs_BP_BC = jade − BP+BC
  - lift_vs_BP_alone = jade − BP
  - Welch t-stat / p-value vs BP+BC baseline

Output: data/profile/jade_lizard_scorecard.parquet
"""
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path("/Users/josephmorris/MaxPain_Project")
RAW = ROOT / "data/backtest/jade_lizard_raw.parquet"
COHORT_OUT = ROOT / "data/profile/jade_lizard_cohort_scorecard.parquet"
PERTKR_OUT = ROOT / "data/profile/jade_lizard_per_ticker_scorecard.parquet"


def stats_for(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) == 0:
        return {"N": 0, "mean": np.nan, "median": np.nan, "win_rate": np.nan,
                "worst": np.nan, "best": np.nan}
    return {
        "N": int(len(s)),
        "mean": round(float(s.mean()), 4),
        "median": round(float(s.median()), 4),
        "win_rate": round(float((s > 0).mean()), 3),
        "worst": round(float(s.min()), 4),
        "best": round(float(s.max()), 4),
    }


def main() -> None:
    df = pd.read_parquet(RAW)
    print(f"Loaded {len(df):,} raw rows; {df['ticker'].nunique()} tickers")

    # Pivot to wide: per (ticker, expiration, slip), rows for jade/bp/bc
    keys = ["ticker", "expiration", "entry_date", "slip"]
    wide = df.pivot_table(
        index=keys, columns="structure",
        values=["pnl_exp", "pnl_mgd", "entry_credit", "max_profit"],
        aggfunc="first",
    )
    wide.columns = [f"{a}_{b}" for a, b in wide.columns.to_flat_index()]
    wide = wide.reset_index()

    # Compute combined BP+BC P&L (only on cycles where both legs opened AND
    # jade also opened — strict matched comparison)
    matched = wide.dropna(subset=["pnl_exp_jade_lizard",
                                   "pnl_exp_bull_put",
                                   "pnl_exp_bear_call"]).copy()
    matched["bp_bc_exp"] = matched["pnl_exp_bull_put"] + matched["pnl_exp_bear_call"]
    matched["bp_bc_mgd"] = matched["pnl_mgd_bull_put"] + matched["pnl_mgd_bear_call"]

    print(f"Matched cycles (jade fired AND BP+BC opened): {len(matched):,}")
    print(f"  by slip: {matched['slip'].value_counts().to_dict()}")

    # ─── Cohort-level scorecard ────────────────────────────────
    cohort_rows = []
    for slip in [0.25, 0.50]:
        for exit_label, jade_col, bp_bc_col, bp_col in [
            ("exp", "pnl_exp_jade_lizard", "bp_bc_exp", "pnl_exp_bull_put"),
            ("mgd", "pnl_mgd_jade_lizard", "bp_bc_mgd", "pnl_mgd_bull_put"),
        ]:
            sub = matched[matched["slip"] == slip]
            jade = sub[jade_col].dropna()
            bp_bc = sub[bp_bc_col].dropna()
            bp = sub[bp_col].dropna()
            j = stats_for(jade)
            b = stats_for(bp_bc)
            p = stats_for(bp)
            if len(jade) >= 10 and len(bp_bc) >= 10:
                t_stat, p_val = stats.ttest_ind(jade, bp_bc, equal_var=False)
            else:
                t_stat, p_val = np.nan, np.nan
            cohort_rows.append({
                "slip": slip, "exit_rule": exit_label,
                "N_jade": j["N"], "mean_jade": j["mean"], "win_jade": j["win_rate"],
                "worst_jade": j["worst"], "best_jade": j["best"],
                "N_bpbc": b["N"], "mean_bpbc": b["mean"], "win_bpbc": b["win_rate"],
                "lift_vs_bpbc": round(j["mean"] - b["mean"], 4)
                    if pd.notna(j["mean"]) and pd.notna(b["mean"]) else np.nan,
                "mean_bp_alone": p["mean"],
                "lift_vs_bp_alone": round(j["mean"] - p["mean"], 4)
                    if pd.notna(j["mean"]) and pd.notna(p["mean"]) else np.nan,
                "t_stat": round(float(t_stat), 3) if pd.notna(t_stat) else np.nan,
                "p_value": round(float(p_val), 4) if pd.notna(p_val) else np.nan,
            })
    cohort_df = pd.DataFrame(cohort_rows)

    # ─── Per-ticker scorecard ──────────────────────────────────
    pertkr_rows = []
    for (tkr, slip), sub in matched.groupby(["ticker", "slip"]):
        for exit_label, jade_col, bp_bc_col, bp_col in [
            ("exp", "pnl_exp_jade_lizard", "bp_bc_exp", "pnl_exp_bull_put"),
            ("mgd", "pnl_mgd_jade_lizard", "bp_bc_mgd", "pnl_mgd_bull_put"),
        ]:
            jade = sub[jade_col].dropna()
            bp_bc = sub[bp_bc_col].dropna()
            bp = sub[bp_col].dropna()
            j = stats_for(jade); b = stats_for(bp_bc); p = stats_for(bp)
            pertkr_rows.append({
                "ticker": tkr, "slip": slip, "exit_rule": exit_label,
                "N_jade": j["N"], "mean_jade": j["mean"], "win_jade": j["win_rate"],
                "worst_jade": j["worst"],
                "mean_bpbc": b["mean"],
                "lift_vs_bpbc": round(j["mean"] - b["mean"], 4)
                    if pd.notna(j["mean"]) and pd.notna(b["mean"]) else np.nan,
                "mean_bp_alone": p["mean"],
                "lift_vs_bp_alone": round(j["mean"] - p["mean"], 4)
                    if pd.notna(j["mean"]) and pd.notna(p["mean"]) else np.nan,
            })
    pertkr_df = pd.DataFrame(pertkr_rows)

    cohort_df.to_parquet(COHORT_OUT, index=False)
    pertkr_df.to_parquet(PERTKR_OUT, index=False)
    print(f"\nWrote cohort: {COHORT_OUT}")
    print(f"Wrote per-ticker: {PERTKR_OUT}")

    # ─── Display ───────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("COHORT-LEVEL RESULTS")
    print("=" * 100)
    print(cohort_df.to_string(index=False))

    print("\n" + "=" * 100)
    print("PROMOTION CHECK — slip=0.50, both exit rules")
    print("=" * 100)
    for exit_label in ["exp", "mgd"]:
        cell = cohort_df[(cohort_df["slip"] == 0.50) &
                          (cohort_df["exit_rule"] == exit_label)].iloc[0]
        print(f"\nslip=0.50 {exit_label}:")
        print(f"  N_jade:         {cell['N_jade']}")
        print(f"  mean_jade:      ${cell['mean_jade']:+.4f}")
        print(f"  mean_bpbc:      ${cell['mean_bpbc']:+.4f}")
        print(f"  lift:           ${cell['lift_vs_bpbc']:+.4f}")
        print(f"  p_value:        {cell['p_value']}")
        verdict = "✓ pass" if (cell['lift_vs_bpbc'] >= 0.05 and cell['p_value'] < 0.05) else "✗ fail"
        print(f"  promotion gate: {verdict}")

    # Top per-ticker by lift at slip=0.5 exp
    print("\n" + "=" * 100)
    print("TOP 20 — per-ticker lift at slip=0.50, exp exit")
    print("=" * 100)
    top = pertkr_df[(pertkr_df["slip"] == 0.50) & (pertkr_df["exit_rule"] == "exp")
                     & (pertkr_df["N_jade"] >= 20)]
    top = top.sort_values("lift_vs_bpbc", ascending=False).head(20)
    print(top.to_string(index=False))

    # Bottom 20 (most negative lift)
    print("\n" + "=" * 100)
    print("BOTTOM 20 — per-ticker lift at slip=0.50, exp exit (jade hurts most)")
    print("=" * 100)
    bot = pertkr_df[(pertkr_df["slip"] == 0.50) & (pertkr_df["exit_rule"] == "exp")
                     & (pertkr_df["N_jade"] >= 20)]
    bot = bot.sort_values("lift_vs_bpbc").head(20)
    print(bot.to_string(index=False))

    # Promotion-candidate names: positive mean AND positive lift at slip=0.5 exp
    print("\n" + "=" * 100)
    print("PROMOTION CANDIDATES — positive mean_jade AND positive lift_vs_bpbc, slip=0.5 exp, N>=20")
    print("=" * 100)
    promote = pertkr_df[(pertkr_df["slip"] == 0.50) &
                         (pertkr_df["exit_rule"] == "exp") &
                         (pertkr_df["mean_jade"] > 0) &
                         (pertkr_df["lift_vs_bpbc"] > 0) &
                         (pertkr_df["N_jade"] >= 20)]
    promote = promote.sort_values("lift_vs_bpbc", ascending=False)
    print(f"Count: {len(promote)} names")
    print(promote.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
