"""Migration 004 — add + backfill `sector` on spread_score_trades.

Adds a GICS `sector` column to the placed-trade ledger and backfills it from
lib.sector_map.get_sector(symbol), so the placed book can be sliced by sector
for rotation / post-mortem (complements the recommendation-slate SECTOR DRIFT
WATCH, which reads cycle_qualifier_runs). Idempotent + re-runnable: re-running
force-re-derives every row, so it also re-syncs after a sector_map.py update.

The daily mark cron (scripts/pipeline/mark_open_spreads.py) calls the NULL-only
backfill so new trades self-populate within a day.

Usage: python3.11 scripts/migrations/004_add_sector_to_spread_score_trades.py
"""
import sys
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import connect                       # noqa: E402
from lib.trade_ledger import backfill_sectors     # noqa: E402
from lib.sector_map import UNKNOWN_SENTINEL       # noqa: E402


def main():
    conn = connect()
    try:
        n = backfill_sectors(conn, force=True)
        print(f"✓ sector column ensured + {n} rows (re)derived")
        # report coverage
        total = conn.execute("SELECT COUNT(*) FROM spread_score_trades").fetchone()[0]
        unk = conn.execute(
            "SELECT COUNT(*) FROM spread_score_trades WHERE sector=?",
            (UNKNOWN_SENTINEL,)).fetchone()[0]
        nullc = conn.execute(
            "SELECT COUNT(*) FROM spread_score_trades WHERE sector IS NULL OR sector=''"
        ).fetchone()[0]
        print(f"  rows={total}  _UNKNOWN={unk}  NULL/empty={nullc}")
        print("  sector distribution:")
        for sec, c in conn.execute(
                "SELECT sector, COUNT(*) FROM spread_score_trades "
                "GROUP BY sector ORDER BY 2 DESC"):
            print(f"    {sec:<26}{c}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
