#!/usr/bin/env python3.11
"""Migration 012 — add `account` column to spread_score_trades (live vs paper).

Driver: the first real-money trade (HCA bull_put 370/365 Aug-21, opened
2026-06-09, closed 2026-06-12) ran during the paper-test window. The go-live
paper purge (migration 010) deletes the whole paper book; live trades and
their trade_id-linked rows must survive it. This column is the distinction.

  account = 'paper'  (default) — paper-test rows, purged at go-live
  account = 'live'             — real-money rows, kept forever

SQLite ADD COLUMN with a DEFAULT serves the default for pre-existing rows,
but we also physically backfill 'paper' so the value is explicit on disk.

Idempotent: re-run is a no-op if the column exists.

  python3.11 -m scripts.migrations.012_add_account_to_trades
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(spread_score_trades)")}
    if "account" in cols:
        n_live = cur.execute(
            "SELECT COUNT(*) FROM spread_score_trades WHERE account='live'"
        ).fetchone()[0]
        print(f"account column already present ({n_live} live rows) — no-op.")
        conn.close()
        return

    cur.execute(
        "ALTER TABLE spread_score_trades ADD COLUMN account TEXT DEFAULT 'paper'"
    )
    cur.execute(
        "UPDATE spread_score_trades SET account='paper' WHERE account IS NULL"
    )
    conn.commit()
    n = cur.execute("SELECT COUNT(*) FROM spread_score_trades").fetchone()[0]
    print(f"✓ account column added; {n} existing rows backfilled to 'paper'.")
    conn.close()


if __name__ == "__main__":
    main()
