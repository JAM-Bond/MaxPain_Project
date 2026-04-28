"""
MaxPain — Pure-math option-chain analytics
~/MaxPain_Project/lib/max_pain.py

Lifted from ~/Metal_Project/scripts/pipeline/yfinance_daily_snapshot.py.
All functions here are stateless — they take pandas DataFrames and return
dicts of metrics. No I/O, no DB, no auth. Used by snapshot.py and any
analysis script that wants to compute pin / gamma / expected-move metrics
from a chain.
"""
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import norm


def calculate_max_pain(calls: pd.DataFrame, puts: pd.DataFrame) -> dict:
    """Calculate Max Pain, pin zone, and PCR from options chain.

    Returns dict with: max_pain, pin_zone_low, pin_zone_high, pin_zone_width,
    total_call_oi, total_put_oi, pcr.
    """
    strikes = sorted(set(calls["strike"]) | set(puts["strike"]))
    if not strikes:
        return {}

    payout_data = []
    for s in strikes:
        itm_calls = calls[calls["strike"] < s]
        call_pain = ((s - itm_calls["strike"]) * itm_calls["openInterest"]).sum()
        itm_puts = puts[puts["strike"] > s]
        put_pain = ((itm_puts["strike"] - s) * itm_puts["openInterest"]).sum()
        payout_data.append({
            "strike": s,
            "call_pain": call_pain,
            "put_pain": put_pain,
            "total_pain": call_pain + put_pain,
        })

    df = pd.DataFrame(payout_data)
    min_pain = df["total_pain"].min()
    max_pain_row = df.loc[df["total_pain"].idxmin()]
    max_pain = float(max_pain_row["strike"])

    pin_threshold = min_pain * 1.10
    pin_zone_df = df[df["total_pain"] <= pin_threshold]
    pin_zone_low = float(pin_zone_df["strike"].min())
    pin_zone_high = float(pin_zone_df["strike"].max())

    total_call_oi = int(calls["openInterest"].sum())
    total_put_oi = int(puts["openInterest"].sum())
    pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else None

    return {
        "max_pain": max_pain,
        "pin_zone_low": pin_zone_low,
        "pin_zone_high": pin_zone_high,
        "pin_zone_width": round(pin_zone_high - pin_zone_low, 2),
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "pcr": pcr,
    }


def calculate_expected_move(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    current_price: float,
) -> float | None:
    """Approximate expected move from ATM straddle mid price."""
    all_strikes = sorted(set(calls["strike"]) | set(puts["strike"]))
    if not all_strikes:
        return None

    atm = min(all_strikes, key=lambda s: abs(s - current_price))
    c_rows = calls[calls["strike"] == atm]
    p_rows = puts[puts["strike"] == atm]
    if c_rows.empty or p_rows.empty:
        return None

    c_mid = (c_rows.iloc[0]["bid"] + c_rows.iloc[0]["ask"]) / 2
    p_mid = (p_rows.iloc[0]["bid"] + p_rows.iloc[0]["ask"]) / 2
    return round(c_mid + p_mid, 2)


def bs_gamma(S: float, K: float, T: float, iv: float, r: float = 0.05) -> float:
    """Black-Scholes gamma. Same for calls and puts. Returns 0.0 on bad input."""
    try:
        if T <= 0 or iv <= 0 or S <= 0 or K <= 0:
            return 0.0
        d1 = (np.log(S / K) + (r + 0.5 * iv ** 2) * T) / (iv * np.sqrt(T))
        return norm.pdf(d1) / (S * iv * np.sqrt(T))
    except Exception:
        return 0.0


def calculate_gamma_profile(
    calls: pd.DataFrame,
    puts: pd.DataFrame,
    current_price: float,
    dte: int,
    max_pain: float | None,
) -> dict:
    """Compute net gamma exposure, gamma flip strike, and OI concentration at MP.

    Returns dict with: net_gamma, net_gamma_sign ('positive'|'negative'|'neutral'),
    gamma_flip_strike, oi_concentration_at_mp.
    """
    T = max(dte, 1) / 252.0

    all_strikes = sorted(set(calls["strike"]) | set(puts["strike"]))
    if not all_strikes:
        return {}

    strike_gamma = {}
    for k in all_strikes:
        c_row = calls[calls["strike"] == k]
        p_row = puts[puts["strike"] == k]
        c_iv = float(c_row.iloc[0]["impliedVolatility"]) if not c_row.empty else 0.0
        p_iv = float(p_row.iloc[0]["impliedVolatility"]) if not p_row.empty else 0.0
        c_oi = int(c_row.iloc[0]["openInterest"]) if not c_row.empty else 0
        p_oi = int(p_row.iloc[0]["openInterest"]) if not p_row.empty else 0
        avg_iv = (c_iv + p_iv) / 2 if (c_iv + p_iv) > 0 else 0.01
        g = bs_gamma(current_price, k, T, avg_iv)
        # Dealer net gamma: long calls (from selling puts) vs short calls
        strike_gamma[k] = (c_oi - p_oi) * g * 100

    net_gamma = sum(strike_gamma.values())
    if net_gamma > 1e-8:
        net_gamma_sign = "positive"
    elif net_gamma < -1e-8:
        net_gamma_sign = "negative"
    else:
        net_gamma_sign = "neutral"

    # Gamma flip: walk strikes low→high, find sign-change in cumulative
    gamma_flip_strike = None
    cumulative = 0.0
    prev_cumulative = 0.0
    for k in all_strikes:
        prev_cumulative = cumulative
        cumulative += strike_gamma[k]
        if prev_cumulative != 0.0:
            if (prev_cumulative < 0 and cumulative >= 0) or \
               (prev_cumulative > 0 and cumulative <= 0):
                gamma_flip_strike = k
                break

    if gamma_flip_strike is None and strike_gamma:
        gamma_flip_strike = min(strike_gamma, key=lambda k: abs(strike_gamma[k]))

    oi_concentration_at_mp = None
    if max_pain is not None:
        total_oi = int(calls["openInterest"].sum()) + int(puts["openInterest"].sum())
        if total_oi > 0:
            sorted_strikes = sorted(all_strikes)
            mp_idx = min(range(len(sorted_strikes)),
                         key=lambda i: abs(sorted_strikes[i] - max_pain))
            lo_idx = max(0, mp_idx - 1)
            hi_idx = min(len(sorted_strikes) - 1, mp_idx + 1)
            near_strikes = sorted_strikes[lo_idx:hi_idx + 1]
            near_oi = sum(
                int(calls[calls["strike"] == k]["openInterest"].sum()) +
                int(puts[puts["strike"] == k]["openInterest"].sum())
                for k in near_strikes
            )
            oi_concentration_at_mp = round(near_oi / total_oi, 4)

    return {
        "net_gamma": round(net_gamma, 2),
        "net_gamma_sign": net_gamma_sign,
        "gamma_flip_strike": gamma_flip_strike,
        "oi_concentration_at_mp": oi_concentration_at_mp,
    }


def check_dividend_flag(ticker, opex: date, window_days: int = 10) -> dict:
    """Check if ex-dividend date is within window_days of OpEx.

    `ticker` should be a yfinance Ticker object (or None — returns inert dict).
    """
    if ticker is None:
        return {"dividend_flag": False, "ex_div_date": None}
    try:
        cal = ticker.calendar
        if cal is None or (hasattr(cal, "empty") and cal.empty):
            return {"dividend_flag": False, "ex_div_date": None}
        ex_div = None
        # cal can be a DataFrame (newer yfinance) or dict (older). Handle both.
        cols = list(cal.columns) if hasattr(cal, "columns") else list(cal.keys())
        for col in cols:
            if "dividend" in col.lower() or "ex-div" in col.lower():
                val = cal[col].iloc[0] if hasattr(cal[col], "iloc") else cal[col]
                if val is not None and pd.notna(val):
                    ex_div = pd.to_datetime(val).date()
                    break
        if ex_div is None:
            return {"dividend_flag": False, "ex_div_date": None}
        days_diff = abs((ex_div - opex).days)
        return {
            "dividend_flag": days_diff <= window_days,
            "ex_div_date": str(ex_div),
            "days_to_opex": (opex - ex_div).days,
        }
    except Exception:
        return {"dividend_flag": False, "ex_div_date": None}
