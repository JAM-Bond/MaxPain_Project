#!/usr/bin/env python3.11
"""
Migration 002 — import April 2026 stock trades from Metal trade_log into
MaxPain spread_score_trades.

Adds a `shares` column to spread_score_trades (NULL for spread rows;
shares count for stock rows). Stock trades use spread_type='stock' and
0-sentinel values for the spread-specific NOT NULL columns
(short_strike, long_strike, width, entry_credit, exit_credit) since
SQLite ALTER TABLE cannot relax NOT NULL constraints in place.

Consumers that filter by spread_type can branch on 'stock' to handle
shares-based vs strike-based math.

Source: Metal_Project trade_log, entry_date='2026-04-07', 11 trades
totaling +$1,761 P/L on the 2026-04-17 OpEx cycle.

Idempotent: safe to re-run; INSERT OR IGNORE on (symbol, entry_date,
spread_type) prevents duplicate insert if rerun. ALTER TABLE wrapped
in try/except for the same reason.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DB = Path.home() / "Metal_Project/data/shared/metal_project.db"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("BEGIN")

    # 1. Add shares column if not present
    cols = [r[1] for r in cur.execute("PRAGMA table_info(spread_score_trades)")]
    if "shares" not in cols:
        cur.execute("ALTER TABLE spread_score_trades ADD COLUMN shares INTEGER")
        print("  ✓ Added `shares` column to spread_score_trades")
    else:
        print("  · `shares` column already present")

    # 2. Pull April stock rows from Metal trade_log
    rows = list(cur.execute("""
        SELECT symbol, entry_date, opex_date, entry_price, exit_date,
               exit_price, shares, pnl
        FROM trade_log
        WHERE entry_date = '2026-04-07' AND trade_type = 'stock'
        ORDER BY symbol
    """))
    print(f"  Source: {len(rows)} April stock trades from trade_log")

    # 3. Check what already exists in spread_score_trades to avoid dups
    existing = {
        (r[0], r[1])
        for r in cur.execute(
            "SELECT symbol, entry_date FROM spread_score_trades "
            "WHERE entry_date = '2026-04-07' AND spread_type = 'stock'"
        )
    }
    print(f"  Already in spread_score_trades: {len(existing)} stock rows for 2026-04-07")

    # 4. Insert
    inserted = 0
    skipped = 0
    total_pnl = 0.0
    for symbol, entry_date, opex_date, entry_price, exit_date, exit_price, shares, pnl in rows:
        if (symbol, entry_date) in existing:
            skipped += 1
            continue
        cur.execute("""
            INSERT INTO spread_score_trades (
                symbol, opex_date, spread_type,
                short_strike, long_strike, width, entry_credit,
                entry_date, entry_price,
                exit_date, exit_credit, exit_price,
                final_pnl, status, placed, shares
            ) VALUES (?, ?, 'stock', 0, 0, 0, 0, ?, ?, ?, 0, ?, ?, 'closed', 1, ?)
        """, (symbol, opex_date, entry_date, entry_price, exit_date, exit_price, pnl, shares))
        inserted += 1
        total_pnl += pnl or 0
        print(f"    + {symbol:<5} {entry_date} → {exit_date}  {shares} sh  P/L ${pnl:+.2f}")

    print()
    print(f"  Inserted: {inserted}  |  Skipped (already present): {skipped}")
    print(f"  Total P/L migrated: ${total_pnl:+,.2f}")

    if args.dry_run:
        conn.rollback()
        print("\n  (dry-run — ROLLBACK)")
    else:
        conn.commit()
        print("\n  ✓ COMMITTED")
    conn.close()


if __name__ == "__main__":
    main()
