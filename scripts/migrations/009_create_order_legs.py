#!/usr/bin/env python3.11
"""Migration 009 — create `order_legs`, the leg-level mirror of Schwab orders.

Per user design 2026-06-10: SQLite mirrors Schwab at the LEG level. Each Schwab
order leg has a shared `order_id` (same for all legs of a spread) and a per-leg
`leg_id` (unique within the order). The **compound PRIMARY KEY (order_id, leg_id)**
makes duplicate inserts impossible and lets us store each leg's own fill price + fees
— so net P/L = Σ signed leg cash flows − fees, which works for ANY leg count (2-leg
verticals through 3+ leg zebra / inverted-fly / iron-condor).

This is the read-only Schwab mirror (source of truth for fills/P&L). The reconciler
derives the spread-level `spread_score_trades` row from these legs.

Idempotent: re-run is a no-op if the table exists.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS order_legs (
    order_id        TEXT,      -- shared across all legs of the spread (Schwab orderId)
    leg_id          INTEGER,   -- unique within the order (Schwab legId)
    underlying      TEXT,
    symbol          TEXT,      -- OCC option symbol
    asset_type      TEXT,
    put_call        TEXT,
    strike          REAL,
    expiry          TEXT,
    instruction     TEXT,      -- SELL_TO_OPEN / BUY_TO_OPEN / BUY_TO_CLOSE / SELL_TO_CLOSE
    position_effect TEXT,      -- OPENING / CLOSING
    quantity        REAL,
    fill_price      REAL,      -- per-share execution price for THIS leg
    fees            REAL,      -- per-leg commissions/fees (from schwab_fills)
    order_type      TEXT,      -- NET_CREDIT / NET_DEBIT / LIMIT
    status          TEXT,      -- order status (FILLED, ...)
    entered_time    TEXT,
    execution_time  TEXT,
    ingested_at     TEXT,
    PRIMARY KEY (order_id, leg_id)
)
"""


def main(apply: bool) -> None:
    c = sqlite3.connect(DB_PATH)
    exists = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='order_legs'").fetchone()
    if exists:
        print("order_legs already exists — no-op")
        c.close()
        return
    print("CREATE TABLE order_legs (PK order_id, leg_id)" if apply
          else "DRY-RUN: would create order_legs (pass --apply)")
    if apply:
        c.execute(DDL)
        c.commit()
        print("APPLIED")
    c.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
