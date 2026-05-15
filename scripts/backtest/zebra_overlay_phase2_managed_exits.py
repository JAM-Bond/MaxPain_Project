"""ZEBRA + put overlay Phase 2 — M2/M3/M4 managed-exit variants on the put.

Phase 1 V3 (10% OTM, held to OpEx) is the baseline. M1 (T-21 close) was
rejected. This script tests three remaining managed-exit variants on the
SAME V3 (10% OTM) put structure:

  M2 : close put when proceeds >= 1.5 * debit (50% gain on put)
  M3 : close put when proceeds >= 2.0 * debit (100% gain — put doubles)
  M4 : time-staircase — three separate runs at T-30 / T-21 / T-14

ZEBRA itself is held to OpEx in every variant. Only the put is managed.
HOLD baseline (Phase 1 V3) is reproduced inline for direct comparison.

Output: data/profile/zebra_put_overlay_phase2_managed_exits.parquet
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before
from structures import open_zebra, intrinsic_value_at_expiry
from legs import price_long_put, close_cost_put

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_OUT = ROOT / "data/profile/zebra_put_overlay_phase2_managed_exits.parquet"

ENTRY_DTE = 75
SLIP = 0.25
TIER1 = ["SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN"]

PUT_PCT_BELOW = 0.10            # V3 = 10% OTM
M2_PROFIT_MULT = 1.5            # close at 50% gain
M3_PROFIT_MULT = 2.0            # close at 100% gain
M4_DTE_THRESHOLDS = [30, 21, 14]


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def open_long_put(chain, spot, signed_pct, expiration):
    target_strike = spot * (1.0 - signed_pct)
    cand = chain.dropna(subset=["pBidPx", "pAskPx", "pMidIv"]).copy()
    if cand.empty:
        return None, None
    cand = cand[cand["pMidIv"] >= C.MIN_IV_FOR_PRICING]
    if cand.empty:
        return None, None
    idx = (cand["strike"] - target_strike).abs().idxmin()
    row = cand.loc[idx]
    K = float(row["strike"])
    px = price_long_put(row)
    if px is None or px <= 0:
        return None, None
    return {"strike": K}, float(px)


def mark_long_put(put_pos, chain):
    K = put_pos["strike"]
    row_match = chain[chain["strike"] == K]
    if row_match.empty:
        return None
    row = row_match.iloc[0]
    px = close_cost_put(row, "long")
    if px is not None and px > 0:
        return float(px)
    bid = row.get("pBidPx")
    return float(bid) if pd.notna(bid) and bid > 0 else 0.0


def intrinsic_put(K, S_exp):
    return max(0.0, K - S_exp)


def simulate_cycle(slice_by_day, available_days, entry_date, expiration, ticker):
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    zpos = open_zebra(entry_chain, pd.Timestamp(entry_date), expiration)
    if zpos is None:
        return None

    spot_entry = zpos.underlying_entry
    put_pos, put_debit = open_long_put(entry_chain, spot_entry, PUT_PCT_BELOW, expiration)
    if put_pos is None:
        return None

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]

    # Walk through cycle once, capturing all variant exit points.
    m2_close = None  # (proceeds, date, dte)
    m3_close = None
    m4_close = {dte_t: None for dte_t in M4_DTE_THRESHOLDS}

    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        proceeds = mark_long_put(put_pos, chain_d)
        if proceeds is None:
            continue
        dte = (expiration.date() - d).days

        # M2: first time proceeds >= 1.5 * debit
        if m2_close is None and proceeds >= M2_PROFIT_MULT * put_debit:
            m2_close = (proceeds, d, dte)

        # M3: first time proceeds >= 2.0 * debit
        if m3_close is None and proceeds >= M3_PROFIT_MULT * put_debit:
            m3_close = (proceeds, d, dte)

        # M4 staircase: capture proceeds at first day where dte <= threshold
        for dte_t in M4_DTE_THRESHOLDS:
            if m4_close[dte_t] is None and dte <= dte_t:
                m4_close[dte_t] = (proceeds, d, dte)

    # Settle at expiration (intrinsic) for HOLD baseline + any unclosed variant
    last_chain = slice_by_day.get(expiration.date())
    if last_chain is None or last_chain.empty:
        last_d = forward_days[-1] if forward_days else None
        if last_d is None:
            return None
        last_chain = slice_by_day.get(last_d)
        if last_chain is None or last_chain.empty:
            return None

    S_exp = float(last_chain["stkPx"].iloc[0])
    pnl_zebra = float(zpos.entry_credit + intrinsic_value_at_expiry(zpos, S_exp))

    intrinsic = intrinsic_put(put_pos["strike"], S_exp)
    pnl_put_hold = intrinsic - put_debit

    def _put_pnl(closeinfo):
        if closeinfo is None:
            return pnl_put_hold, True   # no fire → fallback to HOLD
        proceeds, _, _ = closeinfo
        return proceeds - put_debit, False

    pnl_m2, m2_fb = _put_pnl(m2_close)
    pnl_m3, m3_fb = _put_pnl(m3_close)
    pnl_m4 = {}
    m4_fb = {}
    for dte_t in M4_DTE_THRESHOLDS:
        p, fb = _put_pnl(m4_close[dte_t])
        pnl_m4[dte_t] = p
        m4_fb[dte_t] = fb

    row = {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "return_pct": (S_exp / spot_entry - 1.0) * 100,
        "pnl_zebra": pnl_zebra,
        "v3_strike": put_pos["strike"],
        "v3_debit": float(put_debit),

        "pnl_put_hold": float(pnl_put_hold),
        "pnl_combined_hold": float(pnl_zebra + pnl_put_hold),

        "pnl_put_m2": float(pnl_m2),
        "pnl_combined_m2": float(pnl_zebra + pnl_m2),
        "m2_fired": (m2_close is not None),

        "pnl_put_m3": float(pnl_m3),
        "pnl_combined_m3": float(pnl_zebra + pnl_m3),
        "m3_fired": (m3_close is not None),
    }
    for dte_t in M4_DTE_THRESHOLDS:
        row[f"pnl_put_m4_t{dte_t}"] = float(pnl_m4[dte_t])
        row[f"pnl_combined_m4_t{dte_t}"] = float(pnl_zebra + pnl_m4[dte_t])
        row[f"m4_t{dte_t}_fired"] = (m4_close[dte_t] is not None)
    return row


def simulate_ticker(ticker):
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


def report(df):
    n = len(df)
    print(f"\n=== Phase 2 managed-exit variants (V3 10%-OTM put, slip={SLIP}) ===")
    print(f"cycles: {n}\n")

    variants = [
        ("HOLD (Phase 1 V3)",        "pnl_combined_hold",         None),
        ("M2 (close put @ 1.5x)",    "pnl_combined_m2",           "m2_fired"),
        ("M3 (close put @ 2.0x)",    "pnl_combined_m3",           "m3_fired"),
        ("M4 T-30 staircase",        "pnl_combined_m4_t30",       "m4_t30_fired"),
        ("M4 T-21 staircase",        "pnl_combined_m4_t21",       "m4_t21_fired"),
        ("M4 T-14 staircase",        "pnl_combined_m4_t14",       "m4_t14_fired"),
    ]

    base = df["pnl_combined_hold"].mean()
    for label, col, fircol in variants:
        m = df[col].mean()
        w = (df[col] > 0).mean()
        mn = df[col].min()
        sd = df[col].std()
        lift = m - base
        fire_str = ""
        if fircol:
            fire_pct = df[fircol].mean()
            fire_str = f"  fired={fire_pct:.1%}"
        print(f"  {label:24s} mean=${m:+.2f}  win={w:.1%}  worst=${mn:+.2f}  std=${sd:.2f}  lift_vs_HOLD=${lift:+.2f}{fire_str}")

    print("\n=== Walk-forward (lift vs HOLD per split × variant) ===")
    df = df.copy()
    df["val_year"] = pd.to_datetime(df["expiration"]).dt.year
    splits = [
        ("2021-2023", range(2021, 2024)),
        ("2022-2024", range(2022, 2025)),
        ("2023-2025", range(2023, 2026)),
        ("2024-2026", range(2024, 2027)),
    ]
    cols = ["pnl_combined_m2", "pnl_combined_m3",
            "pnl_combined_m4_t30", "pnl_combined_m4_t21", "pnl_combined_m4_t14"]
    headers = ["M2", "M3", "M4_T30", "M4_T21", "M4_T14"]
    print("  split        " + "  ".join(f"{h:>7s}" for h in headers))
    pos_count = {c: 0 for c in cols}
    for slabel, yrs in splits:
        m = df[df["val_year"].isin(list(yrs))]
        if m.empty:
            continue
        hbase = m["pnl_combined_hold"].mean()
        parts = []
        for col in cols:
            lift = m[col].mean() - hbase
            if lift > 0:
                pos_count[col] += 1
            parts.append(f"{lift:+7.2f}")
        print(f"  {slabel}: " + "  ".join(parts))
    print("\n  Positive splits / 4:")
    for col, h in zip(cols, headers):
        print(f"    {h:7s}  {pos_count[col]}/4")


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("zebra_p2_mexits")
    log.info("Phase 2 managed-exit variants on tier-1: %s", TIER1)

    all_results = []
    for i, t in enumerate(TIER1, 1):
        s = simulate_ticker(t)
        all_results.extend(s)
        log.info("  [%d/%d] %s: %d cycles", i, len(TIER1), t, len(s))

    if not all_results:
        log.error("No cycles produced")
        return

    df = pd.DataFrame(all_results)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d cycles to %s", len(df), RESULTS_OUT)
    report(df)


if __name__ == "__main__":
    main()
