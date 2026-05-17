"""Anti-ZEBRA + long-call overlay backtest.

Mirror of `zebra_long_put_overlay_backtest.py` but for anti-ZEBRA's short-delta
side: the protective leg is an OTM CALL (upside protection on a short-delta
position) instead of an OTM PUT.

Per (ticker, monthly OpEx cycle):
  1. Find target expiration ~75 DTE before opex_date.
  2. Open anti-ZEBRA (existing logic).
  3. ALSO open a long call at 3 strike levels, same expiration:
       W1: ATM (closest to spot)
       W2: 5% OTM (5% above spot)
       W3: 10% OTM (10% above spot)
  4. Walk forward to expiration; settle anti-ZEBRA + each call on intrinsic.
  5. Per cycle, compute pnl_anti_zebra (base) + pnl_w{1,2,3}_call + combined.

Pre-registration: docs/ANTI_ZEBRA_PREREG.md (sub-question 3).

Output:
  data/profile/anti_zebra_long_call_overlay_results.parquet  (one row per cycle)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before
from structures import open_anti_zebra, intrinsic_value_at_expiry
from legs import price_long_call, close_cost_call

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"

ENTRY_DTE = 75
DEFAULT_SLIP = 0.50  # match anti-ZEBRA Phase 1 primary
SLIP = DEFAULT_SLIP

# v1.5 deployable cohort (matches anti_zebra_backtest.py)
V15_COHORT = [
    "AMZN", "GOOGL", "META", "MSFT", "NVDA", "QQQ", "SPY",
    "AMD", "DIA", "GE", "GLD", "PLTR", "TJX", "WMT",
    "BABA", "CAR", "CLF", "CNC", "CNQ", "DAL", "EFA", "GOLD",
    "HYG", "INTC", "IWM", "KO", "NEM", "NUE", "PG", "RIO",
    "RRC", "SCCO", "TSLA", "WFC", "XLU", "XOM",
]

# Call-overlay strike grid (% above spot at entry)
CALL_VARIANTS = {
    "w1_atm":   0.00,
    "w2_otm5":  0.05,
    "w3_otm10": 0.10,
}


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def open_long_call_at_strike_pct(chain: pd.DataFrame, spot: float,
                                  strike_pct_above: float, expiration) -> tuple:
    """Pick a long call with strike closest to spot * (1 + strike_pct_above).

    Returns (Position-like dict, debit) or (None, None).
    """
    target_strike = spot * (1.0 + strike_pct_above)
    candidates = chain.dropna(subset=["cBidPx", "cAskPx", "cMidIv"]).copy()
    if candidates.empty:
        return None, None
    candidates = candidates[candidates["cMidIv"] >= C.MIN_IV_FOR_PRICING]
    if candidates.empty:
        return None, None
    idx = (candidates["strike"] - target_strike).abs().idxmin()
    row = candidates.loc[idx]
    K = float(row["strike"])
    px = price_long_call(row)
    if px is None or px <= 0:
        return None, None
    return {
        "strike": K,
        "entry_px": float(px),
        "expiration": expiration,
        "iv_entry": float(row["cMidIv"]),
        "spot_entry": spot,
    }, float(px)


def intrinsic_call(K: float, S_exp: float) -> float:
    return max(0.0, S_exp - K)


def simulate_cycle(slice_by_day, available_days, entry_date, expiration, ticker):
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    azpos = open_anti_zebra(entry_chain, pd.Timestamp(entry_date), expiration)
    if azpos is None:
        return None

    spot_entry = azpos.underlying_entry
    az_debit = azpos.notes["debit"]

    # Open each call variant
    calls = {}
    for label, pct in CALL_VARIANTS.items():
        c, debit = open_long_call_at_strike_pct(entry_chain, spot_entry, pct, expiration)
        if c is not None:
            calls[label] = (c, debit)

    if not calls:
        return None

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]
    last_chain = slice_by_day.get(expiration.date())
    if last_chain is None or last_chain.empty:
        last_d = forward_days[-1] if forward_days else None
        if last_d is None:
            return None
        last_chain = slice_by_day.get(last_d)
        if last_chain is None or last_chain.empty:
            return None

    S_exp = float(last_chain["stkPx"].iloc[0])

    pnl_az = float(azpos.entry_credit + intrinsic_value_at_expiry(azpos, S_exp))

    out = {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "return_pct": (S_exp / spot_entry - 1.0) * 100,
        "az_debit": float(az_debit),
        "long_strike": azpos.notes["long_strike"],
        "short_strike": azpos.notes["short_strike"],
        "pnl_anti_zebra": pnl_az,
    }
    for label, (c, debit) in calls.items():
        K = c["strike"]
        intrinsic = intrinsic_call(K, S_exp)
        pnl_call = intrinsic - debit
        out[f"{label}_strike"] = K
        out[f"{label}_debit"] = float(debit)
        out[f"pnl_{label}_call"] = float(pnl_call)
        out[f"pnl_{label}_combined"] = float(pnl_az + pnl_call)

    return out


def simulate_ticker(ticker: str) -> list:
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return []
    tdf = pd.read_parquet(path)
    if tdf.empty:
        return []
    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"])
    tdf["date_only"] = tdf["trade_date"].dt.date
    first_date = tdf["trade_date"].min().date()
    last_date = tdf["trade_date"].max().date()

    exp_str_to_date = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None:
            exp_str_to_date[s] = ts

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]
    opex_to_exp = {}
    for opex in opex_eligible:
        ts = pd.Timestamp(opex)
        for s, d in exp_str_to_date.items():
            if abs((d - ts).days) <= 1:
                opex_to_exp[ts] = s
                break

    exp_groups = {s: sub for s, sub in tdf.groupby("expirDate", sort=False)}
    summaries = []

    C.activate_slip(SLIP)
    for opex_ts, exp_str in opex_to_exp.items():
        exp_df = exp_groups[exp_str]
        slice_by_day = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}
        available_days = sorted(slice_by_day.keys())

        target = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target, available_days)
        if entry_date is None:
            continue
        s = simulate_cycle(slice_by_day, available_days, entry_date, opex_ts, ticker)
        if s is not None:
            summaries.append(s)
    return summaries


def main():
    global SLIP
    ap = argparse.ArgumentParser()
    ap.add_argument("--slip", type=float, default=DEFAULT_SLIP)
    args = ap.parse_args()

    SLIP = args.slip
    suffix = "" if args.slip == DEFAULT_SLIP else f"_slip{int(args.slip * 100):02d}"
    results_out = ROOT / f"data/profile/anti_zebra_long_call_overlay_results{suffix}.parquet"

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("az_overlay")
    log.info("Anti-ZEBRA + long-call overlay backtest on v1.5 cohort (N=%d names, slip=%.2f)",
             len(V15_COHORT), SLIP)

    all_results = []
    for i, t in enumerate(V15_COHORT, 1):
        s = simulate_ticker(t)
        all_results.extend(s)
        log.info("  [%d/%d] %s: %d cycles", i, len(V15_COHORT), t, len(s))

    if not all_results:
        log.error("No cycles produced")
        return

    df = pd.DataFrame(all_results)
    results_out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(results_out, index=False)
    log.info("Wrote %d cycles to %s", len(df), results_out)

    print(f"\n=== Anti-ZEBRA + long-call overlay results (v1.5 cohort, all years, slip={SLIP}) ===")
    print(f"Total cycles: {len(df)}")
    print()
    base_mean = df["pnl_anti_zebra"].mean()
    base_win = (df["pnl_anti_zebra"] > 0).mean()
    base_min = df["pnl_anti_zebra"].min()
    print(f"  BASE (anti-ZEBRA only):  mean=${base_mean:+.2f}  win={base_win:.1%}  worst=${base_min:+.2f}")
    for label in CALL_VARIANTS:
        col = f"pnl_{label}_combined"
        if col not in df.columns:
            continue
        m = df[col].mean()
        w = (df[col] > 0).mean()
        mn = df[col].min()
        call_cost = -df[f"{label}_debit"].mean()
        print(f"  +{label.upper()} call:     mean=${m:+.2f}  win={w:.1%}  worst=${mn:+.2f}  avg_call_cost=${call_cost:.2f}")


if __name__ == "__main__":
    main()
