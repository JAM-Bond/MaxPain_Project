"""Go-live order reconciler — READ-ONLY Schwab orders -> our spread_score_trades.

⚠️ This module NEVER places, modifies, or cancels an order. It only READS filled
orders from Schwab and WRITES to our own SQLite book ([[feedback_never_execute_trades]]).
It automates what we have been doing by hand:

  - A FILLED order whose legs are all OPENING  -> a NEW position -> INSERT status='open'
    (entry_credit = order price, signed; shares = qty; strikes/structure from the legs).
  - A FILLED order whose legs are all CLOSING  -> match the open row and close it:
    status 'open'->'closed', exit_credit = closing price, net-of-fees final_pnl.

Net P/L = gross +/- (entry vs exit) x 100 x shares, MINUS Schwab fees summed from
`schwab_fills` (open order + close order), per user decision 2026-06-10.

Safety: idempotent on Schwab orderId (open_order_id / close_order_id, migration 008).
Ambiguous closes (0 or >1 matching open rows, rolls, unknown structures) are NEVER
guessed — they are returned as `flags` for manual confirmation.

Default mode is DRY-RUN: reconcile(dry_run=True) computes the plan and writes nothing.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from lib.db import DB_PATH
from lib.schwab_account import BASE, _headers, account_hash, parse_occ_symbol

TIMEOUT = 60

# Structures the reconciler will auto-record. Trickier multi-leg structures
# (zebra, inverted_fly) are classified but FLAGGED for manual confirmation rather
# than auto-written, because their short/long leg mapping is ambiguous from an
# order alone and the live volume is low.
_AUTO_STRUCTURES = {"bull_put", "bear_call", "long_put"}


# ── read-only fetch ──────────────────────────────────────────────────────────
def fetch_filled_orders(days: int = 5, hash_value: str | None = None) -> list[dict]:
    """READ-ONLY: FILLED orders entered in the last `days`."""
    h = hash_value or account_hash()
    now = datetime.now(timezone.utc)
    params = {
        "maxResults": 200,
        "status": "FILLED",
        "fromEnteredTime": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "toEnteredTime": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    r = requests.get(f"{BASE}/accounts/{h}/orders", headers=_headers(),
                     params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── classification ───────────────────────────────────────────────────────────
def classify_order(order: dict) -> dict | None:
    """Normalize one Schwab order into the fields the reconciler needs.

    Returns None for non-option orders (bonds/CDs/equity). For option orders,
    returns a dict with side ('open'|'close'|'mixed'), spread_type, underlying,
    expiry, short_strike, long_strike, put_call, qty, price, fill_date, plus a
    `recordable` flag (False -> must be flagged, not auto-written)."""
    legs = order.get("orderLegCollection", []) or []
    opts = [l for l in legs if l.get("instrument", {}).get("assetType") == "OPTION"]
    if not opts:
        return None  # bond/CD/equity — not our book

    parsed = []
    for l in opts:
        occ = parse_occ_symbol(l["instrument"].get("symbol", ""))
        if not occ:
            return {"order_id": order.get("orderId"), "recordable": False,
                    "reason": f"unparseable option symbol {l['instrument'].get('symbol')!r}"}
        parsed.append((l, occ))

    effects = {l.get("positionEffect") for l, _ in parsed}
    side = ("open" if effects <= {"OPENING"} else
            "close" if effects <= {"CLOSING"} else "mixed")

    putcalls = {occ["put_call"] for _, occ in parsed}
    strikes = sorted({occ["strike"] for _, occ in parsed})
    underlying = parsed[0][1]["underlying"]
    expiry = parsed[0][1]["expiry"]
    n = len(parsed)

    if n == 2 and putcalls == {"PUT"}:
        st, short_k, long_k = "bull_put", max(strikes), min(strikes)
    elif n == 2 and putcalls == {"CALL"}:
        st, short_k, long_k = "bear_call", min(strikes), max(strikes)
    elif n == 1 and putcalls == {"PUT"}:
        st, short_k, long_k = "long_put", 0.0, strikes[0]
    elif n == 3 and putcalls == {"CALL"}:
        st, short_k, long_k = "zebra", min(strikes), max(strikes)
    elif n == 4:
        st, short_k, long_k = "inverted_fly", max(strikes), min(strikes)
    else:
        st, short_k, long_k = "unknown", (strikes[0] if strikes else 0.0), (strikes[-1] if strikes else 0.0)

    # entry/exit price sign: NET_CREDIT positive, NET_DEBIT / single-leg debit negative.
    otype = order.get("orderType", "")
    price = float(order.get("price") or 0.0)
    if otype == "NET_CREDIT":
        signed_price = price
    elif otype == "NET_DEBIT":
        signed_price = -price
    else:  # single-leg LIMIT etc. — sign by instruction (BUY = debit paid)
        buy = any("BUY" in (l.get("instruction") or "") for l, _ in parsed)
        signed_price = -price if buy else price

    fill_date = str(order.get("closeTime") or order.get("enteredTime") or "")[:10]
    return {
        "order_id": str(order.get("orderId")),
        "side": side,
        "spread_type": st,
        "underlying": underlying,
        "expiry": expiry,
        "short_strike": float(short_k),
        "long_strike": float(long_k),
        "put_call": "PUT" if "PUT" in putcalls else "CALL",
        "qty": int(float(order.get("quantity") or 0)),
        "signed_price": round(signed_price, 4),
        "abs_price": round(price, 4),
        "fill_date": fill_date,
        "recordable": (side in ("open", "close")) and (st in _AUTO_STRUCTURES),
        "reason": ("" if (side in ("open", "close") and st in _AUTO_STRUCTURES)
                   else f"side={side}, structure={st} — manual confirm"),
    }


# ── fees + matching helpers ──────────────────────────────────────────────────
def _fees_for_order(conn: sqlite3.Connection, order_id) -> float:
    # schwab_fills.order_id is stored as INTEGER; reconciler order ids are strings.
    # CAST both sides to TEXT so the join matches regardless of storage class.
    if order_id is None:
        return 0.0
    try:
        v = conn.execute(
            "SELECT COALESCE(SUM(fees),0) FROM schwab_fills WHERE CAST(order_id AS TEXT)=?",
            (str(order_id),)).fetchone()[0]
        return round(float(v or 0.0), 2)
    except Exception:
        return 0.0


def _find_open_rows(conn: sqlite3.Connection, c: dict) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT * FROM spread_score_trades
           WHERE status='open' AND symbol=? AND spread_type=?
             AND opex_date=? AND ABS(short_strike-?)<0.001 AND ABS(long_strike-?)<0.001""",
        (c["underlying"], c["spread_type"], c["expiry"],
         c["short_strike"], c["long_strike"]),
    ).fetchall()


def _net_pnl(entry_credit: float, exit_signed_price: float, shares: int,
             fees_total: float) -> float:
    """Total net P/L. Credit spread: entry_credit>0, close is a debit paid
    (exit_signed_price<0 from NET_DEBIT) -> gross=(entry_credit-|exit|)*100*sh.
    Debit structure: entry_credit<0 (=-debit), close is a credit (exit>0) ->
    gross=(|exit|-|entry|)*100*sh. Both reduce to (entry+exit_as_pl)*... so we
    compute via signed economics: proceeds_in - cost_out."""
    # entry_credit already signed (credit +, debit -). Closing cash is the
    # opposite sign of the open: a credit spread pays to close (negative cash),
    # a debit position receives (positive). exit_signed_price carries that sign.
    gross = (entry_credit + exit_signed_price) * 100 * shares
    return round(gross - fees_total, 2)


# ── reconcile ────────────────────────────────────────────────────────────────
def reconcile(days: int = 5, dry_run: bool = True) -> dict:
    """Plan (and optionally apply) the open/close reconciliation. Returns a report
    dict: {inserts, links, closes, flags, skipped}. dry_run=True writes nothing."""
    orders = fetch_filled_orders(days=days)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    report = {"inserts": [], "links": [], "closes": [], "flags": [], "skipped": []}

    for o in orders:
        c = classify_order(o)
        if c is None:
            continue  # non-option order
        oid = c["order_id"]

        if not c["recordable"]:
            report["flags"].append({**c, "flag": "needs manual confirm"})
            continue

        if c["side"] == "open":
            # idempotent: already recorded as an opener?
            seen = conn.execute(
                "SELECT id FROM spread_score_trades WHERE open_order_id=?", (oid,)).fetchone()
            if seen:
                report["skipped"].append({"order_id": oid, "why": "open already recorded",
                                          "trade_id": seen[0]})
                continue
            # transition: link to a matching manually-entered open row if present
            matches = [r for r in _find_open_rows(conn, c) if r["open_order_id"] is None]
            if len(matches) == 1:
                tid = matches[0]["id"]
                report["links"].append({"order_id": oid, "trade_id": tid,
                                        "desc": f"{c['underlying']} {c['spread_type']} "
                                                f"{c['short_strike']:g}/{c['long_strike']:g}"})
                if not dry_run:
                    conn.execute("UPDATE spread_score_trades SET open_order_id=? WHERE id=?",
                                 (oid, tid))
            elif len(matches) > 1:
                report["flags"].append({**c, "flag": f"{len(matches)} open rows match this "
                                                     "opener — ambiguous link"})
            else:
                fees = _fees_for_order(conn, oid)
                ins = {
                    "symbol": c["underlying"], "opex_date": c["expiry"],
                    "spread_type": c["spread_type"], "short_strike": c["short_strike"],
                    "long_strike": c["long_strike"], "width": abs(c["short_strike"] - c["long_strike"]),
                    "entry_credit": c["signed_price"], "entry_date": c["fill_date"],
                    "shares": c["qty"], "status": "open", "placed": 1,
                    "open_order_id": oid, "fees_total": fees,
                }
                report["inserts"].append(ins)
                if not dry_run:
                    cols = list(ins.keys())
                    conn.execute(
                        f"INSERT INTO spread_score_trades ({','.join(cols)}) "
                        f"VALUES ({','.join('?' for _ in cols)})", [ins[k] for k in cols])

        elif c["side"] == "close":
            # idempotent: already recorded as a closer?
            seen = conn.execute(
                "SELECT id FROM spread_score_trades WHERE close_order_id=?", (oid,)).fetchone()
            if seen:
                report["skipped"].append({"order_id": oid, "why": "close already recorded",
                                          "trade_id": seen[0]})
                continue
            matches = _find_open_rows(conn, c)
            if len(matches) != 1:
                report["flags"].append({**c, "flag": f"{len(matches)} open rows match this "
                                        "closer — cannot close safely (manual)"})
                continue
            row = matches[0]
            entry_credit = float(row["entry_credit"])
            shares = int(row["shares"] or c["qty"] or 1)
            open_fees = _fees_for_order(conn, row["open_order_id"]) if row["open_order_id"] else 0.0
            close_fees = _fees_for_order(conn, oid)
            fees_total = round(open_fees + close_fees, 2)
            pnl = _net_pnl(entry_credit, c["signed_price"], shares, fees_total)
            close = {"trade_id": row["id"], "symbol": c["underlying"],
                     "spread_type": c["spread_type"],
                     "strikes": f"{c['short_strike']:g}/{c['long_strike']:g}",
                     "entry_credit": entry_credit, "exit_price": c["abs_price"],
                     "shares": shares, "fees_total": fees_total, "final_pnl": pnl,
                     "exit_date": c["fill_date"], "close_order_id": oid}
            report["closes"].append(close)
            if not dry_run:
                conn.execute(
                    "UPDATE spread_score_trades SET status='closed', exit_credit=?, "
                    "exit_date=?, final_pnl=?, fees_total=?, close_order_id=?, "
                    "exit_type='schwab_auto' WHERE id=?",
                    (c["abs_price"], c["fill_date"], pnl, fees_total, oid, row["id"]))

    if not dry_run:
        conn.commit()
    conn.close()
    return report
