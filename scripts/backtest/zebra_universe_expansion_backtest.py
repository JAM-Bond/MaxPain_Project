"""
ZEBRA universe expansion — backtest + walk-forward + promotion scorecard.

Runs simulate_ticker() from zebra_backtest.py against the curated 7-ticker pool
defined in ZEBRA_UNIVERSE_EXPANSION_PREREG.md, then evaluates each ticker
against the promotion gates from the sealed pre-reg.

Outputs:
  - data/profile/zebra_universe_expansion_results.parquet  (cycle-level)
  - data/profile/zebra_universe_expansion_walkforward.parquet  (per-ticker train/val)
  - data/profile/zebra_universe_expansion_promoted.parquet  (per-ticker scorecard)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from scripts.backtest.zebra_backtest import simulate_ticker  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("zebra_expansion_bt")

TARGETS = ["HAL", "SLB", "STM", "KO", "INTC", "XLE", "KRE"]

RESULTS_OUT = ROOT / "data/profile/zebra_universe_expansion_results.parquet"
WALKFORWARD_OUT = ROOT / "data/profile/zebra_universe_expansion_walkforward.parquet"
PROMOTED_OUT = ROOT / "data/profile/zebra_universe_expansion_promoted.parquet"

# Promotion-gate thresholds (mirror sealed pre-reg)
MIN_FIRE_RATE = 0.30          # H1
MIN_FLAT_DAY_MTM = -0.01       # H2
MIN_MEDIAN_CAPTURE = 0.85      # H3
MAX_CAP_EFFICIENCY = 0.50      # H4
MIN_N_TRAIN = 22
MIN_N_TOTAL = 50
WALKFORWARD_TRAIN_END_YEAR = 2022


def run_backtest(tickers: list[str]) -> pd.DataFrame:
    """Run zebra_backtest.simulate_ticker for each ticker; return cycle-level rows."""
    all_sums = []
    for i, t in enumerate(tickers, 1):
        log.info("[%d/%d] simulating %s...", i, len(tickers), t)
        sums, _ = simulate_ticker(t)
        all_sums.extend(sums)
        log.info("    %s: %d cycle rows", t, len(sums))
    if not all_sums:
        raise RuntimeError("Zero cycles produced — check ORATS coverage for the cohort")
    return pd.DataFrame(all_sums)


def per_ticker_scorecard(df: pd.DataFrame, slip: float = 0.50) -> pd.DataFrame:
    """Compute per-ticker promotion-gate metrics at a given slip."""
    sub = df[df["slip"] == slip].copy()
    sub["entry_year"] = pd.to_datetime(sub["entry_date"]).dt.year

    rows = []
    for t, grp in sub.groupby("ticker"):
        n_total = len(grp)
        win_mask = grp["pnl_zebra"] > 0
        upside_mask = grp["pnl_stock"] > 0
        flat_day_avg = grp["flat_day_mean_mtm_change"].dropna().mean()

        median_capture = (
            grp.loc[upside_mask, "capture_ratio"].median()
            if upside_mask.any() else np.nan
        )

        # Walk-forward split
        train = grp[grp["entry_year"] <= WALKFORWARD_TRAIN_END_YEAR]
        val = grp[grp["entry_year"] > WALKFORWARD_TRAIN_END_YEAR]
        train_capture = (
            train.loc[train["pnl_stock"] > 0, "capture_ratio"].median()
            if (train["pnl_stock"] > 0).any() else np.nan
        )
        val_capture = (
            val.loc[val["pnl_stock"] > 0, "capture_ratio"].median()
            if (val["pnl_stock"] > 0).any() else np.nan
        )

        rows.append({
            "ticker": t,
            "slip": slip,
            "n_total": n_total,
            "n_train": len(train),
            "n_val": len(val),
            "mean_zebra": grp["pnl_zebra"].mean(),
            "mean_stock": grp["pnl_stock"].mean(),
            "win_rate": float(win_mask.mean()),
            "median_capture_upside": float(median_capture) if pd.notna(median_capture) else np.nan,
            "cap_efficiency": grp["capital_efficiency"].mean(),
            "flat_day_mean_mtm": float(flat_day_avg) if pd.notna(flat_day_avg) else np.nan,
            "mean_zebra_train": train["pnl_zebra"].mean() if len(train) else np.nan,
            "mean_zebra_val": val["pnl_zebra"].mean() if len(val) else np.nan,
            "median_capture_train": float(train_capture) if pd.notna(train_capture) else np.nan,
            "median_capture_val": float(val_capture) if pd.notna(val_capture) else np.nan,
        })
    return pd.DataFrame(rows)


def evaluate_gates(scorecard: pd.DataFrame) -> pd.DataFrame:
    """Stamp pass/fail flags + per-row summary reason."""
    out = scorecard.copy()
    out["gate_n_total"] = out["n_total"] >= MIN_N_TOTAL
    out["gate_n_train"] = out["n_train"] >= MIN_N_TRAIN
    out["gate_flat_day"] = out["flat_day_mean_mtm"] >= MIN_FLAT_DAY_MTM
    out["gate_median_capture"] = out["median_capture_upside"] >= MIN_MEDIAN_CAPTURE
    out["gate_cap_efficiency"] = out["cap_efficiency"] <= MAX_CAP_EFFICIENCY
    out["gate_walkforward"] = (
        (out["mean_zebra_train"] > 0)
        & (out["mean_zebra_val"] > 0)
        & (out["median_capture_train"] >= 1.0)
        & (out["median_capture_val"] >= 1.0)
    )
    out["promoted"] = (
        out["gate_n_total"]
        & out["gate_n_train"]
        & out["gate_flat_day"]
        & out["gate_median_capture"]
        & out["gate_cap_efficiency"]
        & out["gate_walkforward"]
    )

    def fail_reason(row):
        fails = []
        if not row["gate_n_total"]: fails.append(f"n_total={row['n_total']}<{MIN_N_TOTAL}")
        if not row["gate_n_train"]: fails.append(f"n_train={row['n_train']}<{MIN_N_TRAIN}")
        if not row["gate_flat_day"]: fails.append(f"flat_day_mtm={row['flat_day_mean_mtm']:.3f}<{MIN_FLAT_DAY_MTM}")
        if not row["gate_median_capture"]: fails.append(f"med_capture={row['median_capture_upside']:.2f}<{MIN_MEDIAN_CAPTURE}")
        if not row["gate_cap_efficiency"]: fails.append(f"cap_eff={row['cap_efficiency']:.2f}>{MAX_CAP_EFFICIENCY}")
        if not row["gate_walkforward"]:
            fails.append(
                f"WF train_mean={row['mean_zebra_train']:.2f} val_mean={row['mean_zebra_val']:.2f} "
                f"train_cap={row['median_capture_train']:.2f} val_cap={row['median_capture_val']:.2f}"
            )
        return "; ".join(fails) if fails else "all gates pass"
    out["fail_summary"] = out.apply(fail_reason, axis=1)
    return out


def main():
    log.info("ZEBRA universe expansion backtest — cohort: %s", ", ".join(TARGETS))

    # ── Run backtest ──
    cycles = run_backtest(TARGETS)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    cycles.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d cycle rows to %s", len(cycles), RESULTS_OUT)

    # ── Scorecard at slip=0.50 (primary) ──
    scorecard_50 = per_ticker_scorecard(cycles, slip=0.50)
    scorecard_25 = per_ticker_scorecard(cycles, slip=0.25)

    promoted_50 = evaluate_gates(scorecard_50)
    promoted_25 = evaluate_gates(scorecard_25)

    promoted_combined = pd.concat([promoted_50, promoted_25], ignore_index=True)
    promoted_combined.to_parquet(PROMOTED_OUT, index=False)
    log.info("Wrote scorecard to %s", PROMOTED_OUT)

    # ── Walk-forward dump ──
    wf_cols = ["ticker", "slip", "n_train", "n_val", "mean_zebra_train", "mean_zebra_val",
               "median_capture_train", "median_capture_val"]
    promoted_combined[wf_cols].to_parquet(WALKFORWARD_OUT, index=False)
    log.info("Wrote walk-forward to %s", WALKFORWARD_OUT)

    # ── Print primary (slip=0.50) results ──
    print("\n" + "=" * 80)
    print(f"ZEBRA UNIVERSE EXPANSION — BACKTEST RESULTS (slip=0.50)")
    print("=" * 80)
    print()

    primary = promoted_50.sort_values("mean_zebra", ascending=False)
    cols_show = ["ticker", "n_total", "n_train", "n_val", "mean_zebra", "mean_stock",
                 "win_rate", "median_capture_upside", "cap_efficiency", "flat_day_mean_mtm",
                 "promoted"]
    print(primary[cols_show].to_string(index=False, float_format="%.3f"))

    print("\nWalk-forward (slip=0.50):")
    wf_show = ["ticker", "n_train", "mean_zebra_train", "median_capture_train",
               "n_val", "mean_zebra_val", "median_capture_val"]
    print(promoted_50[wf_show].sort_values("ticker").to_string(index=False, float_format="%.3f"))

    print("\nGate evaluation (slip=0.50):")
    for _, r in promoted_50.sort_values("ticker").iterrows():
        flag = "✓ PROMOTED" if r["promoted"] else "✗ REJECT  "
        print(f"  {flag}  {r['ticker']}  →  {r['fail_summary']}")

    n_promoted = int(promoted_50["promoted"].sum())
    print()
    print(f"PROMOTED: {n_promoted}/{len(promoted_50)} at slip=0.50")
    if n_promoted > 0:
        print(f"Promoted names: {sorted(promoted_50.loc[promoted_50['promoted'], 'ticker'].tolist())}")


if __name__ == "__main__":
    main()
