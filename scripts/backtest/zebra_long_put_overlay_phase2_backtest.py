"""ZEBRA + long-put overlay backtest — Phase 2 (managed exit on the put leg).

Phase 1 (held-to-expiry, at-entry V1/V2/V3) PASSED 2026-05-14. This phase
extends the engine to track daily MTM of the put through the cycle so that
managed exits can be evaluated.

This first cut implements two variants on the V3 (10% OTM) put — the
validated Phase 1 structure:

  HOLD : hold the put to OpEx; settle on intrinsic        (== Phase 1 V3 baseline)
  M1   : close the put at T-21 (21 cal days before its expiry)

ZEBRA is unchanged in both variants (still settled on intrinsic at OpEx).
Phase 2 only manages the put leg.

Output:
  data/profile/zebra_put_overlay_phase2_results.parquet
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
RESULTS_OUT = ROOT / "data/profile/zebra_put_overlay_phase2_results.parquet"

ENTRY_DTE = 75
SLIP = 0.25
TIER1 = ["SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN"]

PUT_PCT_BELOW = 0.10   # V3
T21_DAYS = 21          # cal-day threshold for M1 close


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def open_long_put_at_strike_pct(chain, spot, strike_pct_below, expiration):
    target_strike = spot * (1.0 - strike_pct_below)
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
    return {
        "strike": K,
        "entry_px": float(px),
        "expiration": expiration,
        "spot_entry": spot,
    }, float(px)


def mark_long_put(put_pos, chain):
    """Return the proceeds (slip-adjusted) of selling the long put on this chain.

    None if no quote available. Falls back to bid if slip_sell returns nothing.
    """
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

    put_pos, put_debit = open_long_put_at_strike_pct(
        entry_chain, spot_entry, PUT_PCT_BELOW, expiration
    )
    if put_pos is None:
        return None

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]

    # M1: find the first forward day where DTE <= T21_DAYS and we have a quote.
    m1_close_proceeds = None
    m1_close_date = None
    m1_close_dte = None
    for d in forward_days:
        dte = (expiration.date() - d).days
        if dte > T21_DAYS:
            continue
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        proceeds = mark_long_put(put_pos, chain_d)
        if proceeds is None:
            continue
        m1_close_proceeds = proceeds
        m1_close_date = d
        m1_close_dte = dte
        break

    # HOLD: settle put + zebra at expiration (or last available day)
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

    # HOLD put P/L (Phase 1 V3 baseline)
    pnl_put_hold = intrinsic_put(put_pos["strike"], S_exp) - put_debit

    # M1 put P/L
    if m1_close_proceeds is None:
        # No T-21 quote available — degrade gracefully by reusing HOLD (rare edge case)
        pnl_put_m1 = pnl_put_hold
        m1_fallback = True
    else:
        pnl_put_m1 = float(m1_close_proceeds) - put_debit
        m1_fallback = False

    return {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "return_pct": (S_exp / spot_entry - 1.0) * 100,
        "zebra_debit": float(zpos.notes["debit"]),
        "pnl_zebra": pnl_zebra,
        "v3_strike": put_pos["strike"],
        "v3_debit": float(put_debit),

        # HOLD variant
        "pnl_v3_put_hold": float(pnl_put_hold),
        "pnl_v3_combined_hold": float(pnl_zebra + pnl_put_hold),

        # M1 variant
        "m1_close_date": pd.Timestamp(m1_close_date) if m1_close_date else pd.NaT,
        "m1_close_dte": m1_close_dte,
        "m1_fallback_to_hold": m1_fallback,
        "pnl_v3_put_m1": float(pnl_put_m1),
        "pnl_v3_combined_m1": float(pnl_zebra + pnl_put_m1),
    }


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


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("zebra_phase2")
    log.info("ZEBRA + V3 overlay Phase 2 on tier-1: %s", TIER1)

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

    n = len(df)
    fb = int(df["m1_fallback_to_hold"].sum())
    print(f"\n=== Phase 2 ZEBRA+V3 overlay — managed exit on the put ===")
    print(f"cycles: {n}   T-21 fallback-to-HOLD: {fb}   slip={SLIP}\n")

    def _row(label, series, put_cost_series=None):
        m = series.mean()
        w = (series > 0).mean()
        mn = series.min()
        sd = series.std()
        extra = ""
        if put_cost_series is not None:
            extra = f"  avg_put_cost=${-put_cost_series.mean():.2f}"
        print(f"  {label:22s} mean=${m:+.2f}  win={w:.1%}  worst=${mn:+.2f}  std=${sd:.2f}{extra}")

    _row("ZEBRA only (baseline)", df["pnl_zebra"])
    _row("+V3 HOLD (Phase 1)",    df["pnl_v3_combined_hold"], df["v3_debit"])
    _row("+V3 M1 (T-21 close)",   df["pnl_v3_combined_m1"],   df["v3_debit"])

    lift = df["pnl_v3_combined_m1"].mean() - df["pnl_v3_combined_hold"].mean()
    print(f"\n  M1 vs HOLD lift: ${lift:+.2f}/cycle")

    # Per-ticker comparison
    print("\n=== Per-ticker (V3 combined) ===")
    by_t = df.groupby("ticker").agg(
        n=("pnl_zebra", "size"),
        zebra=("pnl_zebra", "mean"),
        hold=("pnl_v3_combined_hold", "mean"),
        m1=("pnl_v3_combined_m1", "mean"),
    )
    by_t["m1_vs_hold"] = by_t["m1"] - by_t["hold"]
    print(by_t.to_string())

    # Walk-forward splits (match Phase 1: 3-yr validation windows)
    print("\n=== Walk-forward (M1 lift over HOLD per split) ===")
    df["val_year"] = pd.to_datetime(df["expiration"]).dt.year
    splits = [
        ("2021-2023", range(2021, 2024)),
        ("2022-2024", range(2022, 2025)),
        ("2023-2025", range(2023, 2026)),
        ("2024-2026", range(2024, 2027)),
    ]
    rows = []
    pos_splits = 0
    for label, yrs in splits:
        m = df[df["val_year"].isin(list(yrs))]
        if m.empty:
            rows.append((label, 0, None, None, None))
            continue
        hold_mean = m["pnl_v3_combined_hold"].mean()
        m1_mean = m["pnl_v3_combined_m1"].mean()
        lift = m1_mean - hold_mean
        if lift > 0:
            pos_splits += 1
        rows.append((label, len(m), hold_mean, m1_mean, lift))
    for label, n, hold_mean, m1_mean, lift in rows:
        if n == 0:
            print(f"  {label}: no cycles")
            continue
        print(f"  {label}: n={n}  HOLD ${hold_mean:+.2f}  M1 ${m1_mean:+.2f}  lift ${lift:+.2f}/cyc")
    print(f"\n  Splits with M1 > HOLD: {pos_splits}/4")


if __name__ == "__main__":
    main()
