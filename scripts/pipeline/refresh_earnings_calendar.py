#!/usr/bin/env python3.11
"""
Refresh the earnings_calendar_cache.parquet to cover all symbols we care about:

- All symbols currently held in spread_score_trades (placed=1 open + placed=0 open)
- Research cohort (v1.5 deployable shortlist)

Cron: 9:00 AM ET weekdays — runs before the qualifier (9:25) and the
daily alert (4:45 PM), keeping the 24-hour cache TTL fresh for both.

The underlying fetcher is qualifier/earnings_calendar.py. This script just
collects the right symbol scope, calls force_refresh=True, and prints what
came back so the cron logs are useful.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
COHORT_PARQUET = ROOT / "data/profile/research_cohort_v15.parquet"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from lib.db import DB_PATH  # noqa: E402
from qualifier.earnings_calendar import load_earnings_calendar  # noqa: E402


def collect_symbols() -> list[str]:
    syms: set[str] = set()

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM spread_score_trades WHERE status='open'"
        ).fetchall()
        syms.update(r[0] for r in rows if r[0])
    finally:
        conn.close()

    if COHORT_PARQUET.exists():
        df = pd.read_parquet(COHORT_PARQUET)
        col = "ticker" if "ticker" in df.columns else "symbol"
        syms.update(df[col].dropna().tolist())

    return sorted(syms)


def main():
    symbols = collect_symbols()
    print(f"Refreshing earnings calendar for {len(symbols)} symbols...")
    df = load_earnings_calendar(symbols, force_refresh=True)

    if df.empty:
        print("  WARNING: no earnings rows fetched")
        return 1

    today = pd.Timestamp.now().normalize().date()
    df_today = df[df["earnings_date"] >= today].sort_values(
        ["earnings_date", "ticker"]
    )

    fetched_syms = set(df["ticker"].unique())
    missing = sorted(set(symbols) - fetched_syms)

    print(f"  Cache rows total: {len(df)}")
    print(f"  Symbols with data: {len(fetched_syms)} / {len(symbols)} requested")
    if missing:
        print(f"  No earnings data returned for {len(missing)}: "
              f"{', '.join(missing[:20])}"
              + (' ...' if len(missing) > 20 else ''))
    print(f"  Upcoming events (today onward): {len(df_today)}")

    if not df_today.empty:
        print()
        print(df_today[["ticker", "earnings_date"]].head(60).to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
