"""Unit tests for the order reconciler's pure logic — classification + net P/L.
No network: we feed synthetic Schwab order dicts. Run:
  python3.11 -m pytest tests/test_order_reconciler.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.order_reconciler import classify_order, _net_pnl  # noqa: E402


def _leg(symbol, instruction, effect, qty=1):
    return {"instruction": instruction, "positionEffect": effect, "quantity": qty,
            "instrument": {"assetType": "OPTION", "symbol": symbol}}


def _order(otype, price, qty, legs, oid=1):
    return {"orderId": oid, "orderType": otype, "price": price, "quantity": qty,
            "closeTime": "2026-06-10T20:00:00+0000", "orderLegCollection": legs}


def test_classify_bull_put_open():
    o = _order("NET_CREDIT", 2.30, 5, [
        _leg("COF   260717P00185000", "SELL_TO_OPEN", "OPENING", 5),
        _leg("COF   260717P00180000", "BUY_TO_OPEN", "OPENING", 5)])
    c = classify_order(o)
    assert c["side"] == "open" and c["spread_type"] == "bull_put"
    assert c["short_strike"] == 185 and c["long_strike"] == 180  # short = higher put
    assert c["signed_price"] == 2.30 and c["qty"] == 5 and c["recordable"]


def test_classify_bear_call_open():
    o = _order("NET_CREDIT", 2.10, 3, [
        _leg("STZ   260717C00145000", "SELL_TO_OPEN", "OPENING", 3),
        _leg("STZ   260717C00150000", "BUY_TO_OPEN", "OPENING", 3)])
    c = classify_order(o)
    assert c["spread_type"] == "bear_call"
    assert c["short_strike"] == 145 and c["long_strike"] == 150  # short = lower call


def test_classify_close_is_net_debit():
    o = _order("NET_DEBIT", 2.55, 5, [
        _leg("COF   260717P00185000", "BUY_TO_CLOSE", "CLOSING", 5),
        _leg("COF   260717P00180000", "SELL_TO_CLOSE", "CLOSING", 5)])
    c = classify_order(o)
    assert c["side"] == "close" and c["spread_type"] == "bull_put"
    assert c["signed_price"] == -2.55  # paid to close


def test_long_put_debit_open():
    o = _order("LIMIT", 2.56, 1, [_leg("KRE   260717P00068000", "BUY_TO_OPEN", "OPENING", 1)])
    c = classify_order(o)
    assert c["spread_type"] == "long_put" and c["signed_price"] == -2.56  # debit paid


def test_zebra_is_flagged_not_auto():
    o = _order("NET_DEBIT", 9.16, 1, [
        _leg("KRE   260717C00065000", "BUY_TO_OPEN", "OPENING", 2),
        _leg("KRE   260717C00065000", "BUY_TO_OPEN", "OPENING", 2),
        _leg("KRE   260717C00070000", "SELL_TO_OPEN", "OPENING", 1)])
    c = classify_order(o)
    assert c["spread_type"] == "zebra" and not c["recordable"]  # manual confirm


def test_bond_order_ignored():
    o = {"orderId": 9, "orderType": "LIMIT", "price": 100, "quantity": 100,
         "orderLegCollection": [{"instruction": "BUY", "positionEffect": "OPENING",
                                 "quantity": 100,
                                 "instrument": {"assetType": "COLLECTIVE_INVESTMENT",
                                                "symbol": "64034KEE7"}}]}
    assert classify_order(o) is None


def test_net_pnl_credit_spread_loss():
    # bull_put: took in 2.30, paid 2.55 to close, 5 lots, $13 fees -> (2.30-2.55)*500-13
    assert _net_pnl(2.30, -2.55, 5, 13.0) == round((2.30 - 2.55) * 100 * 5 - 13.0, 2)
    assert _net_pnl(2.30, -2.55, 5, 13.0) == -138.0


def test_net_pnl_credit_spread_win():
    assert _net_pnl(2.50, -1.00, 5, 6.5) == 743.5  # (2.50-1.00)*500 - 6.5


def test_net_pnl_debit_structure():
    # long_put: paid 2.56 (entry_credit -2.56), sold 3.00 to close, 1 lot, $1.3 fees
    assert _net_pnl(-2.56, 3.00, 1, 1.3) == round((3.00 - 2.56) * 100 - 1.3, 2)
    assert _net_pnl(-2.56, 3.00, 1, 1.3) == 42.7
