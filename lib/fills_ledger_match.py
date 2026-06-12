"""Fills→ledger matcher — live trades must never run dark (go-live audit F5).

Driver: the first real-money trade (HCA bull_put 370/365, opened 2026-06-09)
sat in `schwab_fills` for 3 days with NO `spread_score_trades` row — so no
marks, no breach alerts, no stop line, no T-21. This module closes that gap:
after every fills ingest, unlinked option fills are grouped by order and
either matched to an existing ledger row or AUTO-CREATE one (user-approved
design choice 2026-06-12: write to the DB without asking — this is
bookkeeping of an already-executed trade, not a trading decision; the system
remains advisory and never places orders).

What it handles automatically:
  - 2-leg same-expiry verticals (the dominant case): bull_put / bear_call /
    bull_call / bear_put inferred from put_call + which strike is short.
  - Single-leg options: long_put / long_call (debit); short singles are
    inserted too (they must be visible) and flagged loudly.
  - CLOSING orders matched back to the open live row by underlying + expiry +
    strike set; close updates exit fields + fees and sets status='closed'.
  - Already-recorded trades: matched by open/close_order_id → fills are just
    linked (idempotent; no duplicate rows).

What it deliberately does NOT auto-create (flagged instead, every run, until
resolved): 3+ leg structures, mixed-expiry groups, unequal-quantity legs,
mixed OPENING+CLOSING orders (rolls), partial closes. The daily
positions-vs-ledger reconciler (lib/live_book_reconcile.py) is the safety
net that keeps nagging about anything unmatched.

Ledger conventions (mirrors the paper book):
  - entry_credit / entry_price = signed per-share net at open (+credit/−debit)
  - exit_price = abs per-share net at close
  - final_pnl  = (open_net + close_net) × 100 × shares  (TOTAL dollars, gross)
  - fees_total = Σ leg fees across open + close (kept separate from final_pnl,
    matching the paper gross-only convention — feedback_paper_pnl_gross_only)
  - account='live', placed=1
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

from lib.sector_map import get_sector


# ─── Schema ───────────────────────────────────────────────────────────

def ensure_link_column(conn: sqlite3.Connection) -> None:
    """Idempotent: add schwab_fills.ledger_trade_id (NULL = not yet matched)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(schwab_fills)")}
    if "ledger_trade_id" not in cols:
        conn.execute("ALTER TABLE schwab_fills ADD COLUMN ledger_trade_id INTEGER")
        conn.commit()


# ─── Structure inference ──────────────────────────────────────────────

def _per_share_net(legs: list[dict]) -> float:
    """Signed per-share cash flow: + = credit received, − = debit paid."""
    contracts = max(abs(l["quantity"]) for l in legs)
    return round(sum(-l["quantity"] * l["price"] for l in legs) / contracts, 4)


def infer_vertical(legs: list[dict]) -> dict | None:
    """Classify a 2-leg same-expiry, same-type, equal-qty option order.

    Returns {spread_type, short_strike, long_strike, width, shares} or None
    if the group is not a clean vertical.
    """
    if len(legs) != 2:
        return None
    a, b = legs
    if a["expiry"] != b["expiry"] or a["put_call"] != b["put_call"]:
        return None
    if abs(a["quantity"]) != abs(b["quantity"]):
        return None
    shorts = [l for l in legs if l["quantity"] < 0]
    longs = [l for l in legs if l["quantity"] > 0]
    if len(shorts) != 1 or len(longs) != 1:
        return None
    s_k, l_k = shorts[0]["strike"], longs[0]["strike"]
    if a["put_call"] == "PUT":
        spread_type = "bull_put" if s_k > l_k else "bear_put"
    else:
        spread_type = "bear_call" if s_k < l_k else "bull_call"
    return {
        "spread_type": spread_type,
        "short_strike": s_k,
        "long_strike": l_k,
        "width": round(abs(s_k - l_k), 4),
        "shares": int(abs(a["quantity"])),
    }


def infer_single(leg: dict) -> dict:
    """Single-leg option order → long_*/short_* row (0-sentinel strikes per
    the stock-row convention: unused strike fields are 0)."""
    long_side = leg["quantity"] > 0
    side = "long" if long_side else "short"
    kind = "put" if leg["put_call"] == "PUT" else "call"
    return {
        "spread_type": f"{side}_{kind}",
        "short_strike": 0.0 if long_side else leg["strike"],
        "long_strike": leg["strike"] if long_side else 0.0,
        "width": 0.0,
        "shares": int(abs(leg["quantity"])),
    }


# ─── Matcher ──────────────────────────────────────────────────────────

def _unlinked_option_fills(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM schwab_fills WHERE asset_type='OPTION' "
        "AND ledger_trade_id IS NULL AND status='VALID' ORDER BY time"
    ).fetchall()
    return [dict(r) for r in rows]


def _link(conn: sqlite3.Connection, legs: list[dict], trade_id: int) -> None:
    conn.executemany(
        "UPDATE schwab_fills SET ledger_trade_id=? WHERE activity_id=?",
        [(trade_id, l["activity_id"]) for l in legs])


def match_fills_to_ledger(conn: sqlite3.Connection) -> dict:
    """Match unlinked option fills to spread_score_trades. Auto-creates live
    rows for clean opens, auto-closes on clean closes, links already-recorded
    trades, and flags everything it can't safely handle.

    Returns {"opened": [...], "closed": [...], "linked": [...], "flagged": [...]}
    of human-readable lines.
    """
    ensure_link_column(conn)
    out = {"opened": [], "closed": [], "linked": [], "flagged": []}
    fills = _unlinked_option_fills(conn)
    if not fills:
        return out

    by_order: dict = defaultdict(list)
    for f in fills:
        by_order[f["order_id"]].append(f)

    for order_id, legs in sorted(by_order.items(), key=lambda kv: kv[1][0]["time"]):
        underlying = legs[0]["underlying"]
        effects = {l["position_effect"] for l in legs}
        desc = (f"{underlying} order {order_id} "
                f"({len(legs)} leg(s), {legs[0]['trade_date']})")

        # Already recorded? Link by order id, both directions.
        row = conn.execute(
            "SELECT id, status FROM spread_score_trades "
            "WHERE open_order_id=? OR close_order_id=?",
            (order_id, order_id)).fetchone()
        if row:
            _link(conn, legs, row[0])
            out["linked"].append(f"{desc} → already recorded as trade id {row[0]}")
            continue

        if effects == {"OPENING"}:
            _handle_open(conn, order_id, legs, desc, out)
        elif effects == {"CLOSING"}:
            _handle_close(conn, order_id, legs, desc, out)
        else:
            out["flagged"].append(
                f"{desc}: mixed OPENING+CLOSING (roll?) — NOT auto-recorded; "
                f"record manually and the next run will link it")
    conn.commit()
    return out


def _handle_open(conn, order_id, legs, desc, out) -> None:
    info = infer_vertical(legs) if len(legs) == 2 else (
        infer_single(legs[0]) if len(legs) == 1 else None)
    if info is None:
        out["flagged"].append(
            f"{desc}: structure not auto-recognized (3+ legs / mixed expiry "
            f"or type / unequal qty) — NOT auto-recorded; record manually")
        return

    net = _per_share_net(legs)
    fees = round(sum(l["fees"] or 0 for l in legs), 2)
    underlying = legs[0]["underlying"]
    cur = conn.execute(
        """INSERT INTO spread_score_trades
           (symbol, opex_date, spread_type, short_strike, long_strike, width,
            entry_credit, entry_date, entry_price, status, placed, shares,
            sector, fees_total, open_order_id, account)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (underlying, legs[0]["expiry"], info["spread_type"],
         info["short_strike"], info["long_strike"], info["width"],
         net, legs[0]["trade_date"], net, "open", 1, info["shares"],
         get_sector(underlying), fees, order_id, "live"))
    _link(conn, legs, cur.lastrowid)
    side = "credit" if net >= 0 else "debit"
    if info["spread_type"].startswith("short_"):
        out["flagged"].append(
            f"{desc}: NAKED SHORT {legs[0]['put_call']} recorded as trade id "
            f"{cur.lastrowid} — violates defined-risk-only; review immediately")
    out["opened"].append(
        f"LIVE OPEN recorded: {underlying} {info['spread_type']} "
        f"{info['short_strike']:g}/{info['long_strike']:g} ×{info['shares']} "
        f"@ {abs(net):.2f} {side} (OpEx {legs[0]['expiry']}) → trade id {cur.lastrowid}")


def _handle_close(conn, order_id, legs, desc, out) -> None:
    underlying = legs[0]["underlying"]
    strikes = sorted(l["strike"] for l in legs)
    qty = max(abs(l["quantity"]) for l in legs)
    conn.row_factory = sqlite3.Row
    candidates = [dict(r) for r in conn.execute(
        "SELECT * FROM spread_score_trades WHERE account='live' "
        "AND status='open' AND symbol=? AND opex_date=?",
        (underlying, legs[0]["expiry"]))]
    match = [c for c in candidates
             if sorted([c["short_strike"], c["long_strike"]])
             == strikes or (len(legs) == 1 and legs[0]["strike"] in
                            (c["short_strike"], c["long_strike"]))]
    if not match:
        out["flagged"].append(
            f"{desc}: CLOSING fills with no matching open live row "
            f"(strikes {strikes}) — close NOT recorded; reconcile manually")
        return
    trade = match[0]
    if qty != trade["shares"]:
        out["flagged"].append(
            f"{desc}: PARTIAL close ({qty} of {trade['shares']}) on trade id "
            f"{trade['id']} — NOT auto-closed; record manually")
        return

    close_net = _per_share_net(legs)          # − = debit paid to close
    fees = round(sum(l["fees"] or 0 for l in legs), 2)
    final_pnl = round((trade["entry_credit"] + close_net) * 100 * trade["shares"], 2)
    conn.execute(
        """UPDATE spread_score_trades
           SET exit_date=?, exit_price=?, final_pnl=?, status='closed',
               exit_type='manual_close', close_order_id=?,
               fees_total=COALESCE(fees_total,0)+?
           WHERE id=?""",
        (legs[0]["trade_date"], abs(close_net), final_pnl, order_id,
         fees, trade["id"]))
    _link(conn, legs, trade["id"])
    out["closed"].append(
        f"LIVE CLOSE recorded: {underlying} {trade['spread_type']} "
        f"{trade['short_strike']:g}/{trade['long_strike']:g} closed @ "
        f"{abs(close_net):.2f} → P/L ${final_pnl:+.0f} gross "
        f"(trade id {trade['id']})")


def render_summary(result: dict) -> str:
    """One-line-per-event text block for the ingest log / alert."""
    lines = []
    for key, prefix in (("opened", "✚"), ("closed", "✔"),
                        ("linked", "·"), ("flagged", "⚠")):
        for msg in result[key]:
            lines.append(f"  {prefix} {msg}")
    return "\n".join(lines)
