"""
MaxPain — Schwab Options Chain Fetcher
~/MaxPain_Project/lib/schwab_options.py

Lifted from ~/Metal_Project/scripts/pipeline/schwab_options.py as part of
Tranche 1 (Metal → MaxPain migration). Stateless function — fetches live
option chains from Schwab's /marketdata/v1/chains endpoint and returns
calls/puts as pandas DataFrames matching the yfinance column convention.

Auth dependency: imports Schwab.auth.get_valid_token from Metal_Project.
That import will be lifted in a later tranche when the token store moves.
For now, the cross-project sys.path import is the one remaining
Metal_Project dependency for stateless option-chain math.
"""

import sys
from pathlib import Path

import requests

# Auth still lives in Metal_Project for now (deferred to a later tranche).
sys.path.insert(0, str(Path.home() / "Metal_Project"))
from Schwab.auth import get_valid_token  # noqa: E402

CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"


def fetch_chain(symbol: str, expiry: str, contract_type: str = "ALL") -> dict | None:
    """
    Fetch raw option chain from Schwab API.

    Args:
        symbol:        Underlying ticker (e.g. "GLD")
        expiry:        Target expiration date "YYYY-MM-DD"
        contract_type: "ALL", "CALL", or "PUT"

    Returns:
        Raw JSON response dict, or None on auth/network failure.
    """
    try:
        token = get_valid_token()
    except Exception as e:
        print(f"    Schwab auth failed: {e}")
        return None

    params = {
        "symbol":       symbol,
        "contractType": contract_type,
        "fromDate":     expiry,
        "toDate":       expiry,
        "strikeCount":  500,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
    }

    try:
        resp = requests.get(CHAINS_URL, headers=headers, params=params, timeout=15)
        if resp.status_code == 401:
            print(f"    Schwab token expired (401) for {symbol}")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"    Schwab chain request failed for {symbol}: {e}")
        return None


def _parse_exp_date_map(exp_date_map: dict) -> list[dict]:
    """Parse Schwab's call/putExpDateMap into a flat list of contract rows."""
    rows = []
    for _exp_key, strikes in exp_date_map.items():
        for _strike_str, contracts in strikes.items():
            for c in contracts:
                rows.append({
                    "strike":            float(c.get("strikePrice", 0)),
                    "openInterest":      int(c.get("openInterest", 0)),
                    "bid":               float(c.get("bid", 0)),
                    "ask":               float(c.get("ask", 0)),
                    "impliedVolatility": float(c.get("volatility", 0)) / 100.0,
                    # Schwab returns IV as percentage (e.g. 25.0 = 25%)
                    # yfinance returns as decimal (0.25) — normalize here
                })
    return rows


def fetch_option_chain(symbol: str, expiry: str):
    """
    Fetch option chain from Schwab and return (calls_df, puts_df, price)
    matching the DataFrame format used throughout the project.

    Args:
        symbol: Underlying ticker
        expiry: Expiration date "YYYY-MM-DD"

    Returns:
        (calls_df, puts_df, underlying_price) or (None, None, None) on failure.
    """
    import pandas as pd

    data = fetch_chain(symbol, expiry)
    if data is None:
        return None, None, None

    call_map = data.get("callExpDateMap", {})
    put_map  = data.get("putExpDateMap", {})

    if not call_map and not put_map:
        print(f"    Schwab returned empty chain for {symbol} @ {expiry}")
        return None, None, None

    call_rows = _parse_exp_date_map(call_map)
    put_rows  = _parse_exp_date_map(put_map)

    cols = ["strike", "openInterest", "bid", "ask", "impliedVolatility"]
    calls_df = pd.DataFrame(call_rows, columns=cols) if call_rows else pd.DataFrame(columns=cols)
    puts_df  = pd.DataFrame(put_rows, columns=cols)  if put_rows  else pd.DataFrame(columns=cols)

    price = data.get("underlyingPrice") or data.get("underlying", {}).get("last")
    if price is not None:
        price = float(price)

    return calls_df, puts_df, price
