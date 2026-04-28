"""
Inverted_fly Phase A batch — 6 pre-registered tests on existing wide-wings data.

All tests use inverted_fly at 10% wings, dte_45 entry, slip=0.25 (canonical cell
per project_inverted_fly_wide_wings_findings.md).

Tests:
  A1. Signal filter — does "term inverted OR VRP<0 OR high IVR" lift inverted_fly?
  A2. Managed (first-trigger 50%/21-DTE) vs 50%-only vs held-to-expiry
  A3. Walk-forward on TSLA/AMD/CAR/META/BABA (train 2013-2022, validate 2023-2026)
  A4. Year-by-year stability for the 5 single-name candidates
  A5. Core-6 correlation matrix (SPX/SPY/QQQ/DIA/TSLA/META)
  A6. Worst-cycle tail per name (p1/p5/p50/p95/p99)

Baseline: inverted_fly at 10% wings dte_45 — per project_inverted_fly_wide_wings_findings.md,
universe-wide mean +$0.146/cycle, win rate declining from 64% at narrow to 51% at 10%.
"""

from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path("/Users/josephmorris/MaxPain_Project")
IF_DATA = ROOT / "data/backtest/results_wide_wings_universe_slip025.parquet"
SIGNAL = ROOT / "data/profile/signal_vrp_termstruct_spy.parquet"
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"

SINGLES = ["TSLA", "AMD", "CAR", "META", "BABA"]
CORE6 = ["SPX", "SPY", "QQQ", "DIA", "TSLA", "META"]


def load_if(wing: float = 0.10, entry_label: str = "dte_45") -> pd.DataFrame:
    df = pd.read_parquet(IF_DATA)
    df = df[(df["wing_pct"] == wing) & (df["entry_label"] == entry_label)]
    df = df[df["exit_rule"].isin(["50_pct", "dte_21"])].copy()
    return df


def compute_exit_variants(df: pd.DataFrame) -> pd.DataFrame:
    """Compute three P&L columns per (ticker, expiration, entry_date):
    pnl_50pct (50%-only), pnl_21dte (21-DTE only), pnl_managed (earlier of the two)."""
    grouped = df.sort_values("exit_date").groupby(
        ["ticker", "expiration", "entry_date"], as_index=False
    )
    managed = grouped.first()
    pnl_50 = df[df["exit_rule"] == "50_pct"].set_index(
        ["ticker", "expiration", "entry_date"])["pnl"]
    pnl_21 = df[df["exit_rule"] == "dte_21"].set_index(
        ["ticker", "expiration", "entry_date"])["pnl"]
    managed["pnl_managed"] = managed["pnl"]
    managed["pnl_50pct"] = managed.set_index(
        ["ticker", "expiration", "entry_date"]
    ).index.map(pnl_50)
    managed["pnl_21dte"] = managed.set_index(
        ["ticker", "expiration", "entry_date"]
    ).index.map(pnl_21)
    return managed


def stats_pnl(s: pd.Series) -> dict:
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


def main() -> None:
    print("Loading inverted_fly 10%-wing dte_45 cycles...")
    df_raw = load_if(wing=0.10, entry_label="dte_45")
    df = compute_exit_variants(df_raw)
    sig = pd.read_parquet(SIGNAL).rename(columns={"trade_date": "entry_date"})[
        ["entry_date", "term_spread", "vrp", "iv_rank"]
    ]
    df = df.merge(sig, on="entry_date", how="left")
    print(f"Cycles with signals: {df['term_spread'].notna().sum()}/{len(df)}")
    df = df.dropna(subset=["term_spread", "vrp", "iv_rank"]).copy()

    # ==================================================================
    # A2 first (needed as baselines for other tests)
    # ==================================================================
    print("\n=== A2: Exit-rule comparison (universe-wide) ===")
    a2_rows = []
    for label, col in [("held-to-rule (50%-only)", "pnl_50pct"),
                        ("held-to-rule (21-DTE only)", "pnl_21dte"),
                        ("managed (first-trigger)", "pnl_managed")]:
        a2_rows.append({"exit": label, **stats_pnl(df[col])})
    a2_df = pd.DataFrame(a2_rows)
    print(a2_df.to_string(index=False))

    # Pick winning exit rule for remaining tests
    best_exit_col = "pnl_50pct"  # hypothesis: long-vol keeps 50%-only
    best_exit_mean = df[best_exit_col].mean()
    print(f"\nUsing {best_exit_col} as canonical for A1/A3/A4/A5/A6 (mean ${best_exit_mean:+.4f}).")

    # ==================================================================
    # A1. Signal filter
    # ==================================================================
    print("\n=== A1: Signal filters on inverted_fly (universe-wide, 50%-only exit) ===")
    baseline = df[best_exit_col]
    a1_rows = []
    a1_rows.append({"cohort": "Baseline (all)", **stats_pnl(baseline)})

    filters = {
        "Term inverted (spread > 0)": df["term_spread"] > 0,
        "VRP negative (vrp < 0)": df["vrp"] < 0,
        "IV rank > 0.7 (elevated)": df["iv_rank"] > 0.7,
        "IV rank > 0.5": df["iv_rank"] > 0.5,
        "Term inv OR VRP<0 (either vol stress)": (df["term_spread"] > 0) | (df["vrp"] < 0),
        "Term inv AND VRP<0 (joint stress)": (df["term_spread"] > 0) & (df["vrp"] < 0),
        "Term inv AND IVR>0.5": (df["term_spread"] > 0) & (df["iv_rank"] > 0.5),
        "Contango + VRP>0 (inverse — short-vol signal)": (df["term_spread"] < 0) & (df["vrp"] > 0),
    }
    for name, mask in filters.items():
        a1_rows.append({"cohort": name, **stats_pnl(df.loc[mask, best_exit_col])})

    a1_df = pd.DataFrame(a1_rows)
    a1_df["lift_vs_base"] = (a1_df["mean"] - a1_df["mean"].iloc[0]).round(4)
    print(a1_df.to_string(index=False))

    # ==================================================================
    # A3. Walk-forward per single-name candidate
    # ==================================================================
    print("\n=== A3: Walk-forward on single-name candidates (50%-only exit) ===")
    a3_rows = []
    train_end = "2022-12-31"
    for tkr in SINGLES:
        sub = df[df["ticker"] == tkr]
        train = sub[sub["entry_date"] <= train_end][best_exit_col].dropna()
        val = sub[sub["entry_date"] > train_end][best_exit_col].dropna()
        a3_rows.append({
            "ticker": tkr,
            "N_train": len(train),
            "mean_train": round(train.mean(), 4) if len(train) else np.nan,
            "win_train": round((train > 0).mean(), 3) if len(train) else np.nan,
            "worst_train": round(train.min(), 2) if len(train) else np.nan,
            "N_val": len(val),
            "mean_val": round(val.mean(), 4) if len(val) else np.nan,
            "win_val": round((val > 0).mean(), 3) if len(val) else np.nan,
            "worst_val": round(val.min(), 2) if len(val) else np.nan,
            "lift": round((val.mean() - train.mean())
                          if len(train) and len(val) else np.nan, 4),
        })
    a3_df = pd.DataFrame(a3_rows)
    print(a3_df.to_string(index=False))

    # ==================================================================
    # A4. Year-by-year stability per single-name candidate
    # ==================================================================
    print("\n=== A4: Per-name annual P&L stability (50%-only) ===")
    df["year"] = df["entry_date"].dt.year
    a4_rows = []
    for tkr in SINGLES:
        sub = df[df["ticker"] == tkr]
        for year, g in sub.groupby("year"):
            pnl = g[best_exit_col].dropna()
            if len(pnl) == 0:
                continue
            a4_rows.append({
                "ticker": tkr,
                "year": int(year),
                "N": len(pnl),
                "mean": round(pnl.mean(), 4),
                "win": round((pnl > 0).mean(), 3),
                "total": round(pnl.sum(), 2),
            })
    a4_df = pd.DataFrame(a4_rows)
    pivot_mean = a4_df.pivot(index="ticker", columns="year", values="mean")
    pivot_total = a4_df.pivot(index="ticker", columns="year", values="total")
    print("Per-name annual MEAN:")
    print(pivot_mean.to_string(float_format=lambda x: f"{x:+.3f}" if pd.notna(x) else "   -  "))
    print("\nPer-name annual TOTAL:")
    print(pivot_total.to_string(float_format=lambda x: f"{x:+.1f}" if pd.notna(x) else "   -  "))

    # Annual positive fraction
    print("\nFraction of years positive per name:")
    for tkr in SINGLES:
        sub = a4_df[a4_df["ticker"] == tkr]
        if len(sub) == 0:
            continue
        pos_years = (sub["mean"] > 0).sum()
        tot_years = len(sub)
        print(f"  {tkr}: {pos_years}/{tot_years} years positive "
              f"= {pos_years/tot_years:.1%}")

    # ==================================================================
    # A5. Core-6 correlation matrix
    # ==================================================================
    print("\n=== A5: Core-6 P&L correlation matrix (50%-only, monthly OpEx cycles) ===")
    core_df = df[df["ticker"].isin(CORE6)].copy()
    # Pivot: rows = expiration date, cols = ticker, values = pnl
    piv = core_df.pivot_table(
        index="expiration", columns="ticker", values=best_exit_col, aggfunc="mean"
    )
    piv = piv.reindex(columns=CORE6)
    print(f"Matrix shape: {piv.shape} (rows = monthly OpEx dates)")
    corr = piv.corr(method="pearson", min_periods=20)
    print("\nPearson correlation:")
    print(corr.round(3).to_string())
    n_matrix = piv.notna().astype(int).T.dot(piv.notna().astype(int))
    print("\nPair-wise overlap counts (min of observations where both names have a cycle):")
    print(n_matrix.to_string())

    # If SPX drops out, recompute correlation
    non_spx = [t for t in CORE6 if t != "SPX"]
    print("\nPearson correlation excluding SPX:")
    print(piv[non_spx].corr(method="pearson", min_periods=20).round(3).to_string())

    # ==================================================================
    # A6. Worst-cycle tail per name (core + singles)
    # ==================================================================
    print("\n=== A6: Tail characterization per name (50%-only) ===")
    all_names = list(dict.fromkeys(CORE6 + SINGLES))
    a6_rows = []
    for tkr in all_names:
        pnl = df[df["ticker"] == tkr][best_exit_col].dropna()
        if len(pnl) < 20:
            continue
        a6_rows.append({
            "ticker": tkr,
            "N": len(pnl),
            "mean": round(pnl.mean(), 4),
            "p1": round(pnl.quantile(0.01), 2),
            "p5": round(pnl.quantile(0.05), 2),
            "p50": round(pnl.quantile(0.50), 2),
            "p95": round(pnl.quantile(0.95), 2),
            "p99": round(pnl.quantile(0.99), 2),
            "worst": round(pnl.min(), 2),
            "best": round(pnl.max(), 2),
        })
    a6_df = pd.DataFrame(a6_rows)
    print(a6_df.to_string(index=False))

    # ==================================================================
    # Save outputs
    # ==================================================================
    out_dir = ROOT / "data/profile"
    a1_df.to_parquet(out_dir / "if_phase_a_signal_filters.parquet", index=False)
    a2_df.to_parquet(out_dir / "if_phase_a_exit_variants.parquet", index=False)
    a3_df.to_parquet(out_dir / "if_phase_a_walkforward.parquet", index=False)
    a4_df.to_parquet(out_dir / "if_phase_a_annual_stability.parquet", index=False)
    corr.to_parquet(out_dir / "if_phase_a_core6_correlation.parquet")
    a6_df.to_parquet(out_dir / "if_phase_a_tail_characterization.parquet", index=False)
    print("\nAll outputs saved to data/profile/if_phase_a_*.parquet")


if __name__ == "__main__":
    main()
