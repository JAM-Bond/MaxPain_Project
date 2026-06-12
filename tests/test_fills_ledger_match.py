#!/usr/bin/env python3.11
"""Unit tests for lib/fills_ledger_match (go-live audit F5).

Runs against an in-memory DB using the REAL spread_score_trades schema
(copied from the live DB's sqlite_master) + the real schwab_fills DDL.

  python3.11 tests/test_fills_ledger_match.py
"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.fills_ledger_match import match_fills_to_ledger  # noqa: E402
from lib.schwab_account import ensure_fills_table  # noqa: E402
from lib.db import DB_PATH  # noqa: E402


def make_conn():
    live = sqlite3.connect(DB_PATH)
    ddl = live.execute(
        "SELECT sql FROM sqlite_master WHERE name='spread_score_trades'"
    ).fetchone()[0]
    live.close()
    conn = sqlite3.connect(":memory:")
    conn.execute(ddl)
    ensure_fills_table(conn)
    return conn


def fill(conn, aid, oid, und, pc, k, qty, px, eff,
         fees=0.66, td="2026-06-15", exp="2026-08-21"):
    conn.execute(
        "INSERT INTO schwab_fills (activity_id, order_id, time, trade_date, "
        "status, asset_type, symbol, underlying, put_call, strike, expiry, "
        "quantity, price, action, position_effect, fees) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (aid, oid, td + "T14:00:00+0000", td, "VALID", "OPTION",
         f"{und} {k}{pc[0]}", und, pc, k, exp, qty, px, "X", eff, fees))


def main():
    conn = make_conn()

    # 1. clean 2-lot bull_put open → live row
    fill(conn, 1, 100, "XYZ", "PUT", 95.0, -2, 1.80, "OPENING")
    fill(conn, 2, 100, "XYZ", "PUT", 90.0, 2, 0.95, "OPENING")
    r = match_fills_to_ledger(conn)
    assert len(r["opened"]) == 1 and "bull_put 95/90" in r["opened"][0], r
    tid = conn.execute("SELECT id FROM spread_score_trades WHERE symbol='XYZ'").fetchone()[0]
    acct, placed, shares, status = conn.execute(
        "SELECT account, placed, shares, status FROM spread_score_trades WHERE id=?",
        (tid,)).fetchone()
    assert (acct, placed, shares, status) == ("live", 1, 2, "open")
    print("  ✓ clean open → live row (credit 0.85, 2 lots)")

    # 2. idempotent re-run
    r = match_fills_to_ledger(conn)
    assert all(not v for v in r.values()), r
    print("  ✓ re-run is a no-op (fills linked)")

    # 3. clean close → exit fields + TOTAL P/L + fees accumulate
    fill(conn, 3, 200, "XYZ", "PUT", 95.0, 2, 0.40, "CLOSING")
    fill(conn, 4, 200, "XYZ", "PUT", 90.0, -2, 0.15, "CLOSING")
    r = match_fills_to_ledger(conn)
    assert len(r["closed"]) == 1, r
    st, xp, pnl, fees, coid = conn.execute(
        "SELECT status, exit_price, final_pnl, fees_total, close_order_id "
        "FROM spread_score_trades WHERE id=?", (tid,)).fetchone()
    assert st == "closed" and abs(xp - 0.25) < 1e-9 and abs(pnl - 120.0) < 1e-9
    assert int(coid) == 200 and abs(fees - 2.64) < 1e-9
    print("  ✓ clean close → exit 0.25, P/L +$120 TOTAL, fees 2.64")

    # 4. pre-recorded trade (HCA pattern) → linked, no duplicate
    conn.execute(
        "INSERT INTO spread_score_trades (symbol, opex_date, spread_type, "
        "short_strike, long_strike, width, entry_credit, entry_date, "
        "entry_price, status, placed, shares, open_order_id, account) "
        "VALUES ('AAA','2026-08-21','bull_put',50,45,5.0,1.0,'2026-06-15',"
        "1.0,'open',1,1,300,'live')")
    fill(conn, 5, 300, "AAA", "PUT", 50.0, -1, 1.5, "OPENING")
    fill(conn, 6, 300, "AAA", "PUT", 45.0, 1, 0.5, "OPENING")
    r = match_fills_to_ledger(conn)
    assert len(r["linked"]) == 1
    assert conn.execute("SELECT COUNT(*) FROM spread_score_trades WHERE symbol='AAA'").fetchone()[0] == 1
    print("  ✓ pre-recorded row → linked, no duplicate")

    # 5. partial close → flagged, stays open
    fill(conn, 7, 400, "AAA", "PUT", 50.0, 0.5, 0.7, "CLOSING")
    fill(conn, 8, 400, "AAA", "PUT", 45.0, -0.5, 0.2, "CLOSING")
    r = match_fills_to_ledger(conn)
    assert len(r["flagged"]) == 1 and "PARTIAL" in r["flagged"][0]
    assert conn.execute("SELECT status FROM spread_score_trades WHERE symbol='AAA'").fetchone()[0] == "open"
    print("  ✓ partial close → flagged, not auto-closed")

    # 6. mixed open/close order (roll) → flagged
    fill(conn, 9, 500, "BBB", "PUT", 30.0, 1, 0.5, "CLOSING")
    fill(conn, 10, 500, "BBB", "PUT", 28.0, -1, 0.4, "OPENING")
    r = match_fills_to_ledger(conn)
    assert any("roll" in f for f in r["flagged"]), r
    print("  ✓ roll order → flagged")

    # 7. single-leg long put → long_put row, debit stored negative
    fill(conn, 11, 600, "CCC", "PUT", 600.0, 1, 12.5, "OPENING")
    r = match_fills_to_ledger(conn)
    assert len(r["opened"]) == 1 and "long_put" in r["opened"][0]
    lp = tuple(conn.execute(
        "SELECT spread_type, short_strike, long_strike, entry_credit "
        "FROM spread_score_trades WHERE symbol='CCC'").fetchone())
    assert lp == ("long_put", 0.0, 600.0, -12.5), lp
    print("  ✓ single long put → long_put row, debit negative")

    # 8. 4-leg structure → flagged, no auto-row
    for i, (k, q, px) in enumerate([(80, -1, 2.0), (75, 1, 1.0), (85, -1, 1.8), (90, 1, 0.9)]):
        fill(conn, 20 + i, 700, "DDD", "PUT", float(k), q, px, "OPENING")
    r = match_fills_to_ledger(conn)
    assert any("not auto-recognized" in f for f in r["flagged"])
    assert conn.execute("SELECT COUNT(*) FROM spread_score_trades WHERE symbol='DDD'").fetchone()[0] == 0
    print("  ✓ 4-leg structure → flagged, no auto-row")

    print("ALL PASSED")


if __name__ == "__main__":
    main()
