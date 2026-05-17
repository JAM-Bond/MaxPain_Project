"""
ZEBRA construction preview — pulls live Schwab chain + applies open_zebra logic
to generate an alert-ready trade construction block.

Used to prototype the format for the daily alert's enrichment section.
Run standalone: python3.11 scripts/preview/zebra_construction_preview.py KRE 2026-07-17
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path.home() / "MaxPain_Project"
BACKTEST_DIR = ROOT / "scripts/backtest"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKTEST_DIR))  # structures.py uses bare "from legs import ..." style

from structures import open_zebra  # noqa: E402

CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"


def fetch_schwab_chain_with_greeks(symbol: str, expiry: str) -> tuple[pd.DataFrame, float]:
    """Fetch Schwab chain INCLUDING delta + IV at strike level.

    schwab_options.py drops delta from its parser — we need it for ZEBRA leg
    selection. Builds a DataFrame with columns matching open_zebra's
    expectation: strike, delta, cMidIv, cBidPx, cAskPx, stkPx.
    """
    from Schwab.auth import get_valid_token
    token = get_valid_token()
    params = {
        "symbol": symbol,
        "contractType": "CALL",
        "fromDate": expiry,
        "toDate": expiry,
        "strikeCount": 500,
    }
    resp = requests.get(
        CHAINS_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params, timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    spot = float(data.get("underlyingPrice") or data.get("underlying", {}).get("last") or 0)
    rows = []
    call_map = data.get("callExpDateMap", {})
    for _exp_key, strikes in call_map.items():
        for _strike_str, contracts in strikes.items():
            for c in contracts:
                iv = float(c.get("volatility", 0)) / 100.0
                rows.append({
                    "strike": float(c.get("strikePrice", 0)),
                    "cBidPx": float(c.get("bid", 0)),
                    "cAskPx": float(c.get("ask", 0)),
                    "cMidIv": iv,
                    "pMidIv": iv,  # stubbed; ZEBRA uses calls only, but select_by_delta dropna includes pMidIv
                    "delta": float(c.get("delta", 0)),
                    "stkPx": spot,
                })
    df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
    return df, spot


def format_construction_block(symbol: str, expiry: str, position) -> str:
    """Format the open_zebra Position into an alert-ready construction block."""
    if position is None:
        return f"  ✗ Could not construct ZEBRA for {symbol} @ {expiry} (no extrinsic-balanced strikes found)"

    n = position.notes
    long_leg = position.legs[0]   # both longs share the same strike/delta
    short_leg = position.legs[2]
    spot = position.underlying_entry
    debit = n["debit"]

    lines = [
        f"  ZEBRA — {symbol} (spot ${spot:.2f}, expiration {expiry})",
        f"",
        f"    LEG               QTY  STRIKE  DELTA   PRICE",
        f"    Long  call  ITM    +2  ${long_leg.strike:>6.2f}  {long_leg.delta:+.2f}  ${long_leg.price:>5.2f}",
        f"    Short call  ATM    -1  ${short_leg.strike:>6.2f}  {short_leg.delta:+.2f}  ${short_leg.price:>5.2f}",
        f"",
        f"    Net debit (per ZEBRA):    ${debit:.2f}",
        f"    Capital outlay / contract: ${debit*100:.0f}",
        f"    Capital efficiency:        {n['capital_efficiency']*100:.1f}% of stock cost",
        f"    Max loss (defined risk):   ${debit*100:.0f}  (= debit; realized only if spot < ${long_leg.strike:.2f} at expiry)",
        f"    Net entry delta:           {n['entry_delta']:+.2f}  (≈ stock-equivalent + slight gamma kicker)",
        f"",
        f"    Extrinsic check (must be theta-neutral):",
        f"      Short extrinsic:    ${n['short_extrinsic']:.2f}",
        f"      Long extrinsic ×2:  ${n['long_extrinsic_total']:.2f}",
        f"      Cushion:            ${n['extrinsic_cushion']:+.2f}  ({'PASS' if n['extrinsic_cushion'] >= 0 else 'FAIL'})",
        f"",
        f"    Sizing rule:  capital outlay = 5–10% of book equity per ZEBRA position",
        f"                  e.g. $50K book → 1 contract ($5K is ~10% of book)",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol", help="e.g. KRE")
    parser.add_argument("expiry", help="YYYY-MM-DD")
    args = parser.parse_args()

    print(f"Fetching Schwab chain: {args.symbol} {args.expiry}...")
    chain, spot = fetch_schwab_chain_with_greeks(args.symbol, args.expiry)
    if chain.empty:
        print(f"  empty chain")
        return

    print(f"  {len(chain)} strikes, spot ${spot:.2f}")
    print()

    pos = open_zebra(chain, pd.Timestamp.today(), pd.Timestamp(args.expiry))

    print("=" * 72)
    print(f"DAILY ALERT — ZEBRA CONSTRUCTION PREVIEW ({args.symbol})")
    print("=" * 72)
    print(format_construction_block(args.symbol, args.expiry, pos))
    print("=" * 72)


if __name__ == "__main__":
    main()
