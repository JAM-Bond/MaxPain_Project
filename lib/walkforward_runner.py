"""Per-ticker per-structure walk-forward runner for the auto-promotion pipeline.

Reuses the existing `simulate_ticker()` entry points in each structure's
backtest script (which are themselves per-ticker), then applies a 4-split
rolling walk-forward and aggregates the metrics needed by the sealed
promotion gates in `lib.auto_promotion`:
  - per-split mean P/L + val_N + Wilcoxon p (Gate B)
  - per-year P/L for concentration cap (Gate D)
  - most-recent split p-value for BH-FDR (Gate E)

Output units (chosen to match `lib.auto_promotion` thresholds):
  - bull_put / bear_call : $/contract  (per-share × 100)
  - inverted_fly         : $/contract
  - zebra                : % median capture × 100 (so 0.85 ratio → 85.0)

A ticker can be processed standalone; the existing aggregate-universe
backtest+walkforward scripts are NOT invoked, which is what bounds runtime
to ~30s per (ticker, structure).
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path.home() / "MaxPain_Project"
BACKTEST_DIR = ROOT / "scripts/backtest"
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Lazy imports inside the dispatcher to avoid importing every backtest at
# module load (each carries config.activate_slip side effects).

STRUCTURES = ("bull_put", "bear_call", "inverted_fly", "zebra")

# Default 4-split rolling walk-forward (Phase C convention from
# if_phase_c_walkforward_rolling.py). Each split: 8-year train, 3-year val.
DEFAULT_SPLITS: list[tuple[str, str, str, str]] = [
    ("2013-01-01", "2020-12-31", "2021-01-01", "2023-12-31"),
    ("2014-01-01", "2021-12-31", "2022-01-01", "2024-12-31"),
    ("2015-01-01", "2022-12-31", "2023-01-01", "2025-12-31"),
    ("2016-01-01", "2023-12-31", "2024-01-01", "2026-12-31"),
]

# Minimum cycles required to make any judgment at all.
MIN_CYCLES_PER_SPLIT = 5

log = logging.getLogger("walkforward_runner")


# ──── Per-structure cycle generation (returns normalized DataFrame) ──────

def _cycles_for_bull_put(ticker: str) -> pd.DataFrame:
    """Returns DataFrame with: entry_date, variant (OTM/ATM/ITM), pnl_per_share.
    Uses managed-50% exit P&L (mgd50_pnl) — matches live exit rule."""
    from bull_put_moneyness_backtest import simulate_ticker
    rows = simulate_ticker(ticker)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return pd.DataFrame({
        "entry_date": pd.to_datetime(df["entry_date"]),
        "variant": df["moneyness"],
        "pnl_per_share": df["mgd50_pnl"].astype(float),
    })


def _cycles_for_bear_call(ticker: str) -> pd.DataFrame:
    from bear_call_moneyness_backtest import simulate_ticker
    rows = simulate_ticker(ticker)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return pd.DataFrame({
        "entry_date": pd.to_datetime(df["entry_date"]),
        "variant": df["moneyness"],
        "pnl_per_share": df["mgd50_pnl"].astype(float),
    })


def _cycles_for_inverted_fly(ticker: str) -> pd.DataFrame:
    from inverted_fly_wing_backtest import simulate_ticker
    rows = simulate_ticker(ticker)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # IF stores variant under 'wing_variant' (e.g. wide_10pct) and the
    # 50%-managed PnL under 'mgd50_pnl' as well (managed-only by spec).
    pnl_col = "mgd50_pnl" if "mgd50_pnl" in df.columns else (
        "pnl" if "pnl" in df.columns else df.columns[df.columns.str.contains("pnl")][0]
    )
    variant_col = ("wing_variant" if "wing_variant" in df.columns
                   else ("variant" if "variant" in df.columns else "wing"))
    return pd.DataFrame({
        "entry_date": pd.to_datetime(df["entry_date"]),
        "variant": df[variant_col],
        "pnl_per_share": df[pnl_col].astype(float),
    })


def _cycles_for_zebra(ticker: str) -> pd.DataFrame:
    """Returns DataFrame with: entry_date, variant ('default'),
    pnl_per_share (= pnl_zebra), capture_ratio, pnl_stock,
    capital_efficiency, flat_day_mean_mtm_change.

    The last two are needed by the 5-gate ZEBRA promotion logic added 2026-05-20
    to match zebra_universe_expansion_backtest.py. Uses slip=0.50 only."""
    from zebra_backtest import simulate_ticker
    summaries, _ = simulate_ticker(ticker)
    if not summaries:
        return pd.DataFrame()
    df = pd.DataFrame(summaries)
    df = df[df["slip"] == 0.50].copy()
    if df.empty:
        return pd.DataFrame()
    return pd.DataFrame({
        "entry_date": pd.to_datetime(df["entry_date"]),
        "variant": "default",
        "pnl_per_share": df["pnl_zebra"].astype(float),
        "capture_ratio": df["capture_ratio"].astype(float),
        "pnl_stock": df["pnl_stock"].astype(float),
        "capital_efficiency": df["capital_efficiency"].astype(float),
        "flat_day_mean_mtm_change": df["flat_day_mean_mtm_change"].astype(float),
    })


_CYCLE_DISPATCH = {
    "bull_put": _cycles_for_bull_put,
    "bear_call": _cycles_for_bear_call,
    "inverted_fly": _cycles_for_inverted_fly,
    "zebra": _cycles_for_zebra,
}


# ──── Walk-forward statistics ────────────────────────────────────────────

def _slice_window(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    return df[(df["entry_date"] >= s) & (df["entry_date"] <= e)]


def _wilcoxon_against_zero(series: pd.Series) -> float:
    """One-sample Wilcoxon signed-rank vs zero. Returns p-value or NaN."""
    s = series.dropna()
    if len(s) < 5:
        return float("nan")
    # All-zero series → no signal
    if (s == 0).all():
        return float("nan")
    try:
        _, p = stats.wilcoxon(s.to_numpy())
        return float(p)
    except ValueError:
        return float("nan")


def _split_stats_verticals_or_if(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    structure: str,
) -> dict:
    """For bull_put / bear_call / inverted_fly: pick best variant by train
    mean; report val mean (×100 for $/contract) + val_n + val p."""
    if train_df.empty or val_df.empty:
        return {
            "best_variant": None,
            "mean_pnl": float("nan"),
            "val_n": 0,
            "train_n": 0,
            "val_p": float("nan"),
        }
    train_means = train_df.groupby("variant")["pnl_per_share"].mean()
    if train_means.empty or train_means.isna().all():
        return {
            "best_variant": None,
            "mean_pnl": float("nan"),
            "val_n": 0,
            "train_n": 0,
            "val_p": float("nan"),
        }
    best_variant = train_means.idxmax()
    val_sub = val_df[val_df["variant"] == best_variant]
    train_sub = train_df[train_df["variant"] == best_variant]
    val_pnl = val_sub["pnl_per_share"].astype(float)
    val_mean_per_share = float(val_pnl.mean()) if len(val_pnl) else float("nan")
    val_n = int(len(val_sub))
    train_n = int(len(train_sub))
    val_p = _wilcoxon_against_zero(val_pnl) if val_n >= 5 else float("nan")
    # Convert per-share → per-contract
    mean_pnl_per_contract = (val_mean_per_share * 100.0
                              if val_mean_per_share == val_mean_per_share
                              else float("nan"))
    return {
        "best_variant": best_variant,
        "mean_pnl": mean_pnl_per_contract,
        "val_n": val_n,
        "train_n": train_n,
        "val_p": val_p,
    }


def _split_stats_zebra(train_df: pd.DataFrame, val_df: pd.DataFrame) -> dict:
    """ZEBRA: emit per-split metrics for the 5-gate ZEBRA promotion logic.

    Mirrors zebra_universe_expansion_backtest.py:
      - mean_pnl                  : val median capture × 100 (legacy field; H3 metric)
      - train_median_capture_pct  : train median capture × 100 (upside cycles)
      - train_mean_zebra          : mean per-share pnl_zebra over ALL train cycles
      - val_mean_zebra            : mean per-share pnl_zebra over ALL val cycles
      - val_cap_efficiency        : median capital_efficiency over ALL val cycles
      - val_flat_day_mtm          : mean flat_day_mean_mtm_change over val cycles
      - val_n / train_n           : cycle counts (all cycles, not upside-only)
    """
    nan = float("nan")
    empty = {
        "best_variant": "default", "mean_pnl": nan,
        "val_n": 0, "train_n": 0, "val_p": nan,
        "train_median_capture_pct": nan,
        "train_mean_zebra": nan, "val_mean_zebra": nan,
        "val_cap_efficiency": nan, "val_flat_day_mtm": nan,
    }
    if train_df.empty or val_df.empty:
        return empty
    train_up = train_df[train_df["pnl_stock"] > 0]
    val_up = val_df[val_df["pnl_stock"] > 0]
    val_n_up = int(len(val_up))
    train_n_total = int(len(train_df))
    val_n_total = int(len(val_df))
    if val_n_up == 0:
        e = empty.copy()
        e["train_n"] = train_n_total
        e["val_n"] = val_n_total
        return e
    val_median_capture = float(val_up["capture_ratio"].median())
    train_median_capture = (float(train_up["capture_ratio"].median())
                             if len(train_up) else nan)
    # mean_zebra uses ALL cycles (not upside-only) to match live script's
    # mean_zebra_train / mean_zebra_val convention.
    train_mean_zebra = float(train_df["pnl_per_share"].mean())
    val_mean_zebra = float(val_df["pnl_per_share"].mean())
    val_cap_eff = float(val_df["capital_efficiency"].median())
    flat_series = val_df["flat_day_mean_mtm_change"].dropna()
    val_flat_mtm = float(flat_series.mean()) if len(flat_series) else nan
    # p-value: Wilcoxon on capture_ratio − 1.0 over upside cycles in val
    diffs = val_up["capture_ratio"] - 1.0
    val_p = _wilcoxon_against_zero(diffs) if val_n_up >= 5 else nan
    return {
        "best_variant": "default",
        "mean_pnl": val_median_capture * 100.0,
        "val_n": val_n_total,
        "train_n": train_n_total,
        "val_p": val_p,
        "train_median_capture_pct": (train_median_capture * 100.0
                                       if pd.notna(train_median_capture)
                                       else nan),
        "train_mean_zebra": train_mean_zebra,
        "val_mean_zebra": val_mean_zebra,
        "val_cap_efficiency": val_cap_eff,
        "val_flat_day_mtm": val_flat_mtm,
    }


def _per_year_pnl(df: pd.DataFrame) -> dict[int, float]:
    """For Gate D concentration cap: aggregate per-share P/L by entry year."""
    if df.empty:
        return {}
    df2 = df.copy()
    df2["year"] = df2["entry_date"].dt.year
    return df2.groupby("year")["pnl_per_share"].sum().to_dict()


# ──── Top-level entry point ──────────────────────────────────────────────

def run_walkforward(
    ticker: str,
    structure: str,
    splits: list[tuple[str, str, str, str]] | None = None,
) -> dict:
    """Run a per-ticker per-structure 4-split walk-forward.

    Returns a dict with:
      ticker, structure, status, error
      walkforward_rows: DataFrame[split, mean_pnl, val_n, train_n, val_p, best_variant]
      per_year_pnl: dict[year, sum_per_share_pnl]
      most_recent_p: float (val_p of the latest split — for BH-FDR Gate E)
      cycle_count: int
    """
    if structure not in STRUCTURES:
        raise ValueError(f"unknown structure: {structure}")
    splits = splits or DEFAULT_SPLITS

    result = {
        "ticker": ticker,
        "structure": structure,
        "status": "ok",
        "error": None,
        "walkforward_rows": pd.DataFrame(),
        "per_year_pnl": {},
        "most_recent_p": float("nan"),
        "cycle_count": 0,
    }

    try:
        cycles = _CYCLE_DISPATCH[structure](ticker)
    except FileNotFoundError as e:
        result["status"] = "no_data"
        result["error"] = str(e)
        return result
    except Exception as e:
        log.exception("simulate_ticker failed for %s/%s", ticker, structure)
        result["status"] = "error"
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    if cycles.empty:
        result["status"] = "no_data"
        return result

    result["cycle_count"] = int(len(cycles))
    result["per_year_pnl"] = _per_year_pnl(cycles)

    rows = []
    for i, (tr_s, tr_e, val_s, val_e) in enumerate(splits, start=1):
        train_df = _slice_window(cycles, tr_s, tr_e)
        val_df = _slice_window(cycles, val_s, val_e)
        if structure == "zebra":
            stats_d = _split_stats_zebra(train_df, val_df)
        else:
            stats_d = _split_stats_verticals_or_if(train_df, val_df, structure)
        rows.append({
            "split": i,
            "train_window": f"{tr_s}..{tr_e}",
            "val_window": f"{val_s}..{val_e}",
            **stats_d,
        })
    wf_df = pd.DataFrame(rows)
    # Stamp the ticker's lifetime cycle count on every row so structure-aware
    # gate evaluators (e.g. ZEBRA n_total ≥ 50) can read it from the DataFrame
    # without changing the existing (wf_rows, structure) gate signature.
    if len(wf_df):
        wf_df["total_cycles"] = result["cycle_count"]
    result["walkforward_rows"] = wf_df
    if len(wf_df):
        # Use the most-recent split (highest split number)
        most_recent = wf_df.sort_values("split").iloc[-1]
        result["most_recent_p"] = float(most_recent["val_p"]) if pd.notna(
            most_recent["val_p"]) else float("nan")
    return result


def run_batch(
    tickers: Iterable[str],
    structures: Iterable[str] = STRUCTURES,
    splits: list[tuple[str, str, str, str]] | None = None,
    progress: bool = True,
) -> list[dict]:
    """Run walk-forward for each (ticker, structure) cross-product.
    Returns a list of result dicts. Failures are captured per-row, not raised."""
    out = []
    tickers = list(tickers)
    structures = list(structures)
    total = len(tickers) * len(structures)
    i = 0
    for t in tickers:
        for s in structures:
            i += 1
            if progress and (i % 25 == 0 or i == total):
                log.info("[%d/%d] %s/%s", i, total, t, s)
            out.append(run_walkforward(t, s, splits=splits))
    return out


if __name__ == "__main__":
    # CLI for ad-hoc smoke testing: python3.11 -m lib.walkforward_runner SPY bull_put
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("structure", choices=STRUCTURES)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run_walkforward(args.ticker, args.structure)
    print(f"ticker={res['ticker']}  structure={res['structure']}  "
          f"status={res['status']}  cycles={res['cycle_count']}")
    if res["error"]:
        print(f"  error: {res['error']}")
    if not res["walkforward_rows"].empty:
        print(res["walkforward_rows"].to_string(index=False))
    print(f"  per-year P/L (per-share): {res['per_year_pnl']}")
    print(f"  most-recent split p: {res['most_recent_p']}")
