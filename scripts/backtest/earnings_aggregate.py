"""Aggregate earnings_structure_results.parquet into cohort-level scorecards.

For each (cohort, structure, entry_label, exit_rule, slip):
  - Compute earnings cohort mean P&L, win rate, N, worst, best
  - Compute control cohort mean P&L (synthetic non-earnings days, same protocol)
  - lift_vs_control = earnings_mean - control_mean
  - Welch t-statistic and approximate p-value for the difference

Also produces a per-ticker breakdown for the best/worst cohort cells.
"""
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path("/Users/josephmorris/MaxPain_Project")
RAW = ROOT / "data/profile/earnings_structure_results.parquet"
COHORT_OUT = ROOT / "data/profile/earnings_cohort_scorecard.parquet"
PERTKR_OUT = ROOT / "data/profile/earnings_per_ticker_scorecard.parquet"


def stats_for(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) == 0:
        return {"N": 0, "mean": np.nan, "median": np.nan, "win_rate": np.nan,
                "worst": np.nan, "best": np.nan, "total": np.nan, "std": np.nan}
    return {
        "N": int(len(s)),
        "mean": round(float(s.mean()), 4),
        "median": round(float(s.median()), 4),
        "win_rate": round(float((s > 0).mean()), 3),
        "worst": round(float(s.min()), 4),
        "best": round(float(s.max()), 4),
        "total": round(float(s.sum()), 2),
        "std": round(float(s.std(ddof=1)), 4) if len(s) > 1 else np.nan,
    }


def main() -> None:
    df = pd.read_parquet(RAW)
    print(f"Loaded {len(df):,} raw rows; {df['ticker'].nunique()} tickers")
    print(f"Anchor mix: {df['anchor_kind'].value_counts().to_dict()}")
    print()

    # ─── Cohort scorecard ────────────────────────────────────
    grp_cols = ["cohort", "structure", "entry_label", "exit_rule", "slip"]
    cohort_rows = []
    for keys, sub in df.groupby(grp_cols):
        cohort, struct, entry_l, exit_l, slip = keys
        earn = sub[sub["anchor_kind"] == "earnings"]["pnl"].dropna()
        ctrl = sub[sub["anchor_kind"] == "control"]["pnl"].dropna()
        e = stats_for(earn)
        c = stats_for(ctrl)
        # Welch's t-test
        if len(earn) >= 5 and len(ctrl) >= 5:
            t_stat, p_val = stats.ttest_ind(earn, ctrl, equal_var=False)
            t_stat, p_val = float(t_stat), float(p_val)
        else:
            t_stat, p_val = np.nan, np.nan
        cohort_rows.append({
            "cohort": cohort, "structure": struct,
            "entry_label": entry_l, "exit_rule": exit_l, "slip": slip,
            "N_earn": e["N"], "mean_earn": e["mean"], "win_earn": e["win_rate"],
            "worst_earn": e["worst"], "best_earn": e["best"],
            "N_ctrl": c["N"], "mean_ctrl": c["mean"], "win_ctrl": c["win_rate"],
            "lift": round(e["mean"] - c["mean"], 4) if e["N"] > 0 and c["N"] > 0 else np.nan,
            "t_stat": round(t_stat, 3) if pd.notna(t_stat) else np.nan,
            "p_value": round(p_val, 4) if pd.notna(p_val) else np.nan,
        })
    cohort_df = pd.DataFrame(cohort_rows)
    cohort_df = cohort_df.sort_values(["cohort", "slip", "entry_label", "exit_rule"])

    # ─── Per-ticker scorecard (focus: lift per name) ─────────
    grp_cols_t = ["cohort", "ticker", "structure", "entry_label", "exit_rule", "slip"]
    pertkr_rows = []
    for keys, sub in df.groupby(grp_cols_t):
        cohort, tkr, struct, entry_l, exit_l, slip = keys
        earn = sub[sub["anchor_kind"] == "earnings"]["pnl"].dropna()
        ctrl = sub[sub["anchor_kind"] == "control"]["pnl"].dropna()
        e = stats_for(earn)
        c = stats_for(ctrl)
        pertkr_rows.append({
            "cohort": cohort, "ticker": tkr, "structure": struct,
            "entry_label": entry_l, "exit_rule": exit_l, "slip": slip,
            "N_earn": e["N"], "mean_earn": e["mean"], "win_earn": e["win_rate"],
            "worst_earn": e["worst"], "best_earn": e["best"],
            "N_ctrl": c["N"], "mean_ctrl": c["mean"],
            "lift": round(e["mean"] - c["mean"], 4) if e["N"] > 0 and c["N"] > 0 else np.nan,
        })
    pertkr_df = pd.DataFrame(pertkr_rows)
    pertkr_df = pertkr_df.sort_values(["cohort", "structure", "slip", "ticker"])

    cohort_df.to_parquet(COHORT_OUT, index=False)
    pertkr_df.to_parquet(PERTKR_OUT, index=False)
    print(f"Wrote cohort scorecard: {COHORT_OUT}")
    print(f"Wrote per-ticker scorecard: {PERTKR_OUT}")

    # ─── Display key results ─────────────────────────────────
    print("\n" + "=" * 110)
    print("COHORT-LEVEL RESULTS")
    print("=" * 110)
    print(cohort_df.to_string(index=False))

    print("\n" + "=" * 110)
    print("FALSIFICATION CHECK — was hypothesis falsified?")
    print("=" * 110)
    for cohort_label, struct_name in [("T1", "bull_put"), ("T2", "bear_call"),
                                       ("T4", "inverted_fly")]:
        cells = cohort_df[(cohort_df["cohort"] == cohort_label) &
                          (cohort_df["slip"] == 0.50)]
        positive_lift_cells = cells[cells["lift"] > 0]
        print(f"\n{cohort_label} ({struct_name}, slip=0.50):")
        print(f"  cells: {len(cells)} | positive lift: {len(positive_lift_cells)}")
        if len(positive_lift_cells) == 0:
            print(f"  ❌ FALSIFIED — no cell has lift > 0 at slip=0.50")
        else:
            best = positive_lift_cells.sort_values("lift", ascending=False).head(3)
            print("  ✓ NOT falsified — top 3 surviving cells:")
            print(best[["entry_label", "exit_rule", "N_earn", "mean_earn",
                       "mean_ctrl", "lift", "p_value"]].to_string(index=False))

    print("\n" + "=" * 110)
    print("BEST PER-TICKER CELLS (lift>0 AND mean_earn>0 AND slip=0.5)")
    print("=" * 110)
    promote = pertkr_df[(pertkr_df["lift"] > 0) &
                         (pertkr_df["mean_earn"] > 0) &
                         (pertkr_df["slip"] == 0.50) &
                         (pertkr_df["N_earn"] >= 10)]
    promote = promote.sort_values("lift", ascending=False).head(30)
    print(promote.to_string(index=False))


if __name__ == "__main__":
    main()
