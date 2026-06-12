"""Positions-vs-ledger reconciler — daily safety net for live trades (F5).

Compares the REAL option positions in the Schwab account (read-only Trader
API) against open `account='live'` rows in spread_score_trades. Either side
missing is a loud warning in the 16:45 alert:

  - broker position with no open live ledger row → the fills→ledger matcher
    missed it (or it was flagged-and-ignored): the position is running DARK.
  - open live ledger row with no broker position → it was closed/assigned/
    expired at the broker and the ledger doesn't know.

This is the guarantee layer: the matcher automates the common case; this
check makes the uncommon case impossible to miss. Read-only; never writes.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from lib.schwab_account import fetch_account, parse_occ_symbol


def broker_option_positions() -> dict[str, list[dict]]:
    """Live option positions grouped by underlying:
    {underlying: [{strike, put_call, expiry, net_qty}, ...]}"""
    acct = fetch_account()
    sec = acct.get("securitiesAccount", acct)
    out: dict[str, list[dict]] = defaultdict(list)
    for p in sec.get("positions", []):
        inst = p.get("instrument", {})
        if inst.get("assetType") != "OPTION":
            continue
        parsed = parse_occ_symbol(inst.get("symbol", ""))
        if not parsed:
            continue
        net = float(p.get("longQuantity", 0)) - float(p.get("shortQuantity", 0))
        if net == 0:
            continue
        out[parsed["underlying"]].append({**parsed, "net_qty": net})
    return dict(out)


def ledger_open_live(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(
        "SELECT id, symbol, spread_type, short_strike, long_strike, "
        "opex_date, shares FROM spread_score_trades "
        "WHERE account='live' AND status='open'")]


def reconcile(conn: sqlite3.Connection) -> list[str]:
    """Returns warning lines (empty list = book and broker agree)."""
    warnings: list[str] = []
    broker = broker_option_positions()
    ledger = ledger_open_live(conn)
    ledger_under = {r["symbol"] for r in ledger}

    for underlying, legs in sorted(broker.items()):
        if underlying not in ledger_under:
            leg_str = ", ".join(
                f"{'+' if l['net_qty'] > 0 else ''}{l['net_qty']:g} "
                f"{l['strike']:g}{l['put_call'][0]} {l['expiry']}" for l in legs)
            warnings.append(
                f"🚨 LIVE POSITION NOT IN LEDGER: {underlying} [{leg_str}] — "
                f"running DARK (no marks/breach/stop coverage). The fills "
                f"matcher flagged or missed it; record it in "
                f"spread_score_trades (account='live') today.")

    for r in ledger:
        if r["symbol"] not in broker:
            warnings.append(
                f"⚠ LEDGER OPEN BUT NO BROKER POSITION: {r['symbol']} "
                f"{r['spread_type']} {r['short_strike']:g}/{r['long_strike']:g} "
                f"(trade id {r['id']}, OpEx {r['opex_date']}) — closed/expired/"
                f"assigned at the broker? Reconcile and record the close.")
    return warnings
