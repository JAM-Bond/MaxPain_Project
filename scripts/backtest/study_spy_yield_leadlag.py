#!/usr/bin/env python3.11
"""
Lead-lag study: when SPY makes a significant move, how long until 10Y yield
moves in the same direction?

Hypothesis: bond market is the smarter pricer. SPY rallies that get
"validated" by yields (rising) within N days are real; rallies where yields
stay stagnant or fall are suspect.

Data:
  - SPY daily closes from ORATS (2013-01-02 to 2026-04-21)
  - DGS10 (10-Year Treasury) from FRED API (full range)

Outputs:
  - data/profile/spy_yield_aligned.parquet (one row per trading day)
  - terminal report: cross-correlation function, conditional validation rates

Reuses Agent_Project's FRED API key from config/api_keys.env.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path.home() / "MaxPain_Project"
SPY_PARQUET = ROOT / "data/orats/by_ticker/SPY.parquet"
OUT_PARQUET = ROOT / "data/profile/spy_yield_aligned.parquet"
FRED_ENV = Path.home() / "Agent_Project/config/api_keys.env"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def load_fred_key() -> str:
    for line in FRED_ENV.read_text().splitlines():
        if line.startswith("FRED_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("FRED_API_KEY not found in Agent_Project env")


def fetch_fred_series(series_id: str, api_key: str,
                     start: str = "2013-01-01") -> pd.DataFrame:
    r = requests.get(FRED_BASE, params={
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "observation_start": start,
    }, timeout=30)
    r.raise_for_status()
    obs = r.json()["observations"]
    df = pd.DataFrame(obs)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])[["date", "value"]]
    df = df.rename(columns={"value": series_id})
    return df


def load_spy_daily() -> pd.DataFrame:
    df = pd.read_parquet(SPY_PARQUET, columns=["trade_date", "stkPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    daily = df.drop_duplicates("trade_date").sort_values("trade_date")
    daily = daily.rename(columns={"trade_date": "date", "stkPx": "spy"})
    daily["spy_ret"] = daily["spy"].pct_change()
    daily["ma200"] = daily["spy"].rolling(200, min_periods=100).mean()
    daily["below_200dma"] = daily["spy"] < daily["ma200"]
    return daily.reset_index(drop=True)


def build_aligned(spy: pd.DataFrame, fred_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    df = spy.copy()
    for sid, sdf in fred_dfs.items():
        df = df.merge(sdf, on="date", how="left")
    # FRED daily series sometimes have NaNs on holidays; forward-fill at most 1 day
    for sid in fred_dfs:
        df[sid] = df[sid].ffill(limit=1)
    df["dgs10_chg"] = df["DGS10"].diff()
    df["dgs2_chg"] = df["DGS2"].diff() if "DGS2" in df.columns else np.nan
    if "DGS2" in df.columns and "DGS10" in df.columns:
        df["spread_2s10s"] = df["DGS10"] - df["DGS2"]
        df["spread_chg"] = df["spread_2s10s"].diff()
    return df


def cross_correlation(df: pd.DataFrame, max_lag: int = 10) -> pd.DataFrame:
    """corr(SPY_ret_t, DGS10_chg_{t+k}) for k = -max_lag..+max_lag.
    Positive lag = SPY leads (yield follows after k days).
    Negative lag = yield leads (SPY follows after |k| days).
    """
    sub = df.dropna(subset=["spy_ret", "dgs10_chg"]).copy()
    rows = []
    for k in range(-max_lag, max_lag + 1):
        if k > 0:
            r = sub["spy_ret"].corr(sub["dgs10_chg"].shift(-k))
        elif k < 0:
            r = sub["spy_ret"].corr(sub["dgs10_chg"].shift(-k))
        else:
            r = sub["spy_ret"].corr(sub["dgs10_chg"])
        rows.append({"lag": k, "corr": r})
    return pd.DataFrame(rows)


def validation_rate(df: pd.DataFrame, threshold: float = 0.01,
                   windows: list[int] = [1, 3, 5, 10]) -> pd.DataFrame:
    """For SPY ≥ +threshold days: % where DGS10 also rose within W trading days.
    Mirror for SPY ≤ -threshold days: % where DGS10 also fell within W days."""
    rows = []
    sub = df.dropna(subset=["spy_ret", "DGS10"]).copy().reset_index(drop=True)

    for label, mask, follow_dir in [
        ("SPY rally", sub["spy_ret"] >= threshold, "up"),
        ("SPY selloff", sub["spy_ret"] <= -threshold, "down"),
    ]:
        idx = sub.index[mask].tolist()
        n = len(idx)
        for w in windows:
            validated = 0
            measured = 0
            for i in idx:
                if i + w >= len(sub):
                    continue
                yld_today = sub["DGS10"].iloc[i]
                yld_future = sub["DGS10"].iloc[i + 1: i + w + 1]
                if yld_future.dropna().empty:
                    continue
                measured += 1
                yld_max = yld_future.max()
                yld_min = yld_future.min()
                if follow_dir == "up" and yld_max > yld_today:
                    validated += 1
                elif follow_dir == "down" and yld_min < yld_today:
                    validated += 1
            pct = (validated / measured * 100) if measured else float("nan")
            rows.append({
                "event": label, "threshold_pct": threshold * 100,
                "window_d": w, "n_events": n,
                "n_with_data": measured,
                "validated_pct": pct,
            })
    return pd.DataFrame(rows)


def regime_split_validation(df: pd.DataFrame, threshold: float = 0.01,
                           windows: list[int] = [3, 5]) -> pd.DataFrame:
    """Same validation rate but split by bull (>200dma) vs bear (<200dma) regime."""
    rows = []
    for regime_label, regime_mask in [
        ("BULL (SPY > 200dma)", df["below_200dma"] == False),
        ("BEAR (SPY < 200dma)", df["below_200dma"] == True),
    ]:
        sub = df[regime_mask & df["spy_ret"].notna() & df["DGS10"].notna()].copy().reset_index(drop=True)
        for label, mask, follow_dir in [
            ("rally", sub["spy_ret"] >= threshold, "up"),
            ("selloff", sub["spy_ret"] <= -threshold, "down"),
        ]:
            idx = sub.index[mask].tolist()
            for w in windows:
                validated = 0
                measured = 0
                for i in idx:
                    if i + w >= len(sub):
                        continue
                    yld_today = sub["DGS10"].iloc[i]
                    yld_future = sub["DGS10"].iloc[i + 1: i + w + 1]
                    if yld_future.dropna().empty:
                        continue
                    measured += 1
                    if follow_dir == "up" and yld_future.max() > yld_today:
                        validated += 1
                    elif follow_dir == "down" and yld_future.min() < yld_today:
                        validated += 1
                pct = (validated / measured * 100) if measured else float("nan")
                rows.append({
                    "regime": regime_label, "event": label,
                    "window_d": w, "n_with_data": measured,
                    "validated_pct": pct,
                })
    return pd.DataFrame(rows)


def main():
    print("=" * 90)
    print("  SPY ↔ 10Y Yield lead-lag study")
    print("=" * 90)

    api_key = load_fred_key()
    print("  Fetching FRED DGS10 (10Y Treasury)...")
    dgs10 = fetch_fred_series("DGS10", api_key)
    print(f"    {len(dgs10):,} observations, {dgs10['date'].min().date()} → {dgs10['date'].max().date()}")
    print("  Fetching FRED DGS2 (2Y Treasury)...")
    dgs2 = fetch_fred_series("DGS2", api_key)
    print(f"    {len(dgs2):,} observations")

    print("  Loading SPY daily (ORATS)...")
    spy = load_spy_daily()
    print(f"    {len(spy):,} trading days, {spy['date'].min().date()} → {spy['date'].max().date()}")

    print("  Aligning...")
    df = build_aligned(spy, {"DGS10": dgs10, "DGS2": dgs2})
    print(f"    aligned rows: {len(df)}")

    df.to_parquet(OUT_PARQUET, index=False)
    print(f"  Saved aligned daily frame to {OUT_PARQUET}")

    # ── Cross-correlation ──
    print("\n" + "=" * 90)
    print("  CROSS-CORRELATION:  corr(SPY_ret_t, ΔDGS10_{t+k}) — k=lag")
    print("  Positive k = SPY leads yield by k days. Negative k = yield leads SPY.")
    print("=" * 90)
    ccf = cross_correlation(df, max_lag=10)
    for _, r in ccf.iterrows():
        bar = "█" * int(abs(r["corr"]) * 200) if pd.notna(r["corr"]) else ""
        sign = "+" if r["corr"] >= 0 else "-"
        print(f"  k={int(r['lag']):+3d}   corr={r['corr']:+0.4f}   {sign}{bar}")
    peak = ccf.iloc[ccf["corr"].abs().idxmax()]
    print(f"\n  Peak |corr| = {peak['corr']:+0.4f} at lag k={int(peak['lag']):+d}")

    # ── Validation rate (full sample) ──
    print("\n" + "=" * 90)
    print("  CONDITIONAL VALIDATION RATE — full sample")
    print("  'validated_pct' = % of SPY moves where DGS10 confirmed within W days")
    print("=" * 90)
    for thr in [0.005, 0.01, 0.015]:
        print(f"\n  ── SPY |move| ≥ {thr*100:.1f}% ──")
        vr = validation_rate(df, threshold=thr)
        for _, r in vr.iterrows():
            print(f"    {r['event']:12s}  W={int(r['window_d']):2d}d  "
                  f"N={int(r['n_with_data']):4d}  "
                  f"validated={r['validated_pct']:5.1f}%")

    # ── Regime-split validation ──
    print("\n" + "=" * 90)
    print("  REGIME-SPLIT VALIDATION RATE (SPY ≥ ±1% events, W = 3d / 5d)")
    print("=" * 90)
    rsv = regime_split_validation(df, threshold=0.01, windows=[3, 5])
    for regime, grp in rsv.groupby("regime"):
        print(f"\n  {regime}")
        for _, r in grp.iterrows():
            print(f"    {r['event']:8s}  W={int(r['window_d'])}d  "
                  f"N={int(r['n_with_data']):4d}  "
                  f"validated={r['validated_pct']:5.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
