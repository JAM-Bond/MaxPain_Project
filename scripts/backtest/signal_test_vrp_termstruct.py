"""Test two magnitude-signals on SPY history:

1. VRP compression (IV - RV20): when implied vol is close to or below realized,
   the market is underpricing vol and big realized moves tend to follow.
2. IV term structure inversion (front IV > back IV): market pricing imminent vol
   above deferred vol — often precedes volatility expansion.

Bucket each signal into quintiles; measure subsequent 21d and 45d absolute SPY returns
and the fraction of days where the move exceeded 5% / 10%.

Output: data/profile/signal_vrp_termstruct_spy.parquet + console tables.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
OUT_DIR = ROOT / "data/profile"


def build_term_structure(spy: pd.DataFrame) -> pd.DataFrame:
    """Per trade_date, return front_iv (20-40 DTE ATM) and back_iv (60-90 DTE ATM)."""
    spy = spy.copy()
    spy["trade_date"] = pd.to_datetime(spy["trade_date"])
    # Parse expirDate (format M/D/YYYY)
    def parse_exp(s):
        try:
            p = s.split("/")
            return pd.Timestamp(year=int(p[2]), month=int(p[0]), day=int(p[1]))
        except Exception:
            return pd.NaT
    spy["exp_dt"] = spy["expirDate"].map(parse_exp)
    spy = spy.dropna(subset=["exp_dt", "delta", "cMidIv", "pMidIv", "stkPx"])
    spy["dte"] = (spy["exp_dt"] - spy["trade_date"]).dt.days
    spy = spy[(spy["dte"] > 0) & (spy["dte"] < 120)]
    spy["strike_dist"] = (spy["strike"] - spy["stkPx"]).abs()

    # For each (trade_date, expirDate), pick the ATM strike
    idx = spy.groupby(["trade_date", "expirDate"])["strike_dist"].idxmin()
    atm = spy.loc[idx].copy()
    atm["atm_iv"] = (atm["cMidIv"] + atm["pMidIv"]) / 2.0

    rows = []
    for d, g in atm.groupby("trade_date"):
        front = g[(g["dte"] >= 20) & (g["dte"] <= 40)]
        back  = g[(g["dte"] >= 60) & (g["dte"] <= 90)]
        if front.empty or back.empty:
            continue
        # pick the expirDate in each bucket with DTE closest to the bucket midpoint
        fr_row = front.iloc[(front["dte"] - 30).abs().argsort()[:1]].iloc[0]
        bk_row = back.iloc[(back["dte"] - 75).abs().argsort()[:1]].iloc[0]
        rows.append({
            "trade_date": d,
            "front_iv": fr_row["atm_iv"], "front_dte": int(fr_row["dte"]),
            "back_iv":  bk_row["atm_iv"], "back_dte": int(bk_row["dte"]),
            "term_spread": fr_row["atm_iv"] - bk_row["atm_iv"],
            "spot": float(fr_row["stkPx"]),
        })
    return pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)


def bucket_analysis(df: pd.DataFrame, signal_col: str, label: str) -> pd.DataFrame:
    d = df.dropna(subset=[signal_col, "fwd_21d_abs", "fwd_45d_abs"]).copy()
    d["quintile"] = pd.qcut(d[signal_col], 5, labels=["Q1 (low)", "Q2", "Q3", "Q4", "Q5 (high)"])
    out = d.groupby("quintile", observed=True).agg(
        n=(signal_col, "count"),
        signal_mean=(signal_col, "mean"),
        fwd_21d_mean_abs=("fwd_21d_abs", "mean"),
        fwd_21d_ge_5=("fwd_21d_abs", lambda s: (s >= 0.05).mean()),
        fwd_21d_ge_10=("fwd_21d_abs", lambda s: (s >= 0.10).mean()),
        fwd_45d_mean_abs=("fwd_45d_abs", "mean"),
        fwd_45d_ge_5=("fwd_45d_abs", lambda s: (s >= 0.05).mean()),
        fwd_45d_ge_10=("fwd_45d_abs", lambda s: (s >= 0.10).mean()),
    ).reset_index()
    out["signal"] = label
    return out


def main() -> None:
    # VRP series (precomputed)
    vrp = pd.read_parquet(OUT_DIR / "vrp_series.parquet")
    spy_vrp = vrp[vrp["ticker"] == "SPY"][["trade_date", "atm_iv", "hv20", "vrp", "iv_rank", "iv_hv_ratio"]].copy()
    spy_vrp["trade_date"] = pd.to_datetime(spy_vrp["trade_date"])

    # Term structure (compute from raw ORATS)
    spy_raw = pd.read_parquet(ROOT / "data/orats/by_ticker/SPY.parquet",
                              columns=["trade_date","expirDate","strike","delta","cMidIv","pMidIv","stkPx"])
    term = build_term_structure(spy_raw)

    # Daily spot + forward returns
    px = spy_raw.groupby("trade_date")["stkPx"].first().dropna().sort_index()
    px.index = pd.to_datetime(px.index)
    fwd = pd.DataFrame({"trade_date": px.index, "spot": px.values})
    fwd["fwd_21d"] = px.pct_change(21).shift(-21).values
    fwd["fwd_45d"] = px.pct_change(45).shift(-45).values
    fwd["fwd_21d_abs"] = fwd["fwd_21d"].abs()
    fwd["fwd_45d_abs"] = fwd["fwd_45d"].abs()

    merged = fwd.merge(spy_vrp, on="trade_date", how="left").merge(term, on="trade_date", how="left")
    print(f"Merged SPY series: {len(merged):,} days  |  {merged['trade_date'].min().date()} → {merged['trade_date'].max().date()}")
    print(f"  rows with VRP: {merged['vrp'].notna().sum()}  |  with term structure: {merged['term_spread'].notna().sum()}")
    print()

    # ─── Signal 1: VRP compression ───
    # Lower VRP = IV cheap relative to recent RV = market underpricing vol
    print("═══ Signal 1: VRP (IV - RV20) quintile buckets ═══")
    vrp_tbl = bucket_analysis(merged, "vrp", "vrp")
    print(vrp_tbl.to_string(index=False, float_format=lambda x: f"{x:+.4f}" if isinstance(x, float) else str(x)))
    print()

    # ─── Signal 2: IV term structure ───
    # Positive term_spread = front > back = inversion = market expecting near-term vol
    print("═══ Signal 2: IV term structure (front_iv − back_iv) quintile buckets ═══")
    ts_tbl = bucket_analysis(merged, "term_spread", "term_spread")
    print(ts_tbl.to_string(index=False, float_format=lambda x: f"{x:+.4f}" if isinstance(x, float) else str(x)))
    print()

    # ─── Cross-reference: extreme signal days ───
    print("═══ Extreme VRP days (bottom 10%): what happened next 45 days? ═══")
    low_vrp = merged.dropna(subset=["vrp", "fwd_45d_abs"])
    threshold = low_vrp["vrp"].quantile(0.10)
    extreme = low_vrp[low_vrp["vrp"] <= threshold]
    print(f"  threshold: VRP ≤ {threshold:+.4f}  |  N = {len(extreme)} days")
    print(f"  mean fwd 45d |move|: {extreme['fwd_45d_abs'].mean():.1%}")
    print(f"  fraction with ≥5% move: {(extreme['fwd_45d_abs'] >= 0.05).mean():.1%}")
    print(f"  fraction with ≥10% move: {(extreme['fwd_45d_abs'] >= 0.10).mean():.1%}")
    print()
    baseline = merged.dropna(subset=["fwd_45d_abs"])
    print(f"  baseline (all days):      mean {baseline['fwd_45d_abs'].mean():.1%}  |  ≥5%: {(baseline['fwd_45d_abs']>=0.05).mean():.1%}  |  ≥10%: {(baseline['fwd_45d_abs']>=0.10).mean():.1%}")
    print()

    print("═══ Inverted term structure days (positive spread): what happened next? ═══")
    inv = merged.dropna(subset=["term_spread", "fwd_45d_abs"])
    inverted = inv[inv["term_spread"] > 0]
    print(f"  N = {len(inverted)} inverted days  ({len(inverted)/len(inv):.1%} of sample)")
    print(f"  mean fwd 45d |move|: {inverted['fwd_45d_abs'].mean():.1%}")
    print(f"  fraction with ≥5% move: {(inverted['fwd_45d_abs'] >= 0.05).mean():.1%}")
    print(f"  fraction with ≥10% move: {(inverted['fwd_45d_abs'] >= 0.10).mean():.1%}")
    print(f"  fraction with ≥15% move: {(inverted['fwd_45d_abs'] >= 0.15).mean():.1%}")
    print()
    baseline = inv.dropna(subset=["fwd_45d_abs"])
    print(f"  baseline (all days with term data):      mean {baseline['fwd_45d_abs'].mean():.1%}  |  ≥5%: {(baseline['fwd_45d_abs']>=0.05).mean():.1%}  |  ≥10%: {(baseline['fwd_45d_abs']>=0.10).mean():.1%}  |  ≥15%: {(baseline['fwd_45d_abs']>=0.15).mean():.1%}")
    print()

    # Joint signal: BOTH VRP in lowest quintile AND term structure inverted
    joint = merged.dropna(subset=["vrp", "term_spread", "fwd_45d_abs"])
    joint["vrp_low"] = joint["vrp"] <= joint["vrp"].quantile(0.20)
    joint["ts_inv"] = joint["term_spread"] > 0
    both = joint[joint["vrp_low"] & joint["ts_inv"]]
    print(f"═══ Joint signal: VRP bottom quintile AND term structure inverted ═══")
    print(f"  N = {len(both)} days ({len(both)/len(joint):.1%} of sample)")
    if len(both) > 0:
        print(f"  mean fwd 45d |move|: {both['fwd_45d_abs'].mean():.1%}")
        print(f"  fraction with ≥5%: {(both['fwd_45d_abs']>=0.05).mean():.1%}")
        print(f"  fraction with ≥10%: {(both['fwd_45d_abs']>=0.10).mean():.1%}")
        print(f"  fraction with ≥15%: {(both['fwd_45d_abs']>=0.15).mean():.1%}")

    # Save artifact
    merged.to_parquet(OUT_DIR / "signal_vrp_termstruct_spy.parquet", index=False)
    vrp_tbl.to_parquet(OUT_DIR / "signal_vrp_spy_quintiles.parquet", index=False)
    ts_tbl.to_parquet(OUT_DIR / "signal_termstruct_spy_quintiles.parquet", index=False)
    print()
    print("wrote: data/profile/signal_vrp_termstruct_spy.parquet + quintile tables")


if __name__ == "__main__":
    main()
