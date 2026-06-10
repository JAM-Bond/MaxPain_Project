#!/usr/bin/env python3.11
"""Migration 008 — add Schwab order-id + fees columns to spread_score_trades.

Supports the go-live order reconciler (lib/order_reconciler.py): a daily read-only
pass over FILLED Schwab orders that inserts new (OPENING) spreads as status='open'
and closes the matching row when a CLOSING order appears, recording net-of-fees P/L.

- open_order_id  / close_order_id : Schwab orderId for the opening / closing order.
  Make the reconciler idempotent (skip an order already recorded) and auditable
  (every auto-recorded row traces to a real Schwab order).
- fees_total : summed Schwab commissions/fees (open + close) folded into final_pnl.

Idempotent: re-run is a no-op once the columns exist.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402

NEW_COLS = [("open_order_id", "TEXT"), ("close_order_id", "TEXT"), ("fees_total", "REAL")]


def main(apply: bool) -> None:
    c = sqlite3.connect(DB_PATH)
    existing = {r[1] for r in c.execute("PRAGMA table_info(spread_score_trades)")}
    todo = [(n, t) for n, t in NEW_COLS if n not in existing]
    if not todo:
        print("all order-id columns already present — no-op")
        c.close()
        return
    for name, typ in todo:
        print(f"  {'ADD' if apply else 'would add'} column {name} {typ}")
        if apply:
            c.execute(f"ALTER TABLE spread_score_trades ADD COLUMN {name} {typ}")
    if apply:
        c.commit()
        print("APPLIED")
    else:
        print("DRY-RUN (pass --apply to write)")
    c.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
