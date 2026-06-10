"""Go-live order reconciler — READ-ONLY Schwab orders -> leg-level mirror + book.

⚠️ NEVER places, modifies, or cancels an order ([[feedback_never_execute_trades]]).
It only READS filled Schwab orders and WRITES our own SQLite.

Two-layer design (user 2026-06-10):
  1. `order_legs` — a faithful LEG-LEVEL mirror of Schwab, PRIMARY KEY
     (order_id, leg_id). order_id is shared across a spread's legs; leg_id is unique
     within the order. The compound key prevents duplicate inserts and stores each
     leg's own fill price + fees, so net P/L = Σ(signed leg cash flows) − fees works
     for ANY leg count (verticals through 3+ leg zebra / inverted-fly / iron-condor).
  2. `spread_score_trades` — the spread-level book the rest of the system reads,
     DERIVED from the legs: an OPENING order opens a position; a CLOSING order whose
     contracts match an open position closes it with exact net-of-fees P/L.

Per-share signed cash flow per leg: SELL = +fill_price, BUY = −fill_price.
  entry_credit = Σ opening legs ; exit_credit = −(Σ closing legs)
  final_pnl    = (entry_credit − exit_credit) × 100 × shares − fees     [== Σ all-leg
                 signed cash flow × 100 × shares − fees]

Safety: legs are idempotent on (order_id, leg_id); positions on open/close_order_id.
Ambiguous closes (0 or >1 matching open positions), rolls (mixed open+close in one
order), and unknown structures are FLAGGED for manual review, never guessed.
Default mode is DRY-RUN.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from lib.db import DB_PATH
from lib.schwab_account import BASE, _headers, account_hash, parse_occ_symbol

TIMEOUT = 60


# ── read-only fetch ──────────────────────────────────────────────────────────
def fetch_filled_orders(days: int = 5, hash_value: str | None = None) -> list[dict]:
    """READ-ONLY: FILLED orders entered in the last `days`."""
    h = hash_value or account_hash()
    now = datetime.now(timezone.utc)
    params = {
        "maxResults": 200, "status": "FILLED",
        "fromEnteredTime": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "toEnteredTime": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    r = requests.get(f"{BASE}/accounts/{h}/orders", headers=_headers(),
                     params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── per-leg execution prices + fees ──────────────────────────────────────────
def _exec_prices(order: dict) -> dict[int, float]:
    """{leg_id: qty-weighted avg fill price} from orderActivityCollection."""
    acc: dict[int, list[tuple[float, float]]] = {}
    for act in order.get("orderActivityCollection", []) or []:
        for el in act.get("executionLegs", []) or []:
            lid = el.get("legId")
            if lid is None or el.get("price") is None:
                continue
            acc.setdefault(lid, []).append((float(el["price"]), float(el.get("quantity") or 0)))
    out = {}
    for lid, fills in acc.items():
        tot_q = sum(q for _, q in fills) or 0.0
        out[lid] = (sum(p * q for p, q in fills) / tot_q) if tot_q else fills[0][0]
    return out


def _leg_fees(conn: sqlite3.Connection, order_id: str, symbol: str) -> float:
    """Per-leg fees from schwab_fills, matched by (order_id, symbol). schwab_fills
    stores order_id as INTEGER and the OCC symbol with padding — compare as TEXT,
    spaces stripped."""
    try:
        v = conn.execute(
            "SELECT COALESCE(SUM(fees),0) FROM schwab_fills "
            "WHERE CAST(order_id AS TEXT)=? AND REPLACE(symbol,' ','')=REPLACE(?,' ','')",
            (str(order_id), symbol)).fetchone()[0]
        return round(float(v or 0.0), 2)
    except Exception:
        return 0.0


def _sign(instruction: str) -> int:
    return 1 if "SELL" in (instruction or "") else -1   # SELL = cash in (+)


def net_pnl(entry_credit: float, exit_credit: float, shares: int, fees_total: float) -> float:
    """Total net P/L for a position. entry_credit = Σ opening legs (SELL +, BUY −);
    exit_credit = −(Σ closing legs). Equals Σ all-leg signed cash flow × 100 × shares
    − fees, for any leg count."""
    return round((entry_credit - exit_credit) * 100 * shares - fees_total, 2)


# ── normalize an order into legs + a spread-level summary ─────────────────────
def summarize_order(order: dict, conn: sqlite3.Connection) -> tuple[list[dict], dict] | None:
    """Returns (leg_rows, summary) for an OPTION order, or None for non-option
    (bond/CD/equity) orders. leg_rows are ready for order_legs; summary carries the
    derived spread-level fields."""
    legs = order.get("orderLegCollection", []) or []
    opts = [l for l in legs if l.get("instrument", {}).get("assetType") == "OPTION"]
    if not opts:
        return None

    oid = str(order.get("orderId"))
    otype = order.get("orderType", "")
    status = order.get("status")
    entered = order.get("enteredTime")
    closed = order.get("closeTime")
    prices = _exec_prices(order)

    leg_rows, parsed = [], []
    for l in opts:
        sym = l["instrument"].get("symbol", "")
        occ = parse_occ_symbol(sym)
        lid = l.get("legId")
        fp = round(float(prices.get(lid, 0.0)), 4)
        fee = _leg_fees(conn, oid, sym)
        leg_rows.append({
            "order_id": oid, "leg_id": lid,
            "underlying": (occ or {}).get("underlying"), "symbol": sym,
            "asset_type": "OPTION", "put_call": (occ or {}).get("put_call"),
            "strike": (occ or {}).get("strike"), "expiry": (occ or {}).get("expiry"),
            "instruction": l.get("instruction"), "position_effect": l.get("positionEffect"),
            "quantity": float(l.get("quantity") or 0), "fill_price": fp, "fees": fee,
            "order_type": otype, "status": status,
            "entered_time": entered, "execution_time": closed,
        })
        parsed.append((l, occ, fp))

    effects = {l.get("positionEffect") for l in opts}
    side = ("open" if effects <= {"OPENING"} else
            "close" if effects <= {"CLOSING"} else "mixed")
    putcalls = {(occ or {}).get("put_call") for _, occ, _ in parsed}
    strikes = sorted({(occ or {}).get("strike") for _, occ, _ in parsed if occ})
    n = len(opts)

    if n == 2 and putcalls == {"PUT"}:
        st, short_k, long_k = "bull_put", max(strikes), min(strikes)
    elif n == 2 and putcalls == {"CALL"}:
        st, short_k, long_k = "bear_call", min(strikes), max(strikes)
    elif n == 1 and putcalls == {"PUT"}:
        st, short_k, long_k = "long_put", 0.0, strikes[0]
    elif n == 3 and putcalls == {"CALL"}:
        # zebra: short = the sold call, long = the bought calls
        sells = [o["strike"] for l, o, _ in parsed if _sign(l.get("instruction")) > 0 and o]
        buys = [o["strike"] for l, o, _ in parsed if _sign(l.get("instruction")) < 0 and o]
        st, short_k, long_k = "zebra", (sells[0] if sells else max(strikes)), (buys[0] if buys else min(strikes))
    elif n == 4:
        st, short_k, long_k = "inverted_fly", max(strikes), min(strikes)
    else:
        st, short_k, long_k = "unknown", (max(strikes) if strikes else 0.0), (min(strikes) if strikes else 0.0)

    # signed per-share cash flow for THIS order (SELL +, BUY −)
    net_per_share = round(sum(_sign(l.get("instruction")) * fp for l, _, fp in parsed), 4)
    symbols = frozenset(s["symbol"].replace(" ", "") for s in leg_rows)
    fees_total = round(sum(s["fees"] for s in leg_rows), 2)

    summary = {
        "order_id": oid, "side": side, "spread_type": st,
        "underlying": parsed[0][1]["underlying"] if parsed[0][1] else None,
        "expiry": parsed[0][1]["expiry"] if parsed[0][1] else None,
        "short_strike": float(short_k), "long_strike": float(long_k),
        "quantity": int(float(order.get("quantity") or 0)),
        "net_per_share": net_per_share, "symbols": symbols, "fees_total": fees_total,
        "fill_date": str(closed or entered or "")[:10],
        "recordable": side in ("open", "close") and st != "unknown",
        "reason": ("" if side in ("open", "close") and st != "unknown"
                   else f"side={side}, structure={st}"),
    }
    return leg_rows, summary


# ── order_legs mirror upsert ─────────────────────────────────────────────────
_LEG_COLS = ["order_id", "leg_id", "underlying", "symbol", "asset_type", "put_call",
             "strike", "expiry", "instruction", "position_effect", "quantity",
             "fill_price", "fees", "order_type", "status", "entered_time",
             "execution_time"]


def upsert_order_legs(conn: sqlite3.Connection, legs: list[dict], ingested_at: str,
                      dry_run: bool) -> int:
    """INSERT OR IGNORE legs into the mirror (idempotent on (order_id, leg_id))."""
    if dry_run or not legs:
        # count how many would be new
        new = 0
        for s in legs:
            hit = conn.execute("SELECT 1 FROM order_legs WHERE order_id=? AND leg_id=?",
                               (s["order_id"], s["leg_id"])).fetchone()
            if not hit:
                new += 1
        return new
    before = conn.execute("SELECT COUNT(*) FROM order_legs").fetchone()[0]
    cols = _LEG_COLS + ["ingested_at"]
    ph = ", ".join(["?"] * len(cols))
    conn.executemany(
        f"INSERT OR IGNORE INTO order_legs ({', '.join(cols)}) VALUES ({ph})",
        [[s.get(c) for c in _LEG_COLS] + [ingested_at] for s in legs])
    return conn.execute("SELECT COUNT(*) FROM order_legs").fetchone()[0] - before


# ── match a closing order to an open position by its contract set ─────────────
def _open_symbol_set(conn: sqlite3.Connection, open_order_id: str) -> frozenset:
    rows = conn.execute("SELECT symbol FROM order_legs WHERE order_id=?",
                        (open_order_id,)).fetchall()
    return frozenset((r[0] or "").replace(" ", "") for r in rows)


def _match_open_position(conn: sqlite3.Connection, summary: dict) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    candidates = conn.execute(
        "SELECT * FROM spread_score_trades WHERE status='open' AND symbol=? AND opex_date=?",
        (summary["underlying"], summary["expiry"])).fetchall()
    out = []
    for r in candidates:
        if r["open_order_id"] and _open_symbol_set(conn, r["open_order_id"]) == summary["symbols"]:
            out.append(r)
    # fallback: strike-based match for legacy rows that have no recorded legs
    if not out:
        for r in candidates:
            if (r["open_order_id"] is None
                    and r["spread_type"] == summary["spread_type"]
                    and abs((r["short_strike"] or 0) - summary["short_strike"]) < 0.001
                    and abs((r["long_strike"] or 0) - summary["long_strike"]) < 0.001):
                out.append(r)
    return out


# ── reconcile ────────────────────────────────────────────────────────────────
def reconcile(days: int = 5, dry_run: bool = True) -> dict:
    orders = fetch_filled_orders(days=days)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ingested_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rep = {"legs_new": 0, "inserts": [], "links": [], "closes": [], "flags": [], "skipped": []}

    for o in orders:
        res = summarize_order(o, conn)
        if res is None:
            continue
        legs, s = res
        rep["legs_new"] += upsert_order_legs(conn, legs, ingested_at, dry_run)  # mirror ALWAYS

        oid = s["order_id"]
        if not s["recordable"]:
            rep["flags"].append({**{k: s[k] for k in ("order_id", "side", "spread_type",
                                  "underlying")}, "flag": f"{s['reason']} — manual confirm"})
            continue

        if s["side"] == "open":
            if conn.execute("SELECT 1 FROM spread_score_trades WHERE open_order_id=?",
                            (oid,)).fetchone():
                rep["skipped"].append({"order_id": oid, "why": "open already recorded"})
                continue
            matches = _match_open_position(conn, s)  # link a legacy manual row if present
            link = next((m for m in matches if m["open_order_id"] is None), None)
            if link is not None:
                rep["links"].append({"order_id": oid, "trade_id": link["id"],
                                     "desc": f"{s['underlying']} {s['spread_type']} "
                                             f"{s['short_strike']:g}/{s['long_strike']:g}"})
                if not dry_run:
                    conn.execute("UPDATE spread_score_trades SET open_order_id=? WHERE id=?",
                                 (oid, link["id"]))
            else:
                ins = {"symbol": s["underlying"], "opex_date": s["expiry"],
                       "spread_type": s["spread_type"], "short_strike": s["short_strike"],
                       "long_strike": s["long_strike"],
                       "width": abs(s["short_strike"] - s["long_strike"]),
                       "entry_credit": s["net_per_share"], "entry_date": s["fill_date"],
                       "shares": s["quantity"], "status": "open", "placed": 1,
                       "open_order_id": oid, "fees_total": s["fees_total"]}
                rep["inserts"].append(ins)
                if not dry_run:
                    cols = list(ins)
                    conn.execute(f"INSERT INTO spread_score_trades ({','.join(cols)}) "
                                 f"VALUES ({','.join('?' for _ in cols)})", [ins[k] for k in cols])

        elif s["side"] == "close":
            if conn.execute("SELECT 1 FROM spread_score_trades WHERE close_order_id=?",
                            (oid,)).fetchone():
                rep["skipped"].append({"order_id": oid, "why": "close already recorded"})
                continue
            matches = _match_open_position(conn, s)
            if len(matches) != 1:
                rep["flags"].append({"order_id": oid, "underlying": s["underlying"],
                                     "spread_type": s["spread_type"],
                                     "flag": f"{len(matches)} open positions match — manual"})
                continue
            row = matches[0]
            entry_credit = float(row["entry_credit"])
            shares = int(row["shares"] or s["quantity"] or 1)
            exit_credit = round(-s["net_per_share"], 4)   # close cash flow, sign-flipped
            open_fees = float(row["fees_total"] or 0.0)
            fees_total = round(open_fees + s["fees_total"], 2)
            pnl = net_pnl(entry_credit, exit_credit, shares, fees_total)
            rep["closes"].append({"trade_id": row["id"], "symbol": s["underlying"],
                                  "spread_type": s["spread_type"],
                                  "strikes": f"{s['short_strike']:g}/{s['long_strike']:g}",
                                  "entry_credit": entry_credit, "exit_credit": exit_credit,
                                  "shares": shares, "fees_total": fees_total,
                                  "final_pnl": pnl, "exit_date": s["fill_date"],
                                  "close_order_id": oid})
            if not dry_run:
                conn.execute(
                    "UPDATE spread_score_trades SET status='closed', exit_credit=?, "
                    "exit_date=?, final_pnl=?, fees_total=?, close_order_id=?, "
                    "exit_type='schwab_auto' WHERE id=?",
                    (exit_credit, s["fill_date"], pnl, fees_total, oid, row["id"]))

    if not dry_run:
        conn.commit()
    conn.close()
    return rep
