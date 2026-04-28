#!/usr/bin/env python3.11
"""
Study: does the combined signal "spot < 200dma AND IV/HV widening" predict
directional bias on forward returns?

v1: SPY-level test on 13-year ORATS history.

Hypothesis: signal-firing days (bearish trend + market pricing future event the
realized data hasn't confirmed) bias toward NEGATIVE forward returns.

Output: terminal table + parquet to data/profile/signal_200dma_ivhv_divergence_spy.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
SIGNAL_PARQUET = ROOT / "data/profile/signal_vrp_termstruct_spy.parquet"
SPY_PARQUET = ROOT / "data/orats/by_ticker/SPY.parquet"
OUTPUT_PARQUET = ROOT / "data/profile/signal_200dma_ivhv_divergence_spy.parquet"


def load_data() -> pd.DataFrame:
    """Load existing SPY signal parquet + augment with 200dma and slopes."""
    df = pd.read_parquet(SIGNAL_PARQUET)
    df = df[["trade_date", "spot_x", "atm_iv", "hv20", "vrp"]].rename(
        columns={"spot_x": "spot"}).sort_values("trade_date").reset_index(drop=True)

    df["ma200"] = df["spot"].rolling(200, min_periods=100).mean()
    df["pct_to_ma200"] = (df["spot"] / df["ma200"]) - 1.0
    df["below_200dma"] = df["spot"] < df["ma200"]

    # 5-day slopes (today minus 5 days ago)
    df["iv_slope_5d"] = df["atm_iv"].diff(5)
    df["hv_slope_5d"] = df["hv20"].diff(5)
    df["vrp_slope_5d"] = df["vrp"].diff(5)

    # 10-day slopes
    df["iv_slope_10d"] = df["atm_iv"].diff(10)
    df["hv_slope_10d"] = df["hv20"].diff(10)
    df["vrp_slope_10d"] = df["vrp"].diff(10)

    # Forward returns at multiple horizons
    for horizon in [5, 10, 25, 45]:
        df[f"fwd_{horizon}d"] = df["spot"].shift(-horizon) / df["spot"] - 1.0

    return df


def define_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Add boolean signal columns for the candidate definitions."""
    # Definition 1: strict — IV rising clearly AND HV roughly flat/falling
    # IV up by ≥ 0.005 (0.5pp) over 5d, HV by less (or negative)
    df["sig_strict"] = (
        df["below_200dma"]
        & (df["iv_slope_5d"] >= 0.005)
        & (df["hv_slope_5d"] < 0.005)
    )

    # Definition 2: VRP-widening — VRP slope positive (any magnitude)
    df["sig_vrp_widening"] = (
        df["below_200dma"]
        & (df["vrp_slope_5d"] > 0)
    )

    # Definition 3: VRP-widening fast — VRP slope ≥ 0.005 over 10d
    df["sig_vrp_widening_fast"] = (
        df["below_200dma"]
        & (df["vrp_slope_10d"] >= 0.005)
    )

    # Definition 4: control — below 200dma alone (baseline trend signal)
    df["sig_below_200dma_only"] = df["below_200dma"]

    return df


def directional_summary(label: str, df: pd.DataFrame, mask: pd.Series) -> dict:
    """Compute directional bias stats for the masked subset vs the full df."""
    sub = df[mask].dropna(subset=["fwd_25d"])
    base = df.dropna(subset=["fwd_25d"])

    if len(sub) == 0:
        return {"label": label, "n": 0}

    out = {"label": label, "n": len(sub), "n_baseline": len(base)}
    for horizon in [5, 10, 25, 45]:
        col = f"fwd_{horizon}d"
        s = sub[col].dropna()
        b = base[col].dropna()
        if len(s) == 0:
            continue
        out[f"sig_mean_{horizon}d"] = s.mean()
        out[f"base_mean_{horizon}d"] = b.mean()
        out[f"sig_p_neg_{horizon}d"] = (s < 0).mean()
        out[f"base_p_neg_{horizon}d"] = (b < 0).mean()
        out[f"sig_p_neg5_{horizon}d"] = (s < -0.05).mean()
        out[f"base_p_neg5_{horizon}d"] = (b < -0.05).mean()
        out[f"sig_p_pos5_{horizon}d"] = (s > 0.05).mean()
        out[f"base_p_pos5_{horizon}d"] = (b > 0.05).mean()
    return out


def fmt_pct(x):
    return f"{x*100:6.2f}%" if pd.notna(x) else "    n/a"


def fmt_pct_diff(s, b):
    if pd.notna(s) and pd.notna(b):
        d = (s - b) * 100
        return f"{d:+5.2f}pp"
    return "    n/a"


def print_summary(rows: list[dict]):
    """Pretty-print the directional summary table."""
    for r in rows:
        if r.get("n", 0) == 0:
            print(f"\n  [{r['label']}] — N=0 (no firing days)")
            continue
        print(f"\n  [{r['label']}]  N={r['n']:5d}  "
              f"(baseline N={r['n_baseline']})")
        print(f"  {'horizon':<10} {'sig_mean':>9} {'base_mean':>9} {'lift':>8}   "
              f"{'sig_P(<0)':>10} {'base_P(<0)':>10} {'diff':>8}   "
              f"{'sig_P(<-5%)':>11} {'base_P(<-5%)':>12} {'diff':>8}   "
              f"{'sig_P(>+5%)':>11} {'base_P(>+5%)':>12} {'diff':>8}")
        for horizon in [5, 10, 25, 45]:
            print(f"  fwd_{horizon}d{'':<4} "
                  f"{fmt_pct(r.get(f'sig_mean_{horizon}d'))} "
                  f"{fmt_pct(r.get(f'base_mean_{horizon}d'))} "
                  f"{fmt_pct_diff(r.get(f'sig_mean_{horizon}d'), r.get(f'base_mean_{horizon}d'))}   "
                  f"{fmt_pct(r.get(f'sig_p_neg_{horizon}d'))} "
                  f"{fmt_pct(r.get(f'base_p_neg_{horizon}d'))} "
                  f"{fmt_pct_diff(r.get(f'sig_p_neg_{horizon}d'), r.get(f'base_p_neg_{horizon}d'))}   "
                  f"{fmt_pct(r.get(f'sig_p_neg5_{horizon}d'))} "
                  f"{fmt_pct(r.get(f'base_p_neg5_{horizon}d'))} "
                  f"{fmt_pct_diff(r.get(f'sig_p_neg5_{horizon}d'), r.get(f'base_p_neg5_{horizon}d'))}   "
                  f"{fmt_pct(r.get(f'sig_p_pos5_{horizon}d'))} "
                  f"{fmt_pct(r.get(f'base_p_pos5_{horizon}d'))} "
                  f"{fmt_pct_diff(r.get(f'sig_p_pos5_{horizon}d'), r.get(f'base_p_pos5_{horizon}d'))}")


def main():
    print("=" * 100)
    print("  200dma + IV/HV divergence directional study — SPY 13-year (v1)")
    print("=" * 100)

    df = load_data()
    df = define_signals(df)
    df_full = df.copy()

    rows = [
        directional_summary("Strict: <200dma AND IV-up AND HV-flat (5d)",
                            df, df["sig_strict"]),
        directional_summary("VRP-widening (5d): <200dma AND VRP slope > 0",
                            df, df["sig_vrp_widening"]),
        directional_summary("VRP-widening fast (10d, >=0.5pp)",
                            df, df["sig_vrp_widening_fast"]),
        directional_summary("Control: <200dma ALONE (baseline trend signal)",
                            df, df["sig_below_200dma_only"]),
    ]
    print_summary(rows)

    # Save full df with signal columns + forward returns to parquet
    df_full.to_parquet(OUTPUT_PARQUET, index=False)
    print(f"\nSaved full daily signal frame to {OUTPUT_PARQUET}")
    print(f"  N rows: {len(df_full)}, "
          f"date range: {df_full['trade_date'].min().date()} → {df_full['trade_date'].max().date()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
