"""
MaxPain — Schwab quotes endpoint wrapper
~/MaxPain_Project/lib/schwab_quotes.py

Lightweight batched-quote fetcher. Used by update_close_prices.py at
4:16 PM ET to refresh current_price in today's live_snapshots rows
with the actual closing trade.

Schwab /marketdata/v1/quotes returns one call for many symbols. After
4:00 PM ET, lastPrice = today's closing trade. closePrice is the prior
session — don't use it for "today's close."

Auth import still routes through Metal_Project (deferred per Tranche 4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path.home() / "Metal_Project"))
from Schwab.auth import get_valid_token  # noqa: E402

QUOTES_URL = "https://api.schwabapi.com/marketdata/v1/quotes"


def fetch_quotes(symbols: list[str]) -> dict[str, float]:
    """Fetch closing/last prices for a batch of symbols.

    Returns {symbol: price} for symbols Schwab returned a quote for.
    Symbols that Schwab can't price (delisted, index without quote, etc.)
    are silently omitted — caller falls back to yfinance.
    """
    if not symbols:
        return {}
    try:
        token = get_valid_token()
    except Exception as e:
        print(f"  Schwab auth failed: {e}")
        return {}

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    params = {
        "symbols": ",".join(symbols),
        "fields": "quote",
    }
    try:
        resp = requests.get(QUOTES_URL, headers=headers, params=params, timeout=15)
        if resp.status_code == 401:
            print("  Schwab token expired (401)")
            return {}
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Schwab quotes request failed: {e}")
        return {}

    prices: dict[str, float] = {}
    for sym, info in data.items():
        quote = info.get("quote", {}) if isinstance(info, dict) else {}
        # After 4:00 PM ET, lastPrice is today's closing trade.
        # regularMarketLastPrice fallback covers some equity edge cases.
        last = quote.get("lastPrice") or quote.get("regularMarketLastPrice")
        if last:
            prices[sym.upper()] = round(float(last), 4)
    return prices
