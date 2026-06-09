"""Read-only Schwab Trader-API account access: positions, balances, and TRADE
transactions (fills) with fee extraction.

Verified 2026-06-09: the existing market-data token (`get_valid_token`, scope
`readonly`) is authorized for `/trader/v1/accounts/*` — see
reference_schwab_account_api_access. This module is the plumbing for the go-live
fills ingestion (scripts/maintenance/ingest_schwab_fills.py): Schwab supplies
positions, realized P/L (netAmount) and fees (transferItems feeType) directly,
so the live book no longer depends on the manual close protocol.

Scope is read-only — nothing here can place or modify an order.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

import requests

from lib.schwab_options import get_valid_token

BASE = "https://api.schwabapi.com/trader/v1"
TIMEOUT = 25


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_valid_token()}", "Accept": "application/json"}


def account_hash() -> str:
    """Return the hashValue for the (first) account — the id used in all
    subsequent /accounts/{hash}/... calls (never the raw account number)."""
    r = requests.get(f"{BASE}/accounts/accountNumbers", headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise RuntimeError("Schwab returned no accounts")
    return data[0]["hashValue"]


def fetch_account(hash_value: Optional[str] = None) -> dict:
    """Full securitiesAccount incl. positions + currentBalances."""
    h = hash_value or account_hash()
    r = requests.get(f"{BASE}/accounts/{h}", headers=_headers(),
                     params={"fields": "positions"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["securitiesAccount"]


def fetch_transactions(start_iso: str, end_iso: str, *, types: str = "TRADE",
                       hash_value: Optional[str] = None) -> list[dict]:
    """TRADE transactions in [start,end]. Schwab caps the window ~1yr; the
    caller pages if it needs more. Dates are ISO8601 Z strings."""
    h = hash_value or account_hash()
    r = requests.get(f"{BASE}/accounts/{h}/transactions", headers=_headers(),
                     params={"startDate": start_iso, "endDate": end_iso, "types": types},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Parsing (pure functions — unit-testable without the network) ────────────

_OCC_RE = re.compile(r"^([A-Z0-9.]+?)(\d{6})([CP])(\d{8})$")


def parse_occ_symbol(symbol: str) -> Optional[dict]:
    """Parse an OCC option symbol ('AAPL  260116C00150000') into its parts.
    Returns None if it doesn't look like an option symbol."""
    s = (symbol or "").replace(" ", "")
    m = _OCC_RE.match(s)
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    return {
        "underlying": root,
        "expiry": f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}",
        "put_call": "PUT" if cp == "P" else "CALL",
        "strike": int(strike) / 1000.0,
    }


def derive_action(cost: Optional[float], position_effect: Optional[str]) -> Optional[str]:
    """BTO / STO / BTC / STC from cash direction (cost<0 = buy) + positionEffect."""
    if cost is None or position_effect is None:
        return None
    side = "B" if cost < 0 else "S"
    eff = "O" if str(position_effect).upper().startswith("OPEN") else "C"
    return {"BO": "BTO", "SO": "STO", "BC": "BTC", "SC": "STC"}.get(side + eff)


_FEE_TYPES = {"COMMISSION", "OPT_REG_FEE", "ADDITIONAL_FEE", "REG_FEE",
              "SEC_FEE", "TAF_FEE", "INDEX_OPTION_FEE", "BASE_CHARGE",
              "MISCELLANEOUS_FEE", "EXCHANGE_FEE"}


def parse_trade_transaction(txn: dict) -> Optional[dict]:
    """Flatten one Schwab TRADE transaction into a single fill record.

    transferItems splits into (a) the instrument leg(s) and (b) fee items
    (those carrying `feeType`). Normal case = one instrument leg per
    transaction (Schwab books each spread leg as its own activityId); if a
    transaction carries >1 instrument leg we keep the first and record
    n_instrument_legs so the anomaly is visible. Fees are summed across the
    transaction. Returns None for a transaction with no instrument leg."""
    items = txn.get("transferItems", []) or []
    fees = 0.0
    instrument_items = []
    for it in items:
        if it.get("feeType") in _FEE_TYPES or ("feeType" in it and "instrument" not in it):
            fees += abs(float(it.get("cost") or it.get("amount") or 0.0))
            continue
        if it.get("instrument"):
            instrument_items.append(it)
    if not instrument_items:
        return None
    leg = instrument_items[0]
    ins = leg["instrument"]
    asset = ins.get("assetType")
    symbol = ins.get("symbol", "")
    occ = parse_occ_symbol(symbol) if asset == "OPTION" else None
    # prefer explicit instrument fields when Schwab provides them
    underlying = ins.get("underlyingSymbol") or (occ["underlying"] if occ else symbol)
    put_call = ins.get("putCall") or (occ["put_call"] if occ else None)
    strike = ins.get("strikePrice") if ins.get("strikePrice") is not None else (occ["strike"] if occ else None)
    expiry = (str(ins["expirationDate"])[:10] if ins.get("expirationDate")
              else (occ["expiry"] if occ else None))
    cost = float(leg.get("cost")) if leg.get("cost") is not None else None
    return {
        "activity_id": txn.get("activityId"),
        "order_id": txn.get("orderId"),
        "position_id": txn.get("positionId"),
        "time": txn.get("time"),
        "trade_date": str(txn.get("tradeDate") or "")[:10],
        "status": txn.get("status"),
        "sub_account": txn.get("subAccount"),
        "asset_type": asset,
        "symbol": symbol,
        "underlying": underlying,
        "put_call": put_call,
        "strike": strike,
        "expiry": expiry,
        "quantity": float(leg.get("amount")) if leg.get("amount") is not None else None,
        "price": float(leg.get("price")) if leg.get("price") is not None else None,
        "cost": cost,
        "action": derive_action(cost, leg.get("positionEffect")),
        "position_effect": leg.get("positionEffect"),
        "fees": round(fees, 4),
        "net_amount": float(txn["netAmount"]) if txn.get("netAmount") is not None else None,
        "n_instrument_legs": len(instrument_items),
    }


# ── Idempotent fills ingestion ──────────────────────────────────────────────

FILL_COLUMNS = [
    "activity_id", "order_id", "position_id", "time", "trade_date", "status",
    "sub_account", "asset_type", "symbol", "underlying", "put_call", "strike",
    "expiry", "quantity", "price", "cost", "action", "position_effect", "fees",
    "net_amount", "n_instrument_legs",
]


def ensure_fills_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS schwab_fills (
            {', '.join(c + (' INTEGER PRIMARY KEY' if c == 'activity_id' else '') for c in FILL_COLUMNS)},
            ingested_at TEXT
        )""")
    conn.commit()


def upsert_fills(conn: sqlite3.Connection, fills: list[dict], ingested_at: str) -> int:
    """INSERT OR IGNORE parsed fills (idempotent on activity_id). Returns rows
    newly inserted."""
    ensure_fills_table(conn)
    before = conn.execute("SELECT COUNT(*) FROM schwab_fills").fetchone()[0]
    cols = FILL_COLUMNS + ["ingested_at"]
    ph = ", ".join(["?"] * len(cols))
    conn.executemany(
        f"INSERT OR IGNORE INTO schwab_fills ({', '.join(cols)}) VALUES ({ph})",
        [[f.get(c) for c in FILL_COLUMNS] + [ingested_at] for f in fills])
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM schwab_fills").fetchone()[0] - before
