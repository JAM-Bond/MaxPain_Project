"""Materialize snapshot-at-entry context for the trade ledger.

Freezes the regime + qualifier context of each placed trade ONCE, at/after its
entry date, into `trade_ledger_enriched` (see lib.trade_ledger.snapshot_entry).
This gives the journal the immutability the spec requires
(project_trade_ledger_learning.md) instead of recomputing joins on every read.

Idempotent — only un-snapshotted trades are written. Designed to run daily as an
EOD step (a trade placed today has its entry-date regime_state row by the close).

Usage:
  python3.11 -m scripts.maintenance.snapshot_trade_ledger          # incremental
  python3.11 -m scripts.maintenance.snapshot_trade_ledger --refresh # re-freeze all (repair only)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import connect                       # noqa: E402
from lib.trade_ledger import snapshot_entry      # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="re-freeze EVERY trade (discards original entry context; repair only)")
    args = ap.parse_args()
    conn = connect()
    try:
        n = snapshot_entry(conn, refresh=args.refresh)
        total = conn.execute("SELECT COUNT(*) FROM trade_ledger_enriched").fetchone()[0]
        mode = "re-froze" if args.refresh else "froze"
        print(f"snapshot_trade_ledger: {mode} {n} trade(s); {total} total in trade_ledger_enriched")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
