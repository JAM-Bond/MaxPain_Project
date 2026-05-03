"""ZEBRA universe expansion v2 — backtest against the v2 liquidity pool.

Sealed in docs/UNIVERSE_EXPANSION_V2_PREREG.md (Phase 2 ZEBRA scope) but
the original v2 orchestrator only ran bull_put / bear_call / IF. This is
the deferred ZEBRA pass.

Pool source: data/profile/universe_v2_liquidity_pool.parquet (163 names
that passed the liquidity gates 2026-05-02). Excludes already-promoted
ZEBRA Tier 1 + Tier 2 cohort and the 7 v1 expansion candidates from
2026-05-01 (already tested with the same methodology — preserved as the
historical baseline).

Outputs to *_v2_* parquets so v1 artifacts remain intact for the audit:
  - data/profile/zebra_universe_expansion_v2_results.parquet  (cycle-level)
  - data/profile/zebra_universe_expansion_v2_walkforward.parquet  (per-ticker)
  - data/profile/zebra_universe_expansion_v2_promoted.parquet  (scorecard)

Promotion gates mirror the sealed pre-reg (per-ticker, all required):
  - n_total >= 50, n_train >= 22
  - flat_day_mean_mtm >= -0.01
  - median_capture_upside >= 0.85
  - cap_efficiency <= 0.50
  - walk-forward: train_mean > 0 AND val_mean > 0 AND train_cap >= 1.0 AND val_cap >= 1.0
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from scripts.backtest.zebra_backtest import simulate_ticker  # noqa: E402
from scripts.qualifier import gate_config as G  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("zebra_v2_expansion_bt")

POOL_PATH = ROOT / "data/profile/universe_v2_liquidity_pool.parquet"
RESULTS_OUT = ROOT / "data/profile/zebra_universe_expansion_v2_results.parquet"
WALKFORWARD_OUT = ROOT / "data/profile/zebra_universe_expansion_v2_walkforward.parquet"
PROMOTED_OUT = ROOT / "data/profile/zebra_universe_expansion_v2_promoted.parquet"

# v1 expansion (2026-05-01) candidates — already tested under the same
# methodology, preserved in zebra_universe_expansion_*.parquet. Excluding
# avoids redundant compute.
V1_EXPANSION_TESTED = ["HAL", "SLB", "STM", "KO", "INTC", "XLE", "KRE"]

# Promotion-gate thresholds — verbatim from the sealed pre-reg (ZEBRA section)
MIN_FIRE_RATE = 0.30
MIN_FLAT_DAY_MTM = -0.01
MIN_MEDIAN_CAPTURE = 0.85
MAX_CAP_EFFICIENCY = 0.50
MIN_N_TRAIN = 22
MIN_N_TOTAL = 50
WALKFORWARD_TRAIN_END_YEAR = 2022


def select_candidates(limit: int | None = None) -> list[str]:
    """Pull the v2 liquidity pool, exclude already-promoted + v1-tested."""
    pool = pd.read_parquet(POOL_PATH)
    passing = pool[pool["passes_all"] == True]["ticker"].tolist()
    log.info("Pool: %d total, %d passing all liquidity gates", len(pool), len(passing))

    already_promoted = set(G.COHORT_ZEBRA_TIER1) | set(G.COHORT_ZEBRA_TIER2)
    exclude = already_promoted | set(V1_EXPANSION_TESTED)

    candidates = [t for t in passing if t not in exclude]
    log.info("Excluding %d already-promoted (Tier 1+2) + %d v1-tested = %d candidates remain",
             len(already_promoted), len(V1_EXPANSION_TESTED), len(candidates))

    if limit is not None:
        candidates = candidates[:limit]
        log.info("Smoke-test limit applied: testing %d", len(candidates))

    return candidates


def run_backtest(tickers: list[str]) -> pd.DataFrame:
    all_sums: list[dict] = []
    n_no_data = 0
    for i, t in enumerate(tickers, 1):
        log.info("[%d/%d] simulating %s...", i, len(tickers), t)
        try:
            sums, _ = simulate_ticker(t)
        except Exception as e:
            log.warning("    %s failed: %s", t, e)
            n_no_data += 1
            continue
        if not sums:
            log.info("    %s: no cycles (insufficient ORATS history)", t)
            n_no_data += 1
            continue
        all_sums.extend(sums)
        log.info("    %s: %d cycle rows", t, len(sums))
    log.info("Backtest complete: %d cycles across %d tickers (%d skipped: no data)",
             len(all_sums), len(tickers) - n_no_data, n_no_data)
    if not all_sums:
        raise RuntimeError("Zero cycles produced — check ORATS coverage")
    return pd.DataFrame(all_sums)


def per_ticker_scorecard(df: pd.DataFrame, slip: float = 0.50) -> pd.DataFrame:
    sub = df[df["slip"] == slip].copy()
    sub["entry_year"] = pd.to_datetime(sub["entry_date"]).dt.year
    rows = []
    for t, grp in sub.groupby("ticker"):
        n_total = len(grp)
        win_mask = grp["pnl_zebra"] > 0
        upside_mask = grp["pnl_stock"] > 0
        flat_day_avg = grp["flat_day_mean_mtm_change"].dropna().mean()
        median_capture = (grp.loc[upside_mask, "capture_ratio"].median()
                          if upside_mask.any() else np.nan)
        train = grp[grp["entry_year"] <= WALKFORWARD_TRAIN_END_YEAR]
        val = grp[grp["entry_year"] > WALKFORWARD_TRAIN_END_YEAR]
        train_capture = (train.loc[train["pnl_stock"] > 0, "capture_ratio"].median()
                         if (train["pnl_stock"] > 0).any() else np.nan)
        val_capture = (val.loc[val["pnl_stock"] > 0, "capture_ratio"].median()
                       if (val["pnl_stock"] > 0).any() else np.nan)
        rows.append({
            "ticker": t, "slip": slip,
            "n_total": n_total, "n_train": len(train), "n_val": len(val),
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
        out["gate_n_total"] & out["gate_n_train"] & out["gate_flat_day"]
        & out["gate_median_capture"] & out["gate_cap_efficiency"] & out["gate_walkforward"]
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="Smoke-test mode: only run first N candidates")
    args = parser.parse_args()

    candidates = select_candidates(limit=args.limit)
    log.info("Running ZEBRA v2 expansion on %d candidates", len(candidates))

    cycles = run_backtest(candidates)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    cycles.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d cycle rows to %s", len(cycles), RESULTS_OUT)

    scorecard = per_ticker_scorecard(cycles, slip=0.50)
    scorecard.to_parquet(WALKFORWARD_OUT, index=False)
    log.info("Wrote per-ticker scorecard to %s", WALKFORWARD_OUT)

    promoted = evaluate_gates(scorecard)
    promoted.to_parquet(PROMOTED_OUT, index=False)
    log.info("Wrote promotion verdicts to %s", PROMOTED_OUT)

    print("\n══════════════════════════════════════════════════════════")
    print(f"  ZEBRA v2 EXPANSION RESULTS — {len(promoted)} tickers tested")
    print("══════════════════════════════════════════════════════════")
    n_promoted = int(promoted["promoted"].sum())
    print(f"  PROMOTED: {n_promoted}\n")

    if n_promoted > 0:
        print("  Promoted tickers (gates ALL pass):")
        cols = ["ticker", "n_total", "mean_zebra", "median_capture_upside",
                "cap_efficiency", "mean_zebra_train", "mean_zebra_val"]
        print(promoted.loc[promoted["promoted"], cols].to_string(index=False))
        print()

    print("  Top 10 near-misses (most gates passed but not all):")
    near = promoted[~promoted["promoted"]].copy()
    gate_cols = ["gate_n_total", "gate_n_train", "gate_flat_day",
                 "gate_median_capture", "gate_cap_efficiency", "gate_walkforward"]
    near["gates_passed"] = near[gate_cols].sum(axis=1)
    near = near.sort_values(["gates_passed", "n_total"], ascending=[False, False]).head(10)
    print(near[["ticker", "gates_passed", "fail_summary"]].to_string(index=False))


if __name__ == "__main__":
    main()
