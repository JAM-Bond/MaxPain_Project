#!/usr/bin/env python3.11
"""
Universe expansion v2 — liquidity scan (pre-reg Section 2).

Reads the partitioned ORATS archive at data/orats/parquet/ directly
(no per-ticker extraction) and applies the liquidity gates from
docs/UNIVERSE_EXPANSION_V2_PREREG.md to identify candidate names not
already in the 163-ticker by_ticker/ archive.

Output:
  data/profile/universe_v2_liquidity_pool.parquet — candidate list with
    aggregate stats, ready for the extraction step.

Methodology:
  Recent-liquidity stats: average per-ticker aggregates across 12
    sample dates (one per month, last 12 months, nearest the 15th).
  History coverage: ticker presence across 5 yearly probe dates
    (June 15 nearest each year, 2020-2024). Pass if ≥4 of 5.

Runtime: ~30-60 seconds. Pure pandas; no backtest invoked.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PARQUET_ROOT = ROOT / "data/orats/parquet"
BY_TICKER_ROOT = ROOT / "data/orats/by_ticker"
OUTPUT = ROOT / "data/profile/universe_v2_liquidity_pool.parquet"

# Pre-reg gates
MIN_OI = 10_000
MIN_VOL = 1_000
MAX_BID_ASK_PCT = 0.10
MIN_SPOT = 5.0
MAX_SPOT = 1_000.0
MIN_HISTORY_YEARS = 4

# Hard exclusions per pre-reg Section 2
EXCLUDED_INDEX_SYMBOLS = {"SPXW", "RUT", "NDX", "RUTW", "NDXW"}
HARD_EXCLUDE_PATTERN = re.compile(r"[\^\.\$]")


def find_parquet_for(target: date) -> Path | None:
    """Return parquet path for a date, walking ±5 days if exact missing."""
    for offset in range(0, 6):
        for d in (target + timedelta(days=offset), target - timedelta(days=offset)):
            p = PARQUET_ROOT / f"year={d.year}" / f"month={d.month:02d}" / f"{d.isoformat()}.parquet"
            if p.exists():
                return p
        if offset == 0:
            continue
    return None


def recent_sample_dates() -> list[Path]:
    """12 dates, one per month, nearest the 15th, last 12 months."""
    today = date.today()
    paths = []
    for m_offset in range(1, 13):  # 1 month back through 12 months back
        year = today.year + (today.month - 1 - m_offset) // 12
        month = ((today.month - 1 - m_offset) % 12) + 1
        target = date(year, month, 15)
        p = find_parquet_for(target)
        if p:
            paths.append(p)
    return paths


def history_probe_dates() -> list[Path]:
    """5 yearly probe dates: June 15 nearest, 2020-2024."""
    paths = []
    for year in (2020, 2021, 2022, 2023, 2024):
        target = date(year, 6, 15)
        p = find_parquet_for(target)
        if p:
            paths.append(p)
    return paths


def aggregate_one_date(parquet_path: Path) -> pd.DataFrame:
    """Per-ticker aggregate from one daily parquet. Returns DataFrame
    indexed by ticker with columns: spot, sum_oi, sum_vol,
    atm_bid_ask_pct, has_weekly."""
    cols = ["ticker", "stkPx", "expirDate", "cOi", "pOi", "cVolu", "pVolu",
            "cBidPx", "cAskPx", "pBidPx", "pAskPx", "delta", "trade_date"]
    df = pd.read_parquet(parquet_path, columns=cols)

    # Total OI and volume per ticker
    df["total_oi_strike"] = df["cOi"].fillna(0) + df["pOi"].fillna(0)
    df["total_vol_strike"] = df["cVolu"].fillna(0) + df["pVolu"].fillna(0)

    # ATM bid-ask pct: filter |delta - 0.5| < 0.1, average call+put bid-ask/mid
    atm_mask = (df["delta"].abs() - 0.5).abs() < 0.10
    atm = df.loc[atm_mask].copy()
    atm["c_mid"] = (atm["cBidPx"] + atm["cAskPx"]) / 2.0
    atm["p_mid"] = (atm["pBidPx"] + atm["pAskPx"]) / 2.0
    atm["c_ba_pct"] = np.where(atm["c_mid"] > 0,
                                (atm["cAskPx"] - atm["cBidPx"]) / atm["c_mid"],
                                np.nan)
    atm["p_ba_pct"] = np.where(atm["p_mid"] > 0,
                                (atm["pAskPx"] - atm["pBidPx"]) / atm["p_mid"],
                                np.nan)
    atm["atm_ba_pct"] = atm[["c_ba_pct", "p_ba_pct"]].mean(axis=1)
    atm_per_ticker = atm.groupby("ticker", observed=True)["atm_ba_pct"].median()

    # Has weekly: any expirDate giving DTE 7-21
    df["exp_dt"] = pd.to_datetime(df["expirDate"], format="%m/%d/%Y", errors="coerce")
    df["dte"] = (df["exp_dt"] - df["trade_date"]).dt.days
    has_weekly = df.assign(weekly_flag=(df["dte"] >= 7) & (df["dte"] <= 21))\
                   .groupby("ticker", observed=True)["weekly_flag"].any()

    out = pd.DataFrame({
        "spot": df.groupby("ticker", observed=True)["stkPx"].first(),
        "sum_oi": df.groupby("ticker", observed=True)["total_oi_strike"].sum(),
        "sum_vol": df.groupby("ticker", observed=True)["total_vol_strike"].sum(),
        "atm_bid_ask_pct": atm_per_ticker,
        "has_weekly": has_weekly,
    })
    return out


def compute_history_coverage(history_paths: list[Path]) -> pd.Series:
    """Returns a Series indexed by ticker: count of probe dates ticker appears."""
    counts: dict[str, int] = {}
    for p in history_paths:
        df = pd.read_parquet(p, columns=["ticker"])
        unique_tickers = df["ticker"].dropna().unique()
        for t in unique_tickers:
            counts[t] = counts.get(t, 0) + 1
    return pd.Series(counts, name="history_years")


def main() -> None:
    print("Universe v2 liquidity scan")
    print("=" * 60)
    print()

    # Existing 163 cohort to exclude
    existing = {p.stem for p in BY_TICKER_ROOT.glob("*.parquet")}
    print(f"Existing by_ticker/ cohort: {len(existing)} tickers (excluded from candidate pool)")
    print()

    # Recent liquidity sample
    print("Sampling recent liquidity (last 12 months, nearest 15th)...")
    recent_paths = recent_sample_dates()
    print(f"  Sample dates: {len(recent_paths)}")
    for p in recent_paths:
        print(f"    {p.stem}.parquet")
    print()

    t0 = time.time()
    daily_aggs = []
    for p in recent_paths:
        agg = aggregate_one_date(p)
        agg["sample_date"] = p.stem
        daily_aggs.append(agg)
    print(f"  Read + aggregated {len(recent_paths)} dates in {time.time()-t0:.1f}s")

    # Average per-ticker across the sample dates
    combined = pd.concat(daily_aggs).groupby(level=0).agg(
        spot=("spot", "median"),
        sum_oi=("sum_oi", "mean"),
        sum_vol=("sum_vol", "mean"),
        atm_bid_ask_pct=("atm_bid_ask_pct", "median"),
        has_weekly_frac=("has_weekly", "mean"),
        sample_n=("sample_date", "count"),
    )
    print(f"  Distinct tickers in recent sample: {len(combined):,}")
    print()

    # History coverage probe
    print("Probing history coverage (5 yearly dates 2020-2024)...")
    history_paths = history_probe_dates()
    for p in history_paths:
        print(f"    {p.stem}.parquet")
    t0 = time.time()
    history_years = compute_history_coverage(history_paths)
    print(f"  Read + tallied in {time.time()-t0:.1f}s")
    print()

    combined = combined.join(history_years).fillna({"history_years": 0})
    combined["history_years"] = combined["history_years"].astype(int)

    # Apply gates
    print("Applying pre-reg gates...")
    pool = combined.copy()
    pool["gate_oi"] = pool["sum_oi"] >= MIN_OI
    pool["gate_vol"] = pool["sum_vol"] >= MIN_VOL
    pool["gate_ba"] = pool["atm_bid_ask_pct"] <= MAX_BID_ASK_PCT
    pool["gate_spot"] = (pool["spot"] >= MIN_SPOT) & (pool["spot"] <= MAX_SPOT)
    pool["gate_history"] = pool["history_years"] >= MIN_HISTORY_YEARS
    pool["gate_weekly"] = pool["has_weekly_frac"] >= 0.5

    pool["passes_all"] = (pool["gate_oi"] & pool["gate_vol"] & pool["gate_ba"]
                         & pool["gate_spot"] & pool["gate_history"]
                         & pool["gate_weekly"])

    # Per-gate pass counts (informational)
    n_total = len(pool)
    print(f"  Total tickers in scan: {n_total:,}")
    for g in ("gate_oi", "gate_vol", "gate_ba", "gate_spot", "gate_history", "gate_weekly"):
        print(f"    {g}: {pool[g].sum():>5,} pass")
    print(f"  ALL gates pass: {pool['passes_all'].sum():,}")
    print()

    # Filter
    survivors = pool[pool["passes_all"]].copy()
    survivors = survivors.sort_values("sum_oi", ascending=False)

    # Hard exclusions
    is_index = survivors.index.isin(EXCLUDED_INDEX_SYMBOLS)
    is_pattern = survivors.index.to_series().apply(
        lambda t: bool(HARD_EXCLUDE_PATTERN.search(str(t)))
    ).values
    is_existing = survivors.index.isin(existing)
    excluded = is_index | is_pattern | is_existing
    print(f"Hard exclusions: {is_index.sum()} index, "
          f"{is_pattern.sum()} pattern (^./$), "
          f"{is_existing.sum()} already-in-by_ticker. "
          f"Total excluded: {excluded.sum():,}")
    final = survivors[~excluded].copy()
    print()

    # Output
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    final.reset_index().to_parquet(OUTPUT, index=False)
    print(f"✓ Wrote {len(final):,} candidates to {OUTPUT}")
    print()
    print("Top 30 by total OI:")
    show = final[["spot", "sum_oi", "sum_vol", "atm_bid_ask_pct", "history_years"]].head(30)
    show = show.assign(
        sum_oi=show["sum_oi"].astype(int),
        sum_vol=show["sum_vol"].astype(int),
        atm_bid_ask_pct=show["atm_bid_ask_pct"].round(3),
    )
    print(show.to_string())


if __name__ == "__main__":
    main()
