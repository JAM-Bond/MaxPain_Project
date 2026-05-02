#!/usr/bin/env python3.11
"""
Backfill regime_state for any missing dates using SPY ORATS by_ticker history.

Mirrors compute_regime_state in research_cohort_snapshot.py but for arbitrary
historical dates (the production script always picks the latest row). Writes
one regime_state row per SPY trading day the table is missing.

Also rebuilds vrp_series.parquet with the same daily series, replacing the
stale precomputed file.

Idempotent: REPLACE INTO so re-running is safe.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"
SPY_PATH = ROOT / "data/orats/by_ticker/SPY.parquet"
VRP_OUT = ROOT / "data/profile/vrp_series.parquet"
ATM_OUT = ROOT / "data/profile/atm_iv_series.parquet"

sys.path.insert(0, str(ROOT / "scripts/pipeline"))
from research_cohort_snapshot import REGIME_COLUMNS  # noqa: E402


def build_spy_daily() -> pd.DataFrame:
    """Daily SPY series with everything needed for regime state."""
    spy = pd.read_parquet(SPY_PATH, columns=["trade_date", "expirDate", "strike", "stkPx", "delta", "cMidIv"])
    spy["trade_date"] = pd.to_datetime(spy["trade_date"])
    spy["exp_dt"] = pd.to_datetime(spy["expirDate"], format="%m/%d/%Y", errors="coerce")
    spy["dte"] = (spy["exp_dt"] - spy["trade_date"]).dt.days
    spy["delta_dist"] = (spy["delta"] - 0.50).abs()

    front = spy[(spy["dte"] >= 25) & (spy["dte"] <= 35)].sort_values(
        ["trade_date", "delta_dist"]).drop_duplicates("trade_date")
    back = spy[(spy["dte"] >= 65) & (spy["dte"] <= 85)].sort_values(
        ["trade_date", "delta_dist"]).drop_duplicates("trade_date")

    daily = front.set_index("trade_date")[["stkPx", "cMidIv"]].copy()
    daily.columns = ["close", "atm_iv30"]
    daily["atm_iv75"] = back.set_index("trade_date")["cMidIv"]
    daily = daily.sort_index()

    daily["ma200"] = daily["close"].rolling(200, min_periods=100).mean()
    rmin = daily["atm_iv30"].rolling(252, min_periods=120).min()
    rmax = daily["atm_iv30"].rolling(252, min_periods=120).max()
    daily["ivr_252"] = (daily["atm_iv30"] - rmin) / (rmax - rmin).replace(0, np.nan)
    daily["term_spread"] = daily["atm_iv30"] - daily["atm_iv75"]
    daily["log_ret"] = np.log(daily["close"] / daily["close"].shift(1))
    daily["rv20"] = daily["log_ret"].rolling(20).std() * np.sqrt(252)
    daily["vrp"] = daily["atm_iv30"] - daily["rv20"]
    return daily


def regime_row_from_daily(row: pd.Series, snapshot_date: date) -> dict:
    """Convert one daily-series row into a regime_state record."""
    spy_close = float(row["close"])
    ma200 = float(row["ma200"])
    pct_to_ma200 = (spy_close / ma200 - 1) if ma200 > 0 else np.nan
    iv_rank = float(row["ivr_252"])
    term_spread = float(row["term_spread"]) if pd.notna(row["term_spread"]) else None
    vrp = float(row["vrp"]) if pd.notna(row["vrp"]) else None

    below_200dma = bool(spy_close < ma200)
    ivr_high = bool(iv_rank > 0.5)
    term_inverted = bool(term_spread > 0) if term_spread is not None else False
    contango = bool(term_spread < 0) if term_spread is not None else False
    vrp_positive = bool(vrp > 0) if vrp is not None else False
    h1_active = bool(below_200dma and ivr_high)
    hard_pause_active = bool(below_200dma and term_inverted and ivr_high)
    bull_put_signal_active = bool(contango and vrp_positive)
    if_gate_active = bool(term_inverted)

    # VIX proxy from ATM IV30 — exact VIX requires yfinance which is fragile in backfill
    vix_value = float(row["atm_iv30"]) * 100.0
    vix_high = vix_value > 20.0
    near_ma200 = abs(pct_to_ma200) <= 0.02 if not pd.isna(pct_to_ma200) else False
    soft_downsize_active = bool(
        iv_rank > 0.7
        or (near_ma200 and below_200dma)
        or (term_inverted and vix_high)
    )

    if h1_active:
        stage = 3
    elif below_200dma:
        stage = 2
    elif soft_downsize_active:
        stage = 1
    else:
        stage = 0

    as_of = row.name.date() if hasattr(row.name, "date") else row.name

    return {
        "snapshot_date": str(snapshot_date),
        "as_of_close": str(as_of),
        "spy_close": spy_close,
        "spy_ma200": round(ma200, 4),
        "spy_pct_to_ma200": round(pct_to_ma200, 5) if not pd.isna(pct_to_ma200) else None,
        "spy_atm_iv30": round(float(row["atm_iv30"]), 4),
        "spy_ivr_252": round(iv_rank, 4),
        "spy_term_spread": round(term_spread, 5) if term_spread is not None else None,
        "spy_vrp": round(vrp, 5) if vrp is not None else None,
        "spy_vix": round(vix_value, 2),
        "below_200dma": int(below_200dma),
        "ivr_high": int(ivr_high),
        "term_inverted": int(term_inverted),
        "h1_active": int(h1_active),
        "hard_pause_active": int(hard_pause_active),
        "soft_downsize_active": int(soft_downsize_active),
        "if_gate_active": int(if_gate_active),
        "bull_put_signal_active": int(bull_put_signal_active),
        "stage": stage,
    }


def main():
    print("Building SPY daily series...")
    daily = build_spy_daily()
    print(f"  {len(daily):,} trading days, latest {daily.index.max().date()}")

    # ── Update vrp_series.parquet (replace stale 4/21 file) ──
    vrp_out = pd.DataFrame({
        "ticker": "SPY",
        "trade_date": daily.index,
        "atm_iv": daily["atm_iv30"].values,
        "iv_rank": daily["ivr_252"].values,
        "hv20": daily["rv20"].values,
        "vrp": daily["vrp"].values,
        "iv_hv_ratio": (daily["atm_iv30"] / daily["rv20"]).values,
    }).reset_index(drop=True).dropna(subset=["vrp"])
    # Preserve other tickers from existing vrp_series if it exists
    if VRP_OUT.exists():
        existing = pd.read_parquet(VRP_OUT)
        other = existing[existing["ticker"] != "SPY"]
        vrp_out = pd.concat([other, vrp_out], ignore_index=True)
    vrp_out.to_parquet(VRP_OUT, index=False)
    print(f"  Wrote {VRP_OUT} (SPY rows current through {daily.index.max().date()})")

    # ── Update atm_iv_series.parquet (SPY portion) ──
    atm_out = pd.DataFrame({
        "ticker": "SPY",
        "trade_date": daily.index,
        "atm_iv": daily["atm_iv30"].values,
        "iv_rank": daily["ivr_252"].values,
    }).reset_index(drop=True).dropna(subset=["atm_iv"])
    if ATM_OUT.exists():
        existing = pd.read_parquet(ATM_OUT)
        other = existing[existing["ticker"] != "SPY"]
        atm_out = pd.concat([other, atm_out], ignore_index=True)
    atm_out.to_parquet(ATM_OUT, index=False)
    print(f"  Wrote {ATM_OUT}")

    # ── Backfill regime_state for any missing dates ──
    print("\nBackfilling regime_state...")
    conn = sqlite3.connect(DB_PATH)
    existing = pd.read_sql("SELECT snapshot_date FROM regime_state", conn)
    existing_dates = set(pd.to_datetime(existing["snapshot_date"]).dt.date)

    cur = conn.cursor()
    placeholders = ", ".join(["?"] * len(REGIME_COLUMNS))
    col_list = ", ".join(REGIME_COLUMNS)

    new_rows = 0
    updated_rows = 0
    daily_valid = daily.dropna(subset=["ma200", "ivr_252"])
    for trade_date, row in daily_valid.iterrows():
        d = trade_date.date()
        snap_date = d  # snapshot_date == trade_date for backfill (gate state as of that close)
        record = regime_row_from_daily(row, snap_date)
        cur.execute(
            f"INSERT OR REPLACE INTO regime_state ({col_list}) VALUES ({placeholders})",
            [record.get(c) for c in REGIME_COLUMNS],
        )
        if d in existing_dates:
            updated_rows += 1
        else:
            new_rows += 1
    conn.commit()
    print(f"  Inserted {new_rows} new rows, replaced {updated_rows} existing rows")

    # ── Show recent regime evolution ──
    df = pd.read_sql("""
        SELECT snapshot_date, as_of_close, spy_close, spy_term_spread, spy_vrp,
               term_inverted, bull_put_signal_active, h1_active, if_gate_active
        FROM regime_state
        WHERE snapshot_date >= '2026-04-01'
        ORDER BY snapshot_date
    """, conn)
    conn.close()
    print(f"\nApril 1 → present regime ({len(df)} rows):")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
