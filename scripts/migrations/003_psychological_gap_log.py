#!/usr/bin/env python3.11
"""
Migration 003 — create psychological_gap_log table.

SEP-live transition checklist Item 1. Captures the "would I close this
in live capital?" subjective state at each WARNING/TESTED regime moment
on open paper positions, so the held-to-expiry-vs-realistic-live P&L
gap can be quantified at cycle close.

Run with --dry-run to preview.

Schema:
    psychological_gap_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER NOT NULL,        -- FK spread_score_trades.id
        log_date TEXT NOT NULL,           -- date the gap was logged
        regime_state TEXT,                -- 🟢/🟡/🔴 at log time (combined_status)
        mtm_at_log REAL,                  -- mark-to-market $/contract at log
        would_close_live INTEGER,         -- 1 = yes / 0 = no / NULL = unsure
        note TEXT,                        -- free-form subjective context
        created_at TEXT DEFAULT (datetime('now'))
    )

One row per (trade_id, log_date) is the expected cadence — the daily
alert prompts on positions newly transitioned to 🟡/🔴 since their last
log entry. Multiple rows for the same trade across days is fine
(captures escalation; e.g. 🟡 day 12 → 🔴 day 18).
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS psychological_gap_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    log_date TEXT NOT NULL,
    regime_state TEXT,
    mtm_at_log REAL,
    would_close_live INTEGER,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

IDX = """
CREATE INDEX IF NOT EXISTS idx_psych_gap_trade_date
    ON psychological_gap_log (trade_id, log_date);
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='psychological_gap_log';"
    ).fetchone()
    if existing:
        print("psychological_gap_log already exists — nothing to do.")
        return

    print(f"Will create psychological_gap_log in {DB_PATH}")
    if args.dry_run:
        print("DRY RUN — no changes written.")
        return

    cur.executescript(DDL + IDX)
    conn.commit()
    print("Created.")


if __name__ == "__main__":
    main()
