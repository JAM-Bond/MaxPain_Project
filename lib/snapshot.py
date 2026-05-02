"""
MaxPain — Per-symbol snapshot orchestration
~/MaxPain_Project/lib/snapshot.py

Takes a symbol + OpEx + today and returns a dict of all summary metrics
(price, max pain, distance, PCR, gamma profile, ATM IV, dividend flag, ...).
Schwab is primary; yfinance is fallback.

Lifted from ~/Metal_Project/scripts/pipeline/yfinance_daily_snapshot.py.
The Metal_Project ETF_META dependency is removed in favor of always checking
dividends via yfinance (the original "default to True for stocks" path covers
this). Trading-days-to-OpEx helper is included locally to avoid a config
dependency.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

from lib.schwab_options import fetch_option_chain as _schwab_fetch
from lib.max_pain import (
    calculate_max_pain,
    calculate_expected_move,
    calculate_gamma_profile,
    check_dividend_flag,
)


# ─── Trading-days helper (local copy; no config dependency) ───
def trading_days_to(target: date, today: date | None = None) -> int:
    if today is None:
        today = date.today()
    if today >= target:
        return 0
    count = 0
    d = today
    while d < target:
        d += timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


# ─── Chain fetch with yfinance fallback ───
def fetch_chain_data(symbol: str, opex: date):
    """Try Schwab first, fall back to yfinance.

    Returns (calls_df, puts_df, price, source) or (None, None, None, None).
    """
    opex_str = str(opex)

    calls, puts, price = _schwab_fetch(symbol, opex_str)
    if calls is not None and puts is not None and not calls.empty:
        print("[schwab] ", end="")
        return calls, puts, price, "schwab"

    if yf is None:
        print("SKIP (Schwab failed, yfinance not installed)")
        return None, None, None, None

    print("[yf fallback] ", end="")
    try:
        ticker = yf.Ticker(symbol)
        if price is None:
            price = ticker.fast_info.get("last_price")
            if not price:
                info = ticker.info
                price = info.get("regularMarketPrice") or info.get("previousClose")

        expirations = ticker.options
        if not expirations:
            return None, None, None, None

        target = opex_str
        if target not in expirations:
            target = expirations[0]
            print(f"(using {target} as proxy) ", end="")

        chain = ticker.option_chain(target)
        calls = chain.calls[["strike", "openInterest", "bid", "ask",
                              "impliedVolatility"]].copy()
        puts = chain.puts[["strike", "openInterest", "bid", "ask",
                            "impliedVolatility"]].copy()
        calls["openInterest"] = calls["openInterest"].fillna(0).astype(int)
        puts["openInterest"] = puts["openInterest"].fillna(0).astype(int)
        if price:
            price = float(price)
        return calls, puts, price, "yfinance"
    except Exception as e:
        print(f"yfinance also failed: {e}")
        return None, None, None, None


# ─── Take per-symbol snapshot ───
def take_snapshot(symbol: str, opex: date, today: date) -> dict | None:
    """Pull live options data for symbol and compute all summary metrics.

    Returns snapshot dict (same schema as the live_snapshots table)
    or None on failure. Prints a one-line per-symbol summary to stdout.
    """
    print(f"  {symbol}...", end=" ", flush=True)
    try:
        calls, puts, price, source = fetch_chain_data(symbol, opex)
        if calls is None or puts is None or price is None or price <= 0:
            print("SKIP (no chain data)")
            return None

        pain_metrics = calculate_max_pain(calls, puts)
        exp_move = calculate_expected_move(calls, puts, price)

        max_pain = pain_metrics.get("max_pain")
        distance_pct = round(
            (max_pain - price) / price * 100, 3
        ) if max_pain else None

        dte = trading_days_to(opex, today)

        # Dividend flag: always probe via yfinance (Metal_Project's ETF_META
        # was a per-ETF override; the default behavior was to check anyway).
        if yf is not None:
            try:
                div_info = check_dividend_flag(yf.Ticker(symbol), opex)
            except Exception:
                div_info = {"dividend_flag": False, "ex_div_date": None}
        else:
            div_info = {"dividend_flag": False, "ex_div_date": None}

        # ATM IV (call side)
        all_strikes = sorted(set(calls["strike"]))
        atm_strike = min(all_strikes, key=lambda s: abs(s - price)) if all_strikes else None
        atm_iv = None
        if atm_strike is not None:
            atm_row = calls[calls["strike"] == atm_strike]
            if not atm_row.empty:
                atm_iv = round(float(atm_row.iloc[0]["impliedVolatility"]) * 100, 2)

        gamma_metrics = calculate_gamma_profile(calls, puts, price, dte, max_pain)

        snap = {
            "symbol":                  symbol,
            "snapshot_date":           str(today),
            "opex_date":               str(opex),
            "dte":                     dte,
            "current_price":           round(price, 4),
            "max_pain":                max_pain,
            "distance_pct":            distance_pct,
            "pin_zone_low":            pain_metrics.get("pin_zone_low"),
            "pin_zone_high":           pain_metrics.get("pin_zone_high"),
            "pin_zone_width":          pain_metrics.get("pin_zone_width"),
            "pcr":                     pain_metrics.get("pcr"),
            "total_call_oi":           pain_metrics.get("total_call_oi"),
            "total_put_oi":            pain_metrics.get("total_put_oi"),
            "expected_move":           exp_move,
            "atm_iv_pct":              atm_iv,
            "net_gamma":               gamma_metrics.get("net_gamma"),
            "net_gamma_sign":          gamma_metrics.get("net_gamma_sign"),
            "gamma_flip_strike":       gamma_metrics.get("gamma_flip_strike"),
            "oi_concentration_at_mp":  gamma_metrics.get("oi_concentration_at_mp"),
            "data_source":             source,
            **div_info,
        }

        gamma_sign = gamma_metrics.get("net_gamma_sign", "?")
        flip = gamma_metrics.get("gamma_flip_strike", "?")
        conc = gamma_metrics.get("oi_concentration_at_mp")
        conc_str = f"{conc:.1%}" if conc is not None else "?"
        direction = "↑" if distance_pct and distance_pct > 0 else "↓"
        print(f"Price={price:.2f}  MaxPain={max_pain}  "
              f"Dist={distance_pct:+.2f}%{direction}  "
              f"PCR={pain_metrics.get('pcr')}  "
              f"Gamma={gamma_sign}  Flip={flip}  OI@MP={conc_str}  "
              f"DivFlag={'Y' if div_info['dividend_flag'] else 'N'}")
        return snap

    except Exception as e:
        print(f"ERROR: {e}")
        return None


# ─── OpEx-calendar helper (matches Metal_Project's current_opex) ───
def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def current_opex() -> date:
    """Return the current month's OpEx date, or next month's if past."""
    today = date.today()
    y, m = today.year, today.month
    opex = third_friday(y, m)
    if today > opex:
        m += 1
        if m > 12:
            m = 1
            y += 1
        opex = third_friday(y, m)
    return opex
