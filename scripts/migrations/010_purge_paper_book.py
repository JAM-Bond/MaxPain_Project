#!/usr/bin/env python3.11
"""Migration 010 — GO-LIVE paper-book purge (the §E cutover step).

Deletes the ROWS of the 6 paper-book tables (schema kept; the reconciler + crons
repopulate from real fills at go-live). KEEPS all collected market/signal data AND
the real-data tables (order_legs, schwab_fills). See GO_LIVE_READINESS.md §E.

⚠️ DESTRUCTIVE — wipes the entire paper trade book. Therefore:
  - DRY-RUN by default (prints counts, deletes nothing).
  - Requires BOTH --apply AND --yes-purge-paper-book to execute (accidental-run guard;
    must never fire during the paper-test period).
  - Auto-takes a consistent safety backup (sqlite3 online backup) BEFORE any delete.
  - Verifies the KEEP real-data tables are byte-for-byte untouched (row counts equal).
  - Idempotent: re-run after a purge finds 0 paper rows and is a no-op.

NOT wired to any cron — operator-run once, at the go-live cutover, after the final
paper post-mortem.

  python3.11 -m scripts.migrations.010_purge_paper_book                              # dry-run
  python3.11 -m scripts.migrations.010_purge_paper_book --apply --yes-purge-paper-book  # execute
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402

# Rows-only delete; schema preserved.
PAPER_TABLES = [
    "spread_score_trades", "trade_ledger_enriched", "spread_score_daily",
    "position_health_snapshots", "trade_log", "psychological_gap_log",
]
# Real-data / must-never-be-touched tables — asserted unchanged across the purge.
KEEP_REAL = ["order_legs", "schwab_fills"]
BACKUP_DIR = Path.home() / "MaxPain_Project" / "data" / "shared" / "backups"


def _counts(conn, tables):
    out = {}
    for t in tables:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except Exception as e:
            out[t] = f"(err: {e.__class__.__name__})"
    return out


def main(apply: bool, confirmed: bool) -> None:
    conn = sqlite3.connect(DB_PATH)
    paper_before = _counts(conn, PAPER_TABLES)
    keep_before = _counts(conn, KEEP_REAL)

    print("=== Paper-book purge — tables to DELETE rows (schema kept) ===")
    total = 0
    for t, n in paper_before.items():
        print(f"  {t:<28} {n} rows")
        if isinstance(n, int):
            total += n
    print(f"  TOTAL paper rows to delete: {total}")
    print("\n=== KEEP (real data, must stay untouched) ===")
    for t, n in keep_before.items():
        print(f"  {t:<28} {n} rows")

    if not apply:
        print("\nDRY-RUN — nothing deleted. To execute (go-live only):")
        print("  python3.11 -m scripts.migrations.010_purge_paper_book --apply --yes-purge-paper-book")
        conn.close()
        return

    if not confirmed:
        print("\nREFUSED: --apply requires --yes-purge-paper-book too (accidental-run guard).")
        print("This wipes the entire paper book — only run at the go-live cutover.")
        conn.close()
        return

    # Safety backup (consistent online snapshot) BEFORE any delete.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safety = BACKUP_DIR / f"maxpain_pre_purge_{ts}.db"
    dst = sqlite3.connect(str(safety))
    conn.backup(dst)
    dst.close()
    print(f"\nSafety backup written: {safety}")

    # Delete rows (schema kept).
    for t in PAPER_TABLES:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()

    paper_after = _counts(conn, PAPER_TABLES)
    keep_after = _counts(conn, KEEP_REAL)
    print("\nAPPLIED. Paper tables now:")
    for t, n in paper_after.items():
        print(f"  {t:<28} {n} rows")

    # Assert real-data tables untouched.
    if keep_after != keep_before:
        print(f"\n⚠️ WARNING: KEEP tables changed! before={keep_before} after={keep_after} "
              f"— restore from {safety}")
    else:
        print(f"\n✓ KEEP real-data tables unchanged: {keep_after}")
    print(f"Recover if needed: bash scripts/restore_db.sh {safety} --apply")
    conn.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv, confirmed="--yes-purge-paper-book" in sys.argv)
