#!/usr/bin/env python3.11
"""
v2: per-name extension of the 200dma + IV/HV divergence study.

Iterates the 150-symbol universe, computes per-ticker daily signals from raw
ORATS option-chain parquets, and produces a per-ticker stats table.

Key per-name metric: directional asymmetry index = (sig P_neg5 / sig P_pos5) /
(base P_neg5 / base P_pos5) on fwd 25-day returns.
  > 1.5 — signal is directionally bearish (bear_call candidate)
  ≈ 1.0 — signal is magnitude-only, like SPY (inverted_fly candidate)
  < 0.7 — signal predicts mean-reversion bounce (bull_put candidate)

Output:
  data/profile/signal_200dma_ivhv_divergence_per_ticker.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
UNIVERSE_PATH = ROOT / "data/profile/universe_v1.parquet"
ORATS_DIR = ROOT / "data/orats/by_ticker"
OUTPUT_PARQUET = ROOT / "data/profile/signal_200dma_ivhv_divergence_per_ticker.parquet"

NEEDED_COLS = ["trade_date", "expirDate", "stkPx", "delta", "cMidIv"]
HORIZONS = [5, 10, 25, 45]
MIN_SIGNAL_DAYS = 20
MIN_TOTAL_DAYS = 250


def build_daily_signals_per_ticker(df: pd.DataFrame) -> pd.DataFrame | None:
    """Collapse raw chain rows to one row per trade_date with spot, ATM-IV30,
    HV20, MA200, slopes, signal flags, and forward returns."""
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["expirDate"] = pd.to_datetime(df["expirDate"])
    df["dte"] = (df["expirDate"] - df["trade_date"]).dt.days

    near = df[(df["dte"] >= 20) & (df["dte"] <= 50) & df["delta"].notna()
              & df["cMidIv"].notna() & (df["cMidIv"] > 0)].copy()
    if len(near) == 0:
        return None

    near["delta_abs"] = (near["delta"] - 0.5).abs()
    idx = near.groupby(["trade_date", "expirDate"])["delta_abs"].idxmin()
    atm_per_exp = near.loc[idx, ["trade_date", "expirDate", "dte", "stkPx",
                                  "cMidIv"]].copy()
    atm_per_exp["dte_dist"] = (atm_per_exp["dte"] - 30).abs()
    idx2 = atm_per_exp.groupby("trade_date")["dte_dist"].idxmin()
    daily = atm_per_exp.loc[idx2, ["trade_date", "stkPx", "cMidIv", "dte"]]
    daily = daily.rename(columns={"cMidIv": "atm_iv30", "stkPx": "spot"})
    daily = daily.sort_values("trade_date").reset_index(drop=True)

    daily["log_ret"] = np.log(daily["spot"] / daily["spot"].shift(1))
    daily["hv20"] = daily["log_ret"].rolling(20).std() * np.sqrt(252)

    daily["ma200"] = daily["spot"].rolling(200, min_periods=100).mean()
    daily["below_200dma"] = daily["spot"] < daily["ma200"]

    daily["iv_slope_5d"] = daily["atm_iv30"].diff(5)
    daily["hv_slope_5d"] = daily["hv20"].diff(5)

    for h in HORIZONS:
        daily[f"fwd_{h}d"] = daily["spot"].shift(-h) / daily["spot"] - 1.0

    daily["sig_strict"] = (
        daily["below_200dma"]
        & (daily["iv_slope_5d"] >= 0.005)
        & (daily["hv_slope_5d"] < 0.005)
    )

    return daily


def per_ticker_stats(ticker: str, atm: pd.DataFrame) -> dict | None:
    sub = atm[atm["sig_strict"]].dropna(subset=["fwd_25d"])
    base = atm.dropna(subset=["fwd_25d"])

    if len(sub) < MIN_SIGNAL_DAYS:
        return None

    out: dict = {
        "ticker": ticker,
        "n_total": int(len(atm)),
        "n_signal_days": int(len(sub)),
        "n_baseline": int(len(base)),
        "first_date": atm["trade_date"].min().date().isoformat(),
        "last_date": atm["trade_date"].max().date().isoformat(),
    }

    for h in HORIZONS:
        s = sub[f"fwd_{h}d"].dropna()
        b = base[f"fwd_{h}d"].dropna()
        if len(s) == 0 or len(b) == 0:
            continue
        out[f"sig_mean_{h}d"] = float(s.mean())
        out[f"base_mean_{h}d"] = float(b.mean())
        out[f"sig_p_neg_{h}d"] = float((s < 0).mean())
        out[f"sig_p_neg5_{h}d"] = float((s < -0.05).mean())
        out[f"sig_p_pos5_{h}d"] = float((s > 0.05).mean())
        out[f"sig_p_abs5_{h}d"] = float((s.abs() > 0.05).mean())
        out[f"base_p_neg_{h}d"] = float((b < 0).mean())
        out[f"base_p_neg5_{h}d"] = float((b < -0.05).mean())
        out[f"base_p_pos5_{h}d"] = float((b > 0.05).mean())
        out[f"base_p_abs5_{h}d"] = float((b.abs() > 0.05).mean())

    p_neg_s = out.get("sig_p_neg5_25d")
    p_pos_s = out.get("sig_p_pos5_25d")
    p_neg_b = out.get("base_p_neg5_25d")
    p_pos_b = out.get("base_p_pos5_25d")
    if (p_neg_s is not None and p_pos_s and p_pos_s > 0
            and p_neg_b is not None and p_pos_b and p_pos_b > 0):
        out["dir_asym_index"] = (p_neg_s / p_pos_s) / (p_neg_b / p_pos_b)
    else:
        out["dir_asym_index"] = None

    if "sig_p_abs5_25d" in out and "base_p_abs5_25d" in out:
        out["mag_lift_25d"] = out["sig_p_abs5_25d"] - out["base_p_abs5_25d"]
        out["mag_lift_ratio_25d"] = (out["sig_p_abs5_25d"] / out["base_p_abs5_25d"]
                                     if out["base_p_abs5_25d"] > 0 else None)
    if "sig_mean_25d" in out and "base_mean_25d" in out:
        out["mean_lift_25d"] = out["sig_mean_25d"] - out["base_mean_25d"]

    return out


def classify_directional(asym: float | None) -> str:
    if asym is None:
        return "n/a"
    if asym >= 1.5:
        return "BEARISH"
    if asym <= 0.7:
        return "BULLISH"
    return "MAGNITUDE"


def main():
    print("=" * 110)
    print("  200dma + IV/HV divergence directional study — per-ticker (v2)")
    print("=" * 110)

    universe = pd.read_parquet(UNIVERSE_PATH)
    universe_meta = universe[["ticker", "cluster", "sector", "cap_tier"]]\
        .set_index("ticker").to_dict("index")
    tickers = universe["ticker"].tolist()

    rows: list[dict] = []
    skipped: dict[str, int] = {"no_file": 0, "no_data": 0,
                                "too_few_days": 0, "too_few_signals": 0,
                                "load_error": 0}
    print(f"  Universe: {len(tickers)} tickers")
    print(f"  Min signal days: {MIN_SIGNAL_DAYS}, "
          f"Min total days: {MIN_TOTAL_DAYS}")
    print()

    for i, ticker in enumerate(tickers, 1):
        path = ORATS_DIR / f"{ticker}.parquet"
        if not path.exists():
            skipped["no_file"] += 1
            continue
        try:
            df = pd.read_parquet(path, columns=NEEDED_COLS)
        except Exception as e:
            skipped["load_error"] += 1
            print(f"  [{i:3d}/{len(tickers)}] {ticker:6s}  LOAD ERROR: {e}")
            continue

        atm = build_daily_signals_per_ticker(df)
        if atm is None:
            skipped["no_data"] += 1
            continue
        if len(atm) < MIN_TOTAL_DAYS:
            skipped["too_few_days"] += 1
            continue

        stats = per_ticker_stats(ticker, atm)
        if stats is None:
            skipped["too_few_signals"] += 1
            continue

        meta = universe_meta.get(ticker, {})
        stats["cluster"] = meta.get("cluster")
        stats["sector"] = meta.get("sector")
        stats["cap_tier"] = meta.get("cap_tier")
        stats["direction_label"] = classify_directional(stats.get("dir_asym_index"))
        rows.append(stats)

        if i % 10 == 0 or i == len(tickers):
            asym = stats.get("dir_asym_index")
            asym_s = f"{asym:.2f}" if asym is not None else " n/a"
            print(f"  [{i:3d}/{len(tickers)}] {ticker:6s}  "
                  f"N_sig={stats['n_signal_days']:3d}  "
                  f"asym={asym_s}  "
                  f"label={stats['direction_label']}")

    print()
    print(f"  Tickers processed: {len(rows)}")
    print(f"  Skipped: {skipped}")

    if not rows:
        print("  No usable tickers — exiting.")
        return 1

    df_out = pd.DataFrame(rows)
    df_out.to_parquet(OUTPUT_PARQUET, index=False)
    print(f"\n  Saved per-ticker stats to {OUTPUT_PARQUET}")
    print(f"  Rows: {len(df_out)}")

    print_summary_tables(df_out)
    return 0


def print_summary_tables(df: pd.DataFrame):
    print("\n" + "=" * 110)
    print("  Top 15 BEARISH (signal predicts more downside than upside)")
    print("=" * 110)
    sortable = df[df["dir_asym_index"].notna()].copy()
    bear = sortable.sort_values("dir_asym_index", ascending=False).head(15)
    _print_top(bear)

    print("\n" + "=" * 110)
    print("  Top 15 BULLISH (signal predicts more upside than downside — mean reversion)")
    print("=" * 110)
    bull = sortable.sort_values("dir_asym_index", ascending=True).head(15)
    _print_top(bull)

    print("\n" + "=" * 110)
    print("  Top 15 MAGNITUDE (asym near 1.0, signal predicts big moves either way)")
    print("=" * 110)
    sortable["asym_dist"] = (sortable["dir_asym_index"] - 1.0).abs()
    mag = sortable.sort_values(
        ["asym_dist", "mag_lift_25d"], ascending=[True, False]).head(15)
    _print_top(mag)

    print("\n" + "=" * 110)
    print("  Distribution by direction_label")
    print("=" * 110)
    print(df["direction_label"].value_counts().to_string())

    print("\n" + "=" * 110)
    print("  Distribution by cluster × direction_label")
    print("=" * 110)
    if df["cluster"].notna().any():
        ct = pd.crosstab(df["cluster"], df["direction_label"])
        print(ct.to_string())


def _print_top(df: pd.DataFrame):
    cols = ["ticker", "cluster", "sector", "cap_tier", "n_signal_days",
            "dir_asym_index", "sig_mean_25d", "base_mean_25d", "mean_lift_25d",
            "sig_p_neg5_25d", "sig_p_pos5_25d", "sig_p_abs5_25d",
            "mag_lift_25d", "direction_label"]
    sub = df[[c for c in cols if c in df.columns]].copy()
    for c in ["sig_mean_25d", "base_mean_25d", "mean_lift_25d",
              "sig_p_neg5_25d", "sig_p_pos5_25d", "sig_p_abs5_25d",
              "mag_lift_25d"]:
        if c in sub.columns:
            sub[c] = sub[c].apply(lambda x: f"{x*100:+5.1f}%" if pd.notna(x) else "  n/a")
    if "dir_asym_index" in sub.columns:
        sub["dir_asym_index"] = sub["dir_asym_index"].apply(
            lambda x: f"{x:5.2f}" if pd.notna(x) else " n/a")
    if "sector" in sub.columns:
        sub["sector"] = sub["sector"].fillna("").apply(
            lambda s: s[:18] if isinstance(s, str) else "")
    print(sub.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
