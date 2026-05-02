#!/usr/bin/env python3.11
"""
Migration 001 — merge daily_snapshots + research_cohort_snapshots into live_snapshots.

Run with --dry-run to preview the merge without writing anything.

Schema:
    live_snapshots (
        symbol TEXT NOT NULL,
        snapshot_date TEXT NOT NULL,
        opex_date TEXT,
        current_price REAL,
        max_pain REAL,
        distance_pct REAL,
        pin_zone_low REAL,
        pin_zone_high REAL,
        pin_zone_width REAL,
        pcr REAL,
        total_call_oi INTEGER,
        total_put_oi INTEGER,
        expected_move REAL,
        atm_iv_pct REAL,
        net_gamma REAL,
        net_gamma_sign TEXT,
        gamma_flip_strike REAL,
        oi_concentration_at_mp REAL,
        dividend_flag INTEGER DEFAULT 0,
        ex_div_date TEXT,
        dte INTEGER,
        data_source TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (symbol, snapshot_date)
    )

Backfill order: daily_snapshots first (data_source='yfinance' since the
Metal cron used yfinance), then research_cohort_snapshots with
INSERT OR REPLACE so schwab rows win on (symbol, snapshot_date) overlap.

Both old tables are LEFT IN PLACE as frozen backup. Metal scripts that
still reference them remain functional against the historical snapshot.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DB = Path.home() / "Metal_Project/data/shared/metal_project.db"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS live_snapshots (
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    opex_date TEXT,
    current_price REAL,
    max_pain REAL,
    distance_pct REAL,
    pin_zone_low REAL,
    pin_zone_high REAL,
    pin_zone_width REAL,
    pcr REAL,
    total_call_oi INTEGER,
    total_put_oi INTEGER,
    expected_move REAL,
    atm_iv_pct REAL,
    net_gamma REAL,
    net_gamma_sign TEXT,
    gamma_flip_strike REAL,
    oi_concentration_at_mp REAL,
    dividend_flag INTEGER DEFAULT 0,
    ex_div_date TEXT,
    dte INTEGER,
    data_source TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, snapshot_date)
)
"""

BACKFILL_FROM_DAILY = """
INSERT OR IGNORE INTO live_snapshots (
    symbol, snapshot_date, opex_date,
    current_price, max_pain, distance_pct,
    pin_zone_low, pin_zone_high, pin_zone_width,
    pcr, total_call_oi, total_put_oi,
    expected_move, atm_iv_pct,
    net_gamma, net_gamma_sign, gamma_flip_strike, oi_concentration_at_mp,
    dividend_flag, ex_div_date, dte,
    data_source, created_at
)
SELECT
    symbol, snapshot_date, opex_date,
    current_price, max_pain, distance_pct,
    pin_zone_low, pin_zone_high, pin_zone_width,
    pcr, total_call_oi, total_put_oi,
    expected_move, atm_iv_pct,
    net_gamma, net_gamma_sign, gamma_flip_strike, oi_concentration_at_mp,
    dividend_flag, ex_div_date, dte,
    'yfinance' AS data_source, created_at
FROM daily_snapshots
"""

BACKFILL_FROM_RESEARCH = """
INSERT OR REPLACE INTO live_snapshots (
    symbol, snapshot_date, opex_date,
    current_price, max_pain, distance_pct,
    pin_zone_low, pin_zone_high, pin_zone_width,
    pcr, total_call_oi, total_put_oi,
    expected_move, atm_iv_pct,
    net_gamma, net_gamma_sign, gamma_flip_strike, oi_concentration_at_mp,
    dividend_flag, ex_div_date, dte,
    data_source, created_at
)
SELECT
    symbol, snapshot_date, opex_date,
    current_price, max_pain, distance_pct,
    pin_zone_low, pin_zone_high, pin_zone_width,
    pcr, total_call_oi, total_put_oi,
    expected_move, atm_iv_pct,
    net_gamma, net_gamma_sign, gamma_flip_strike, oi_concentration_at_mp,
    dividend_flag, ex_div_date, dte,
    COALESCE(data_source, 'schwab') AS data_source, created_at
FROM research_cohort_snapshots
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Open transaction, run migration, then ROLLBACK and report counts.")
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    n_daily = cur.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
    n_research = cur.execute("SELECT COUNT(*) FROM research_cohort_snapshots").fetchone()[0]
    print(f"daily_snapshots:           {n_daily:>5} rows")
    print(f"research_cohort_snapshots: {n_research:>5} rows")

    cur.execute("BEGIN")
    cur.execute(CREATE_SQL)
    cur.execute(BACKFILL_FROM_DAILY)
    n_after_daily = cur.execute("SELECT COUNT(*) FROM live_snapshots").fetchone()[0]
    cur.execute(BACKFILL_FROM_RESEARCH)
    n_after_research = cur.execute("SELECT COUNT(*) FROM live_snapshots").fetchone()[0]

    print(f"live_snapshots after daily backfill:    {n_after_daily:>5}")
    print(f"live_snapshots after research backfill: {n_after_research:>5}")
    print(f"  → {n_after_research - n_after_daily} new rows from research (rest were updates)")

    by_source = list(cur.execute(
        "SELECT data_source, COUNT(*) FROM live_snapshots GROUP BY data_source"
    ))
    print(f"by data_source: {by_source}")

    n_overlap = cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM daily_snapshots d
            JOIN research_cohort_snapshots r
              ON d.symbol = r.symbol AND d.snapshot_date = r.snapshot_date
        )
    """).fetchone()[0]
    print(f"(sym, date) overlap: {n_overlap} pairs (research wins via REPLACE)")

    if args.dry_run:
        conn.rollback()
        print("\n  (dry-run — ROLLBACK)")
    else:
        conn.commit()
        print("\n  ✓ COMMITTED")
    conn.close()


if __name__ == "__main__":
    main()
