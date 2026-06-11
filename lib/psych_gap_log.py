"""Psychological gap log — paper-to-live "would I close in live?" capture.

SEP-live transition checklist Item 1. The user reports a subjective
"would I close this in live capital?" judgment in chat at WARNING/TESTED
regime moments; Claude writes the row here via `insert_entry`.

The daily alert calls `pending_prompts` to surface positions newly
🟡/🔴 since their last log entry, so the user is reminded to log.

Cycle-close synthesis (held-to-expiry vs realistic-live P&L gap) joins
this table to spread_score_trades — see post-mortem bundle wiring.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from lib.db import DB_PATH


WARNING_STATES = ("🟡", "🔴")


@dataclass
class GapPrompt:
    trade_id: int
    symbol: str
    structure: str
    current_status: str
    last_logged_status: Optional[str]
    last_logged_date: Optional[str]


def insert_entry(
    trade_id: int,
    log_date: str,
    regime_state: Optional[str] = None,
    mtm_at_log: Optional[float] = None,
    would_close_live: Optional[int] = None,
    note: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    owns_conn = conn is None
    if owns_conn:
        conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            """
            INSERT INTO psychological_gap_log
                (trade_id, log_date, regime_state, mtm_at_log,
                 would_close_live, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (trade_id, log_date, regime_state, mtm_at_log,
             would_close_live, note),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        if owns_conn:
            conn.close()


def pending_prompts(
    as_of_date: str,
    conn: Optional[sqlite3.Connection] = None,
) -> list[GapPrompt]:
    """Return open positions at 🟡/🔴 today that have no log entry today
    AND whose worst-status-today differs from their last logged status
    (or have never been logged)."""
    owns_conn = conn is None
    if owns_conn:
        conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            WITH today_status AS (
                SELECT phs.trade_id, phs.symbol, phs.structure,
                       phs.combined_status
                FROM position_health_snapshots phs
                WHERE phs.snapshot_date = ?
                  AND phs.combined_status IN ('🟡', '🔴')
            ),
            last_log AS (
                SELECT trade_id,
                       MAX(log_date) AS last_date,
                       (SELECT regime_state
                          FROM psychological_gap_log p2
                         WHERE p2.trade_id = p.trade_id
                         ORDER BY log_date DESC, id DESC LIMIT 1) AS last_state
                FROM psychological_gap_log p
                GROUP BY trade_id
            ),
            today_log AS (
                SELECT DISTINCT trade_id
                FROM psychological_gap_log
                WHERE log_date = ?
            )
            SELECT ts.trade_id, ts.symbol, ts.structure, ts.combined_status,
                   ll.last_state, ll.last_date
            FROM today_status ts
            JOIN spread_score_trades sst ON sst.id = ts.trade_id
            LEFT JOIN last_log ll ON ll.trade_id = ts.trade_id
            LEFT JOIN today_log tl ON tl.trade_id = ts.trade_id
            WHERE sst.status = 'open'
              AND tl.trade_id IS NULL
              AND (ll.last_state IS NULL
                   OR ll.last_state != ts.combined_status)
            ORDER BY
                CASE ts.combined_status WHEN '🔴' THEN 0 ELSE 1 END,
                ts.symbol
            """,
            (as_of_date, as_of_date),
        ).fetchall()
        return [
            GapPrompt(
                trade_id=r[0],
                symbol=r[1],
                structure=r[2],
                current_status=r[3],
                last_logged_status=r[4],
                last_logged_date=r[5],
            )
            for r in rows
        ]
    finally:
        if owns_conn:
            conn.close()


def render_prompts_text(prompts: list[GapPrompt]) -> str:
    if not prompts:
        return ""
    lines = ["", "  PAPER-vs-LIVE GUT CHECK", "  " + "-" * 68]
    lines.append("  These open positions just turned 🟡/🔴. On paper it's easy to hold —")
    lines.append("  would you actually hold them with real money? Your honest answer now")
    lines.append("  builds the record we check before going live.  (* = first time flagged)")
    # Compact: group by current status, one wrapped line each. Trailing * = a name
    # never logged before (vs a prior-logged status that has since worsened).
    def _fmt(ps: list) -> str:
        return "  ".join(
            f"{p.symbol}(id {p.trade_id}){'*' if p.last_logged_status is None else ''}"
            for p in ps
        )
    reds = [p for p in prompts if p.current_status == "🔴"]
    yels = [p for p in prompts if p.current_status == "🟡"]
    if reds:
        lines.append(f"  🔴 {_fmt(reds)}")
    if yels:
        lines.append(f"  🟡 {_fmt(yels)}")
    lines.append("  Reply for any: \"[symbol] would_close=Y/N mtm=$X note: …\"")
    return "\n".join(lines)
