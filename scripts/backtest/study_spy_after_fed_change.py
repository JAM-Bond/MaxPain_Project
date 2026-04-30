#!/usr/bin/env python3.11
"""
Study: how does SPY react in the days/weeks AFTER a Fed rate change?

Method:
  - Pull DFEDTARU (Fed Funds Target Upper Bound) from FRED, daily 2013-2026.
  - Identify days where the target rate changed (the actual FOMC decision day).
  - Classify each event as CUT (rate decreased) or HIKE (rate increased), by magnitude.
  - Measure SPY forward returns at 1, 5, 25, 45 trading days post-event.
  - Tabulate by event type.

Data:
  - FRED DFEDTARU (Fed Funds Target Upper Bound)
  - SPY daily closes from ORATS by_ticker parquet

Output: terminal report. Saves event-level table to data/profile/spy_after_fed.parquet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path.home() / "MaxPain_Project"
SPY_PARQUET = ROOT / "data/orats/by_ticker/SPY.parquet"
OUT_PARQUET = ROOT / "data/profile/spy_after_fed.parquet"
FRED_ENV = Path.home() / "Agent_Project/config/api_keys.env"
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def load_fred_key() -> str:
    for line in FRED_ENV.read_text().splitlines():
        if line.startswith("FRED_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("FRED_API_KEY not found")


def fetch_fred_series(series_id: str, api_key: str, start: str = "2013-01-01") -> pd.DataFrame:
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
    daily = df.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)
    daily = daily.rename(columns={"trade_date": "date", "stkPx": "spy"})
    return daily


def find_rate_changes(fed: pd.DataFrame) -> pd.DataFrame:
    """Return one row per non-zero change in DFEDTARU.
    Each row: date, prior_rate, new_rate, change_bps, direction."""
    fed = fed.sort_values("date").reset_index(drop=True).copy()
    fed["prior"] = fed["DFEDTARU"].shift(1)
    fed["change"] = fed["DFEDTARU"] - fed["prior"]
    events = fed[(fed["change"].notna()) & (fed["change"].abs() >= 0.05)].copy()
    events["change_bps"] = (events["change"] * 100).round().astype(int)
    events["direction"] = np.where(events["change_bps"] > 0, "HIKE", "CUT")
    events["magnitude"] = events["change_bps"].abs()
    return events[["date", "prior", "DFEDTARU", "change_bps", "direction", "magnitude"]]\
        .rename(columns={"DFEDTARU": "new_rate", "prior": "prior_rate"})


def attach_spy_returns(events: pd.DataFrame, spy: pd.DataFrame,
                       horizons: list[int] = [1, 5, 25, 45]) -> pd.DataFrame:
    """For each event date, find next SPY trading day and compute forward returns."""
    spy_sorted = spy.sort_values("date").reset_index(drop=True)
    rows = []
    for _, ev in events.iterrows():
        ev_date = ev["date"]
        # Find next SPY trading day on or after the event date
        future = spy_sorted[spy_sorted["date"] >= ev_date]
        if future.empty:
            continue
        i0 = future.index[0]
        spot0 = float(spy_sorted["spy"].iloc[i0])
        date0 = spy_sorted["date"].iloc[i0]
        out = ev.to_dict()
        out["spy_event_date"] = date0
        out["spy_event_close"] = spot0
        for h in horizons:
            i = i0 + h
            if i < len(spy_sorted):
                out[f"fwd_{h}d"] = spy_sorted["spy"].iloc[i] / spot0 - 1.0
            else:
                out[f"fwd_{h}d"] = np.nan
        rows.append(out)
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame):
    print("\n" + "=" * 90)
    print("  Per-event detail (chronological)")
    print("=" * 90)
    show_cols = ["date", "direction", "magnitude", "prior_rate", "new_rate",
                 "fwd_1d", "fwd_5d", "fwd_25d", "fwd_45d"]
    fmt = df[show_cols].copy()
    fmt["date"] = fmt["date"].dt.strftime("%Y-%m-%d")
    for c in ["fwd_1d", "fwd_5d", "fwd_25d", "fwd_45d"]:
        fmt[c] = fmt[c].apply(lambda x: f"{x*100:+6.2f}%" if pd.notna(x) else "  n/a")
    fmt["prior_rate"] = fmt["prior_rate"].apply(lambda x: f"{x:.2f}")
    fmt["new_rate"] = fmt["new_rate"].apply(lambda x: f"{x:.2f}")
    fmt["magnitude"] = fmt["magnitude"].astype(str) + "bp"
    print(fmt.to_string(index=False))

    print("\n" + "=" * 90)
    print("  Aggregate by direction (CUT vs HIKE)")
    print("=" * 90)
    for direction, grp in df.groupby("direction"):
        n = len(grp)
        print(f"\n  ── {direction}  N={n} ──")
        for h in [1, 5, 25, 45]:
            col = f"fwd_{h}d"
            x = grp[col].dropna()
            if len(x) == 0:
                continue
            up_pct = (x > 0).mean() * 100
            mean = x.mean() * 100
            median = x.median() * 100
            std = x.std() * 100
            print(f"    fwd_{h:2d}d  N={len(x):2d}  "
                  f"mean={mean:+6.2f}%  median={median:+6.2f}%  "
                  f"P(up)={up_pct:5.1f}%  std={std:5.2f}%")

    print("\n" + "=" * 90)
    print("  Aggregate by magnitude bucket")
    print("=" * 90)
    df["mag_bucket"] = pd.cut(df["magnitude"],
                              bins=[0, 24, 50, 100, 1000],
                              labels=["≤0.25", "0.25-0.50", "0.50-1.00", ">1.00"])
    for (direction, bucket), grp in df.groupby(["direction", "mag_bucket"], observed=True):
        if len(grp) == 0:
            continue
        x = grp["fwd_25d"].dropna()
        if len(x) == 0:
            continue
        print(f"  {direction:5s}  {bucket} pp   N={len(grp)}  "
              f"mean(25d)={x.mean()*100:+6.2f}%  "
              f"P(up)={(x>0).mean()*100:5.1f}%")


def main():
    print("=" * 90)
    print("  SPY reaction to Fed rate changes (2013-2026)")
    print("=" * 90)

    api_key = load_fred_key()
    print("  Fetching FRED DFEDTARU (Fed Funds Target Upper Bound)...")
    fed = fetch_fred_series("DFEDTARU", api_key)
    print(f"    {len(fed):,} daily observations, "
          f"{fed['date'].min().date()} → {fed['date'].max().date()}")

    print("  Loading SPY daily (ORATS)...")
    spy = load_spy_daily()
    print(f"    {len(spy):,} trading days")

    print("  Identifying rate-change events...")
    events = find_rate_changes(fed)
    print(f"    {len(events)} rate-change events found")
    print(f"      cuts:  {(events['direction']=='CUT').sum()}")
    print(f"      hikes: {(events['direction']=='HIKE').sum()}")

    print("  Attaching SPY forward returns...")
    df = attach_spy_returns(events, spy)
    df.to_parquet(OUT_PARQUET, index=False)
    print(f"    Saved event table to {OUT_PARQUET}")

    summarize(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
