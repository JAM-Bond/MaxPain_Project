"""Unit tests for the order reconciler's pure logic — leg classification, the
order_legs mirror fields, and net P/L. No network; synthetic Schwab order dicts
+ an in-memory DB. Run:  python3.11 tests/test_order_reconciler.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.order_reconciler import summarize_order, net_pnl  # noqa: E402


def _mem():
    return sqlite3.connect(":memory:")  # _leg_fees fails -> 0.0 fees, fine for unit tests


def _leg(symbol, instruction, effect, leg_id, qty=1):
    return {"orderLegType": "OPTION", "legId": leg_id, "instruction": instruction,
            "positionEffect": effect, "quantity": qty,
            "instrument": {"assetType": "OPTION", "symbol": symbol}}


def _exec(leg_id, price, qty=1):
    return {"legId": leg_id, "price": price, "quantity": qty}


def _order(otype, qty, legs, execs, oid=1):
    return {"orderId": oid, "orderType": otype, "quantity": qty, "status": "FILLED",
            "closeTime": "2026-06-10T20:00:00+0000", "orderLegCollection": legs,
            "orderActivityCollection": [{"executionLegs": execs}]}


def test_bull_put_open_legs_and_credit():
    o = _order("NET_CREDIT", 5,
               [_leg("HCA   260821P00370000", "SELL_TO_OPEN", "OPENING", 1, 5),
                _leg("HCA   260821P00365000", "BUY_TO_OPEN", "OPENING", 2, 5)],
               [_exec(1, 25.50, 5), _exec(2, 23.13, 5)])
    legs, s = summarize_order(o, _mem())
    assert len(legs) == 2 and {l["leg_id"] for l in legs} == {1, 2}
    assert s["side"] == "open" and s["spread_type"] == "bull_put"
    assert s["short_strike"] == 370 and s["long_strike"] == 365
    assert s["net_per_share"] == 2.37        # +25.50 (SELL) − 23.13 (BUY)
    assert legs[0]["fill_price"] == 25.5 and legs[1]["fill_price"] == 23.13
    assert s["recordable"]


def test_weighted_avg_partial_fills():
    o = _order("NET_CREDIT", 2,
               [_leg("X     260717P00100000", "SELL_TO_OPEN", "OPENING", 1, 2)],
               [_exec(1, 1.00, 1), _exec(1, 1.40, 1)])  # two partials -> avg 1.20
    legs, s = summarize_order(o, _mem())
    assert legs[0]["fill_price"] == 1.2


def test_bear_call_strikes():
    o = _order("NET_CREDIT", 3,
               [_leg("STZ   260717C00145000", "SELL_TO_OPEN", "OPENING", 1, 3),
                _leg("STZ   260717C00150000", "BUY_TO_OPEN", "OPENING", 2, 3)],
               [_exec(1, 3.00, 3), _exec(2, 1.00, 3)])
    _, s = summarize_order(o, _mem())
    assert s["spread_type"] == "bear_call" and s["short_strike"] == 145 and s["long_strike"] == 150


def test_close_side_and_exit_sign():
    o = _order("NET_DEBIT", 5,
               [_leg("COF   260717P00185000", "BUY_TO_CLOSE", "CLOSING", 1, 5),
                _leg("COF   260717P00180000", "SELL_TO_CLOSE", "CLOSING", 2, 5)],
               [_exec(1, 2.75, 5), _exec(2, 0.20, 5)])
    _, s = summarize_order(o, _mem())
    assert s["side"] == "close" and s["spread_type"] == "bull_put"
    # net close per share = −2.75 (BUY) + 0.20 (SELL) = −2.55 ; exit_credit = +2.55
    assert s["net_per_share"] == -2.55


def test_three_leg_zebra_is_recordable_now():
    o = _order("NET_DEBIT", 1,
               [_leg("KRE   260717C00065000", "BUY_TO_OPEN", "OPENING", 1, 2),
                _leg("KRE   260717C00065000", "BUY_TO_OPEN", "OPENING", 2, 2),
                _leg("KRE   260717C00070000", "SELL_TO_OPEN", "OPENING", 3, 1)],
               [_exec(1, 9.0, 2), _exec(2, 9.0, 2), _exec(3, 8.84, 1)])
    legs, s = summarize_order(o, _mem())
    assert len(legs) == 3 and s["spread_type"] == "zebra" and s["recordable"]
    assert s["short_strike"] == 70 and s["long_strike"] == 65  # short=sold call, long=bought


def test_bond_order_ignored():
    o = {"orderId": 9, "orderType": "LIMIT", "quantity": 100, "orderLegCollection":
         [{"legId": 1, "instruction": "BUY", "positionEffect": "OPENING", "quantity": 100,
           "instrument": {"assetType": "COLLECTIVE_INVESTMENT", "symbol": "64034KEE7"}}]}
    assert summarize_order(o, _mem()) is None


def test_roll_mixed_flagged():
    o = _order("NET_DEBIT", 1,
               [_leg("X     260717P00100000", "BUY_TO_CLOSE", "CLOSING", 1),
                _leg("X     260815P00100000", "SELL_TO_OPEN", "OPENING", 2)],
               [_exec(1, 1.0), _exec(2, 1.2)])
    _, s = summarize_order(o, _mem())
    assert s["side"] == "mixed" and not s["recordable"]


def test_net_pnl():
    assert net_pnl(2.30, 2.55, 5, 13.0) == -138.0        # credit-spread loss
    assert net_pnl(2.50, 1.00, 5, 6.5) == 743.5          # credit-spread win
    assert net_pnl(-2.56, -3.00, 1, 1.3) == 42.7         # debit: entry −2.56, exit −3.00


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn(); print("  PASS", fn.__name__); passed += 1
        except AssertionError as e:
            print("  FAIL", fn.__name__, e)
        except Exception as e:
            print("  ERROR", fn.__name__, repr(e))
    print(f"{passed}/{len(fns)} passed")
    raise SystemExit(0 if passed == len(fns) else 1)
