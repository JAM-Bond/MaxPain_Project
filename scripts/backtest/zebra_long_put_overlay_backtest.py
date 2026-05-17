"""ZEBRA + long-put overlay backtest — Phase 1 (at-entry variants).

Pre-registered design 2026-05-14 (see project_zebra_long_put_overlay.md +
post_june_opex_watchlist.md item 8).

Per (ticker, monthly OpEx cycle):
  1. Find target expiration ~75 DTE before opex_date.
  2. Open ZEBRA (existing logic).
  3. ALSO open a long put at 3 strike levels, same expiration:
       V1: ATM (closest to spot)
       V2: 5% OTM (5% below spot)
       V3: 10% OTM (10% below spot)
  4. Walk forward to expiration; settle ZEBRA + each put on intrinsic.
  5. Per cycle, compute:
       pnl_zebra            (base case)
       pnl_v1_put / v1_combined  (V1 put only / ZEBRA + V1 put)
       pnl_v2_put / v2_combined
       pnl_v3_put / v3_combined

Phase 1 = at-entry overlay variants only.
Phase 2 (later) = conditional drawdown / regime triggers.

Output:
  data/profile/zebra_put_overlay_results.parquet  (one row per cycle)
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
from structures import open_zebra, close_cost, intrinsic_value_at_expiry, max_loss
from legs import Position, Leg, price_long_put, close_cost_put

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"

ENTRY_DTE = 75
DEFAULT_SLIP = 0.25  # match Phase C
SLIP = DEFAULT_SLIP   # overridable via --slip

# Cohorts (mirror scripts/qualifier/gate_config.py)
TIER1 = ["SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN"]
TIER2 = ["DIA", "IWM", "GLD", "TJX", "GE", "WMT", "AMD", "PLTR",
         "KRE", "CMG", "SCHW", "CSCO", "TTD", "USB"]
COHORTS = {"tier1": TIER1, "tier2": TIER2}

# Put-overlay strike grid (% below spot at entry)
PUT_VARIANTS = {
    "v1_atm":   0.00,
    "v2_otm5":  0.05,
    "v3_otm10": 0.10,
}


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def open_long_put_at_strike_pct(chain: pd.DataFrame, spot: float,
                                 strike_pct_below: float, expiration) -> tuple:
    """Pick a long put with strike closest to spot * (1 - strike_pct_below).

    Returns (Position-like dict, debit) or (None, None).
    Uses price_long_put for entry fill convention.
    """
    target_strike = spot * (1.0 - strike_pct_below)
    # Filter to rows with valid put pricing data
    candidates = chain.dropna(subset=["pBidPx", "pAskPx", "pMidIv"]).copy()
    if candidates.empty:
        return None, None
    candidates = candidates[candidates["pMidIv"] >= C.MIN_IV_FOR_PRICING]
    if candidates.empty:
        return None, None
    idx = (candidates["strike"] - target_strike).abs().idxmin()
    row = candidates.loc[idx]
    K = float(row["strike"])
    px = price_long_put(row)
    if px is None or px <= 0:
        return None, None
    # Synthesize a minimal Position for close_cost lookup compatibility
    # We don't need full Leg metadata since we track this separately
    return {
        "strike": K,
        "entry_px": float(px),
        "expiration": expiration,
        "iv_entry": float(row["pMidIv"]),
        "spot_entry": spot,
    }, float(px)


def close_cost_long_put(put_pos: dict, chain: pd.DataFrame) -> float:
    """Mark-to-market a long put on the given chain."""
    K = put_pos["strike"]
    # Find the row with matching strike
    row_match = chain[chain["strike"] == K]
    if row_match.empty:
        return None
    row = row_match.iloc[0]
    # close_cost_put for a LONG put = what we'd receive selling = price_short_put logic (sell side)
    # The long put close cost is the negative of the close credit
    px = close_cost_put(row, "long")
    if px is None or px <= 0:
        # Try the bid directly as fallback
        bid = row.get("pBidPx")
        return float(bid) if pd.notna(bid) and bid > 0 else 0.0
    return float(px)


def intrinsic_put(K: float, S_exp: float) -> float:
    return max(0.0, K - S_exp)


def simulate_cycle(slice_by_day, available_days, entry_date, expiration, ticker):
    """Open ZEBRA + 3 long-put variants, walk to expiration, settle.

    Returns dict with per-variant P&L.
    """
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    zpos = open_zebra(entry_chain, pd.Timestamp(entry_date), expiration)
    if zpos is None:
        return None

    spot_entry = zpos.underlying_entry
    z_debit = zpos.notes["debit"]

    # Open each put variant
    puts = {}
    for label, pct in PUT_VARIANTS.items():
        p, debit = open_long_put_at_strike_pct(entry_chain, spot_entry, pct, expiration)
        if p is not None:
            puts[label] = (p, debit)

    if not puts:
        return None

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]
    last_chain = slice_by_day.get(expiration.date())

    if last_chain is None or last_chain.empty:
        # Use last available day; spot only
        last_d = forward_days[-1] if forward_days else None
        if last_d is None:
            return None
        last_chain = slice_by_day.get(last_d)
        if last_chain is None or last_chain.empty:
            return None

    S_exp = float(last_chain["stkPx"].iloc[0])

    # ZEBRA settles via intrinsic
    pnl_zebra = float(zpos.entry_credit + intrinsic_value_at_expiry(zpos, S_exp))

    # Put variants settle via intrinsic
    out = {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "return_pct": (S_exp / spot_entry - 1.0) * 100,
        "zebra_debit": float(z_debit),
        "long_strike": zpos.notes["long_strike"],
        "short_strike": zpos.notes["short_strike"],
        "pnl_zebra": pnl_zebra,
    }
    for label, (p, debit) in puts.items():
        K = p["strike"]
        intrinsic = intrinsic_put(K, S_exp)
        # Long put P&L = intrinsic at expiry - entry debit
        pnl_put = intrinsic - debit
        out[f"{label}_strike"] = K
        out[f"{label}_debit"] = float(debit)
        out[f"pnl_{label}_put"] = float(pnl_put)
        out[f"pnl_{label}_combined"] = float(pnl_zebra + pnl_put)

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
    ap.add_argument("--cohort", choices=list(COHORTS), default="tier1",
                    help="Which cohort to run (default: tier1 — matches Phase 1).")
    ap.add_argument("--slip", type=float, default=DEFAULT_SLIP,
                    help=f"Bid-ask slip (default: {DEFAULT_SLIP} — matches Phase C).")
    args = ap.parse_args()

    SLIP = args.slip
    cohort_name = args.cohort
    cohort = COHORTS[cohort_name]
    suffix = "" if args.slip == DEFAULT_SLIP else f"_slip{int(args.slip * 100):02d}"
    if cohort_name == "tier1":
        results_out = ROOT / f"data/profile/zebra_put_overlay_results{suffix}.parquet"
    else:
        results_out = ROOT / f"data/profile/zebra_put_overlay_{cohort_name}_results{suffix}.parquet"

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("zebra_overlay")
    log.info("ZEBRA + long-put overlay backtest on %s cohort: %s", cohort_name, cohort)

    all_results = []
    for i, t in enumerate(cohort, 1):
        s = simulate_ticker(t)
        all_results.extend(s)
        log.info("  [%d/%d] %s: %d cycles", i, len(cohort), t, len(s))

    if not all_results:
        log.error("No cycles produced")
        return

    df = pd.DataFrame(all_results)
    results_out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(results_out, index=False)
    log.info("Wrote %d cycles to %s", len(df), results_out)

    # Quick summary
    print(f"\n=== ZEBRA + long-put overlay results ({cohort_name} cohort, all years) ===")
    print(f"Total cycles: {len(df)}")
    print()

    base_mean = df["pnl_zebra"].mean()
    base_win = (df["pnl_zebra"] > 0).mean()
    base_min = df["pnl_zebra"].min()
    base_std = df["pnl_zebra"].std()
    print(f"  BASE (ZEBRA only):  mean=${base_mean:+.2f}  win={base_win:.1%}  worst=${base_min:+.2f}  std=${base_std:.2f}")

    for label in PUT_VARIANTS:
        col = f"pnl_{label}_combined"
        if col not in df.columns:
            continue
        m = df[col].mean()
        w = (df[col] > 0).mean()
        mn = df[col].min()
        st = df[col].std()
        put_cost = -df[f"{label}_debit"].mean()  # average premium paid
        print(f"  +{label.upper()} put:     mean=${m:+.2f}  win={w:.1%}  worst=${mn:+.2f}  std=${st:.2f}  avg_put_cost=${put_cost:.2f}")

    print()
    print("=== By ticker (combined ZEBRA + v2_otm5) ===")
    by_t = df.groupby("ticker").agg(
        n=("pnl_zebra", "size"),
        base_mean=("pnl_zebra", "mean"),
        v2_mean=("pnl_v2_otm5_combined", "mean"),
        v2_min=("pnl_v2_otm5_combined", "min"),
        base_min=("pnl_zebra", "min"),
    )
    by_t["lift"] = by_t["v2_mean"] - by_t["base_mean"]
    by_t["worst_reduction"] = by_t["v2_min"] - by_t["base_min"]
    print(by_t.to_string())


if __name__ == "__main__":
    main()
