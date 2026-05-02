#!/usr/bin/env python3.11
"""
Inverted-fly wing-width backtest — analogous to bull_put / bear_call moneyness.

Tests four wing widths (% of spot) across the 162-ticker ORATS-historical
universe:
  narrow_2pct, medium_5pct, wide_10pct, vwide_15pct

45-DTE entry on monthly OpEx, slip=0.50, 50%-managed exit (the IF Phase A
finding established managed dominates held-to-expiry; we skip held to keep
run time reasonable).

Output: data/profile/inverted_fly_wing_results.parquet
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BACKTEST_DIR = ROOT / "scripts/backtest"
sys.path.insert(0, str(BACKTEST_DIR))

import config as C  # noqa: E402
from structures import open_inverted_fly, close_cost, intrinsic_value_at_expiry  # noqa: E402
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before  # noqa: E402

C.activate_slip(0.50)

BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_OUT = ROOT / "data/profile/inverted_fly_wing_results.parquet"

ENTRY_DTE = 45
WING_PCTS = {
    "narrow_2pct": 0.02,
    "medium_5pct": 0.05,
    "wide_10pct": 0.10,
    "vwide_15pct": 0.15,
}
MGD_50_THRESHOLD = 0.5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("if_wing")


def _parse_exp(s):
    try:
        return pd.to_datetime(s, errors="coerce")
    except Exception:
        return None


def simulate_one(slice_by_day, available_days, entry_date, expiration, ticker, variant):
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None

    # Override wing-pct for this cell (mutates module-level state).
    C.BFLY_WING_PCT_SPOT = WING_PCTS[variant]

    pos = open_inverted_fly(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None

    spot_entry = pos.underlying_entry
    debit = -pos.entry_credit  # negative entry_credit = debit
    wing_width = pos.notes["wing_width"]
    center_k = pos.notes["center_k"]

    # Forward simulation: track 50% managed exit (close when debit recoverable ≤ 0.5 × max_profit)
    # IF max_profit = wing - debit. 50% target = 0.5 × wing (close when MTM ≥ 0.5 × max_profit).
    # Equivalent: close when current_debit ≥ debit + 0.5 × (wing - debit), i.e. cost-to-close ≥ this.
    # For simplicity, mirror the bull_put pattern: close when MTM ≥ 0.5 × initial debit.
    mgd_50_exit_date = None
    mgd_50_pnl = None

    forward_days = [d for d in available_days if d > entry_date and d <= expiration.date()]
    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        cost = close_cost(pos, chain_d)
        if cost is None:
            continue
        # close_cost: positive = paid to close. For IF (debit), pnl_if_closed = entry_credit - cost
        # entry_credit is NEGATIVE; cost is NEGATIVE (we receive money to close).
        # pnl = entry_credit - cost. If we entered at -$1.00 (paid $1) and now can close for -$1.50
        # (receive $1.50 to close = our position is worth +$1.50), then pnl = -1.0 - (-1.5) = +0.5.
        # 50% managed = close when pnl ≥ 0.5 × max_profit = 0.5 × (wing - debit)
        max_profit = wing_width - debit
        pnl_if_closed = pos.entry_credit - cost
        if mgd_50_exit_date is None and pnl_if_closed >= 0.5 * max_profit:
            mgd_50_exit_date = d
            mgd_50_pnl = pnl_if_closed

    last_chain = slice_by_day.get(expiration.date())
    if last_chain is not None and not last_chain.empty:
        S_exp = float(last_chain["stkPx"].iloc[0])
        held_pnl = pos.entry_credit + intrinsic_value_at_expiry(pos, S_exp)
    else:
        S_exp = float(slice_by_day[forward_days[-1]]["stkPx"].iloc[0]) if forward_days else spot_entry
        held_pnl = pos.entry_credit + intrinsic_value_at_expiry(pos, S_exp)

    if mgd_50_exit_date is None:
        mgd_50_pnl = held_pnl
        mgd_50_exit_date = expiration.date()

    return {
        "ticker": ticker, "wing_variant": variant,
        "entry_date": pd.Timestamp(entry_date), "expiration": expiration,
        "spot_entry": spot_entry, "spot_exit": S_exp,
        "center_k": center_k, "wing_width": wing_width,
        "debit": float(debit),
        "held_pnl": float(held_pnl), "held_win": int(held_pnl > 0),
        "mgd50_pnl": float(mgd_50_pnl), "mgd50_win": int(mgd_50_pnl > 0),
        "mgd50_exit_date": pd.Timestamp(mgd_50_exit_date),
        "mgd50_triggered_early": int(mgd_50_exit_date < expiration.date()),
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

    exp_to_str = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None and ts not in exp_to_str:
            exp_to_str[ts] = s

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]
    sorted_dates = sorted(tdf["date_only"].unique())

    rows = []
    for opex in opex_eligible:
        opex_ts = pd.Timestamp(opex)
        if opex_ts not in exp_to_str:
            continue
        exp_str = exp_to_str[opex_ts]
        target_entry = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target_entry, sorted_dates)
        if entry_date is None:
            continue
        cycle_df = tdf[tdf["expirDate"] == exp_str]
        if cycle_df.empty:
            continue
        cycle_slice_by_day = {d: g.sort_values("strike").reset_index(drop=True)
                              for d, g in cycle_df.groupby("date_only")}
        available_days = sorted(cycle_slice_by_day.keys())
        for variant in WING_PCTS:
            r = simulate_one(cycle_slice_by_day, available_days, entry_date,
                             opex_ts, ticker, variant)
            if r is not None:
                rows.append(r)
    return rows


def main():
    tickers = sorted(p.stem for p in BY_TICKER.glob("*.parquet"))
    log.info("Universe: %d tickers", len(tickers))
    all_rows = []
    for i, t in enumerate(tickers, 1):
        try:
            rows = simulate_ticker(t)
        except Exception as e:
            log.warning("  %s failed: %s", t, e)
            rows = []
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(tickers):
            log.info("  [%d/%d] %s — total cycle rows: %d", i, len(tickers), t, len(all_rows))
    if not all_rows:
        log.error("Zero cycle rows produced")
        sys.exit(1)
    df = pd.DataFrame(all_rows)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d rows to %s", len(df), RESULTS_OUT)
    log.info("Per-variant counts: %s", df["wing_variant"].value_counts().to_dict())


if __name__ == "__main__":
    main()
