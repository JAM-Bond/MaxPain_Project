"""Unit tests for lib.schwab_account parsing + idempotent fills ingestion.

Standalone (no pytest): `python3.11 -m tests.test_schwab_account`. Pure-function
tests use synthetic Schwab payloads (the live account currently holds only
CDs/T-bills, so the option-fill path has no real data to exercise yet — these
fixtures model Schwab's documented TRADE/transferItems schema).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.schwab_account import (  # noqa: E402
    parse_occ_symbol, derive_action, parse_trade_transaction,
    ensure_fills_table, upsert_fills,
)

# Real CD transaction shape (observed 2026-06-09).
CD_TXN = {
    "activityId": 120993913742, "time": "2026-06-04T15:40:00+0000",
    "tradeDate": "2026-06-04T15:40:00+0000", "orderId": 1006611354713,
    "positionId": 3429120263, "netAmount": -100000.0, "type": "TRADE",
    "status": "VALID", "subAccount": "CASH",
    "transferItems": [{
        "instrument": {"assetType": "FIXED_INCOME", "symbol": "64034KEE7",
                       "description": "Nelnet Bank UT 3.85% CD 07/10/2026"},
        "amount": 100.0, "cost": -100000.0, "price": 100.0,
        "positionEffect": "OPENING"}],
}
# Synthetic option BTO with explicit instrument fields + two fee items.
OPT_BTO = {
    "activityId": 999001, "time": "2026-06-05T14:30:00+0000",
    "tradeDate": "2026-06-05T14:30:00+0000", "orderId": 555, "positionId": 777,
    "netAmount": -2468.65, "type": "TRADE", "status": "VALID", "subAccount": "MARGIN",
    "transferItems": [
        {"instrument": {"assetType": "OPTION", "symbol": "QQQ   270319P00615000",
                        "putCall": "PUT", "underlyingSymbol": "QQQ",
                        "expirationDate": "2027-03-19T20:00:00+0000", "strikePrice": 615.0},
         "amount": 2.0, "cost": -2468.0, "price": 12.34, "positionEffect": "OPENING"},
        {"feeType": "COMMISSION", "cost": -0.65, "amount": 0.65},
        {"feeType": "OPT_REG_FEE", "cost": 0.0, "amount": 0.0},
    ],
}
# Synthetic option STC close WITHOUT explicit fields → OCC-symbol fallback.
OPT_STC = {
    "activityId": 999002, "time": "2026-06-06T15:00:00+0000",
    "tradeDate": "2026-06-06T15:00:00+0000", "orderId": 556, "positionId": 777,
    "netAmount": 2999.35, "type": "TRADE", "status": "VALID", "subAccount": "MARGIN",
    "transferItems": [
        {"instrument": {"assetType": "OPTION", "symbol": "AAPL  260116C00150000"},
         "amount": 2.0, "cost": 3000.0, "price": 15.0, "positionEffect": "CLOSING"},
        {"feeType": "COMMISSION", "cost": -0.65, "amount": 0.65},
    ],
}


def test_parse_occ():
    o = parse_occ_symbol("QQQ   270319P00615000")
    assert o == {"underlying": "QQQ", "expiry": "2027-03-19", "put_call": "PUT", "strike": 615.0}, o
    c = parse_occ_symbol("AAPL  260116C00150000")
    assert c["put_call"] == "CALL" and c["strike"] == 150.0 and c["expiry"] == "2026-01-16", c
    assert parse_occ_symbol("64034KEE7") is None        # a CUSIP isn't an option
    print("  ✓ parse_occ_symbol: OCC put/call/strike/expiry + non-option rejected")


def test_derive_action():
    assert derive_action(-100.0, "OPENING") == "BTO"
    assert derive_action(100.0, "OPENING") == "STO"
    assert derive_action(-100.0, "CLOSING") == "BTC"
    assert derive_action(100.0, "CLOSING") == "STC"
    assert derive_action(None, "OPENING") is None
    print("  ✓ derive_action: BTO/STO/BTC/STC from cost sign + positionEffect")


def test_parse_cd():
    f = parse_trade_transaction(CD_TXN)
    assert f["asset_type"] == "FIXED_INCOME" and f["action"] == "BTO" and f["fees"] == 0.0
    assert f["activity_id"] == 120993913742 and f["net_amount"] == -100000.0
    print("  ✓ parse_trade_transaction: CD fill (no fees, BTO)")


def test_parse_option_explicit_fields():
    f = parse_trade_transaction(OPT_BTO)
    assert f["asset_type"] == "OPTION" and f["action"] == "BTO"
    assert f["underlying"] == "QQQ" and f["put_call"] == "PUT" and f["strike"] == 615.0
    assert f["expiry"] == "2027-03-19" and f["quantity"] == 2.0 and f["price"] == 12.34
    assert f["fees"] == 0.65, f["fees"]                 # summed across fee items
    assert f["n_instrument_legs"] == 1
    print("  ✓ parse_trade_transaction: option BTO, explicit fields, fees summed")


def test_parse_option_occ_fallback():
    f = parse_trade_transaction(OPT_STC)
    assert f["action"] == "STC" and f["underlying"] == "AAPL"   # parsed from OCC symbol
    assert f["put_call"] == "CALL" and f["strike"] == 150.0 and f["expiry"] == "2026-01-16"
    assert f["fees"] == 0.65 and f["net_amount"] == 2999.35
    print("  ✓ parse_trade_transaction: option STC, OCC fallback (no explicit fields)")


def test_fee_only_transaction_skipped():
    assert parse_trade_transaction({"activityId": 1, "transferItems": [
        {"feeType": "COMMISSION", "cost": -0.65}]}) is None
    print("  ✓ fee-only / no-instrument transaction → None")


def test_upsert_idempotent():
    conn = sqlite3.connect(":memory:")
    ensure_fills_table(conn)
    fills = [parse_trade_transaction(t) for t in (CD_TXN, OPT_BTO, OPT_STC)]
    n1 = upsert_fills(conn, fills, "2026-06-09T00:00:00")
    n2 = upsert_fills(conn, fills, "2026-06-09T00:05:00")   # same activityIds
    total = conn.execute("SELECT COUNT(*) FROM schwab_fills").fetchone()[0]
    assert n1 == 3 and n2 == 0 and total == 3, (n1, n2, total)
    print("  ✓ upsert_fills idempotent on activity_id (3 new, 0 dup)")


def main():
    print("schwab_account — unit tests")
    test_parse_occ()
    test_derive_action()
    test_parse_cd()
    test_parse_option_explicit_fields()
    test_parse_option_occ_fallback()
    test_fee_only_transaction_skipped()
    test_upsert_idempotent()
    print("ALL PASSED")


if __name__ == "__main__":
    main()
