"""
MaxPain — Schwab Options Chain Fetcher
~/MaxPain_Project/lib/schwab_options.py

Stateless function — fetches live option chains from Schwab's
/marketdata/v1/chains endpoint and returns calls/puts as pandas DataFrames
matching the yfinance column convention.
"""

import sys
from pathlib import Path

import requests

# Auth lives in this project. PROJECT_ROOT = ~/MaxPain_Project.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


def fetch_chain_with_greeks(symbol: str, expiry: str, dte_tolerance_days: int = 3):
    """Fetch Schwab chain in the COLUMN FORMAT expected by structures.py
    (open_zebra / open_inverted_fly / open_bull_put / open_bear_call etc).

    Returns a single DataFrame with columns:
      strike, delta, cMidIv, pMidIv, cBidPx, cAskPx, pBidPx, pAskPx, stkPx

    Schwab's expiration convention varies — some products list the Thursday
    before OpEx (last-trading-day), others the Friday itself. The tolerance
    window snaps to the listed expiration closest to the requested date.

    Returns (df, spot) or (None, None) on failure.
    """
    import pandas as pd
    from datetime import date as _date, datetime, timedelta

    target = datetime.strptime(expiry, "%Y-%m-%d").date()
    range_lo = (target - timedelta(days=dte_tolerance_days)).isoformat()
    range_hi = (target + timedelta(days=dte_tolerance_days)).isoformat()

    # Single API call across the tolerance window; pick the closest listed expiration
    try:
        token = get_valid_token()
    except Exception as e:
        print(f"    Schwab auth failed: {e}")
        return None, None
    try:
        resp = requests.get(
            CHAINS_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"symbol": symbol, "contractType": "ALL",
                    "fromDate": range_lo, "toDate": range_hi, "strikeCount": 500},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"    Schwab chain request failed for {symbol}: {e}")
        return None, None

    spot_raw = data.get("underlyingPrice")
    if not spot_raw or float(spot_raw) <= 0:
        u = data.get("underlying") or {}
        spot_raw = u.get("last") if isinstance(u, dict) else None
    if not spot_raw or float(spot_raw) <= 0:
        return None, None
    spot = float(spot_raw)

    # Pick the expiration key (format "YYYY-MM-DD:DTE") closest to target
    cmap = data.get("callExpDateMap", {}) or {}
    pmap = data.get("putExpDateMap", {}) or {}
    all_keys = set(cmap.keys()) | set(pmap.keys())
    if not all_keys:
        return None, None

    def _key_to_date(k: str) -> _date:
        return datetime.strptime(k.split(":")[0], "%Y-%m-%d").date()

    chosen_key = min(all_keys, key=lambda k: abs((_key_to_date(k) - target).days))
    chosen_cmap = {chosen_key: cmap[chosen_key]} if chosen_key in cmap else {}
    chosen_pmap = {chosen_key: pmap[chosen_key]} if chosen_key in pmap else {}

    def _rows(exp_map: dict) -> list[dict]:
        out = []
        for _exp_key, strikes in exp_map.items():
            for _strike_str, contracts in strikes.items():
                for c in contracts:
                    out.append({
                        "strike": float(c.get("strikePrice", 0)),
                        "bid": float(c.get("bid", 0)),
                        "ask": float(c.get("ask", 0)),
                        "iv": float(c.get("volatility", 0)) / 100.0,
                        "delta": float(c.get("delta", 0)),
                    })
        return out

    calls = pd.DataFrame(_rows(chosen_cmap))
    puts = pd.DataFrame(_rows(chosen_pmap))
    if calls.empty or puts.empty:
        return None, None

    # Rename for joined-chain naming
    calls = calls.rename(columns={"bid": "cBidPx", "ask": "cAskPx", "iv": "cMidIv"})
    puts = puts.rename(columns={"bid": "pBidPx", "ask": "pAskPx", "iv": "pMidIv",
                                "delta": "pDelta"})

    # Outer join on strike so all strikes appear; structures.py uses call delta primarily
    merged = calls.merge(puts, on="strike", how="outer").sort_values("strike").reset_index(drop=True)
    merged["stkPx"] = spot
    # structures.py select_by_delta filters on "delta" (call delta convention).
    # Keep "delta" as call delta; pDelta carried separately for put-side selection if needed.
    if "delta" not in merged.columns:
        merged["delta"] = float("nan")
    return merged, spot


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
