"""Read-only queries for the Filled Book page — the real Schwab order activity.

Sources:
  - order_legs            : leg-level Schwab mirror (PK order_id, leg_id)
  - spread_score_trades   : positions DERIVED from the orders (open_order_id set)

Read-only. Never touches an order.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def legs_df(days: int = 90) -> pd.DataFrame:
    """Leg-level mirror, most-recent first. One row per Schwab order leg."""
    with _conn() as c:
        if not _has_table(c, "order_legs"):
            return pd.DataFrame()
        df = pd.read_sql_query(
            """SELECT order_id, leg_id, underlying, symbol, put_call, strike, expiry,
                      instruction, position_effect, quantity, fill_price, fees,
                      order_type, status, execution_time
               FROM order_legs
               WHERE execution_time >= datetime('now', ?)
               ORDER BY execution_time DESC, order_id DESC, leg_id ASC""",
            c, params=(f"-{int(days)} days",))
    return df


def order_summary_df(days: int = 90) -> pd.DataFrame:
    """One row per Schwab order: legs rolled up to net price (SELL +, BUY −) + fees.
    This is the 'mirror, grouped by spread' view."""
    legs = legs_df(days)
    if legs.empty:
        return legs
    legs = legs.copy()
    legs["signed"] = legs.apply(
        lambda r: (r["fill_price"] if "SELL" in str(r["instruction"]) else -r["fill_price"]),
        axis=1)
    g = legs.groupby("order_id")
    rows = []
    for oid, sub in g:
        sub = sub.sort_values("leg_id")
        net = round(sub["signed"].sum(), 2)
        effect = ("OPEN" if set(sub["position_effect"]) <= {"OPENING"} else
                  "CLOSE" if set(sub["position_effect"]) <= {"CLOSING"} else "MIXED")
        rows.append({
            "order_id": oid,
            "time": str(sub["execution_time"].iloc[0])[:16],
            "underlying": sub["underlying"].iloc[0],
            "legs": len(sub),
            "effect": effect,
            "contracts": ", ".join(
                f"{r.instruction.split('_')[0]} {int(r.quantity)}x {r.put_call[0]}{r.strike:g}"
                for r in sub.itertuples()),
            "net_price": net,                # + = net credit taken in, − = net debit paid
            "fees": round(sub["fees"].sum(), 2),
            "order_type": sub["order_type"].iloc[0],
        })
    return pd.DataFrame(rows).sort_values("time", ascending=False).reset_index(drop=True)


def reconciled_positions_df() -> pd.DataFrame:
    """spread_score_trades positions that were recorded from real Schwab orders
    (open_order_id set) — the live, reconciled book (open + closed)."""
    with _conn() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(spread_score_trades)")}
        if "open_order_id" not in cols:
            return pd.DataFrame()
        df = pd.read_sql_query(
            """SELECT id, symbol, spread_type, short_strike, long_strike, shares,
                      entry_credit, entry_date, exit_credit, exit_date, fees_total,
                      final_pnl, status, open_order_id, close_order_id
               FROM spread_score_trades
               WHERE open_order_id IS NOT NULL
               ORDER BY (status='open') DESC, COALESCE(exit_date, entry_date) DESC""",
            c)
    return df
