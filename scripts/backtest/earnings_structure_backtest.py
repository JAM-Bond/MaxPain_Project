"""Structure-level earnings backtest — T1, T2, T4 + non-earnings control (T3).

Pre-registration: docs/EARNINGS_PREREG.md (set 2026-04-25 BEFORE this code ran).

Protocol (matched for earnings + control):
  For each (ticker, anchor_date) where anchor_date is either an earnings event
  or a synthetic non-earnings sample:
    1. Find the next monthly OpEx after anchor_date with at least 3 trading days
       buffer (sanity: skip if expiration > 60 calendar days away).
    2. Determine entry_date = anchor_date - entry_offset (in trading days).
    3. Determine exit_date = anchor_date + exit_offset (in trading days), or
       held to expiration if exit_rule == "exp".
    4. Open structure at entry_date chain. If leg selection fails, skip.
    5. Compute close_cost at exit_date OR intrinsic at expiration.
    6. Emit one row per (ticker, anchor_date, structure, entry_offset,
       exit_rule, slip).

Output:
  data/profile/earnings_structure_results.parquet  (raw per-event rows)
  data/profile/earnings_scorecard.parquet          (cohort-level aggregate)
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
from opex_calendar import monthly_opex_dates
from structures import (
    STRUCTURES, close_cost, intrinsic_value_at_expiry, max_profit,
)

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
EVENTS_PATH = ROOT / "data/profile/earnings_events.parquet"
RAW_OUT = ROOT / "data/profile/earnings_structure_results.parquet"
SCORE_OUT = ROOT / "data/profile/earnings_scorecard.parquet"

# Cohort definitions (from EARNINGS_PREREG.md)
COHORT_T1_BIAS_UP = ["SCCO", "CNQ", "KO", "NUE", "KGC", "GOOGL", "NRG",
                     "RRC", "META", "WFC", "CX", "ITUB"]
COHORT_T2_BIAS_DOWN = ["INTC", "JBLU", "NEM", "GLNG", "FCX", "VST", "CAR"]
COHORT_T4_HIGH_VOL = ["RIG", "ENPH", "PLTR", "SNAP", "TME", "TEVA", "CFLT"]

# Test matrix
ENTRY_OFFSETS = {"T-3": 3, "T-1": 1}     # trading days BEFORE anchor
EXIT_RULES = {"T+1": 1, "T+3": 3, "exp": None}  # trading days AFTER anchor; None = held to expiry
SLIPS = [0.25, 0.50]
EXPIRATION_MAX_CALENDAR_DAYS = 60        # skip if next monthly is too far

# Synthetic (non-earnings) control parameters
CONTROL_RATIO = 5                        # synthetic events per real event per ticker
RANDOM_SEED = 20260425

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("earnings_struct")


# ─────────────────────────────────────────────────────────────
# Per-ticker chain handling
# ─────────────────────────────────────────────────────────────

def _parse_exp_str(s: str) -> pd.Timestamp | None:
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def _build_ticker_index(ticker: str) -> dict | None:
    """Build per-ticker fast-lookup structure: trading_days, exp_groups (by_exp_date),
    exp_to_str map, slice_by_day per expiration."""
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["date_only"] = df["trade_date"].dt.date

    exp_str_to_date = {}
    for s in df["expirDate"].unique():
        d = _parse_exp_str(s)
        if d is not None:
            exp_str_to_date[s] = d

    exp_groups = {s: sub for s, sub in df.groupby("expirDate", sort=False)}

    trading_days = sorted(df["date_only"].unique())
    return {
        "df": df,
        "trading_days": trading_days,
        "trading_days_set": set(trading_days),
        "exp_str_to_date": exp_str_to_date,
        "exp_groups": exp_groups,
    }


def _next_monthly_opex_after(anchor: pd.Timestamp, exp_str_to_date: dict
                              ) -> tuple[pd.Timestamp, str] | None:
    """Find the next monthly OpEx (third-Friday) expirDate AFTER anchor.

    We use the project opex calendar to enumerate third-Fridays, then snap to
    the closest expirDate string in this ticker's chain (within ±1 day to handle
    holiday-shifted Fridays)."""
    year_lo = anchor.year
    year_hi = anchor.year + 1
    opex_list = monthly_opex_dates(year_lo, year_hi)
    # Pick first opex strictly after the anchor with at least 3 trading days buffer
    for opex in opex_list:
        opex_ts = pd.Timestamp(opex)
        if opex_ts <= anchor:
            continue
        if (opex_ts - anchor).days > EXPIRATION_MAX_CALENDAR_DAYS:
            return None
        # Find matching expirDate string in this ticker's chain
        for s, d in exp_str_to_date.items():
            if abs((d - opex_ts).days) <= 1:
                return (opex_ts, s)
        # No matching expirDate for this opex, try next
        continue
    return None


def _trading_day_offset(anchor: pd.Timestamp, offset: int,
                        trading_days: list, trading_days_set: set
                        ) -> object | None:
    """Find the trading day that is `offset` business days from anchor.
    offset > 0 means forward (after); offset < 0 means backward (before).

    Returns a date object (matching the trade_date keys) or None.
    """
    anchor_date = anchor.date() if hasattr(anchor, "date") else anchor
    # Find the index in the sorted list of the trading day on/after (or before) anchor
    if offset == 0:
        return anchor_date if anchor_date in trading_days_set else None
    # Find the closest trading day at or before anchor (for negative offsets)
    # or at or after anchor (for positive offsets)
    if offset > 0:
        # Find first trading day >= anchor_date
        ix = None
        for i, d in enumerate(trading_days):
            if d >= anchor_date:
                ix = i
                break
        if ix is None:
            return None
        target = ix + (offset - 1) if anchor_date == trading_days[ix] else ix + offset - 1
        # Simpler: walk forward `offset` trading days starting from the first >= anchor_date
        # If anchor_date is itself a trading day, "T+N" means N trading days AFTER anchor.
        if anchor_date in trading_days_set:
            ix0 = trading_days.index(anchor_date)
            new_ix = ix0 + offset
        else:
            # anchor_date is not a trading day (weekend/holiday). The "next trading day" is
            # already T+0 in spirit, so T+1 means one beyond it.
            new_ix = ix + offset - 1
        if new_ix >= len(trading_days):
            return None
        return trading_days[new_ix]
    # offset < 0: walk backward
    abs_off = -offset
    if anchor_date in trading_days_set:
        ix0 = trading_days.index(anchor_date)
        new_ix = ix0 - abs_off
    else:
        # find the last trading day < anchor_date
        ix = None
        for i in range(len(trading_days) - 1, -1, -1):
            if trading_days[i] < anchor_date:
                ix = i
                break
        if ix is None:
            return None
        new_ix = ix - (abs_off - 1)
    if new_ix < 0:
        return None
    return trading_days[new_ix]


# ─────────────────────────────────────────────────────────────
# Single-event simulation
# ─────────────────────────────────────────────────────────────

def simulate_event(ticker: str, anchor_date: pd.Timestamp,
                   tindex: dict, structure_name: str,
                   entry_label: str, entry_offset: int,
                   exit_label: str, exit_offset: int | None,
                   slip: float, anchor_kind: str
                   ) -> dict | None:
    """Run one (event, structure, entry, exit, slip) cell. Return a row or None."""
    # Find expiration
    exp_pair = _next_monthly_opex_after(anchor_date, tindex["exp_str_to_date"])
    if exp_pair is None:
        return None
    expiration_ts, exp_str = exp_pair

    # Find entry_date = anchor - entry_offset trading days
    entry_date = _trading_day_offset(anchor_date, -entry_offset,
                                     tindex["trading_days"], tindex["trading_days_set"])
    if entry_date is None:
        return None

    # Find exit_date
    if exit_offset is None:
        exit_date = expiration_ts.date()
    else:
        exit_date = _trading_day_offset(anchor_date, +exit_offset,
                                        tindex["trading_days"], tindex["trading_days_set"])
    if exit_date is None:
        return None
    if exit_date > expiration_ts.date():
        # exit-by-time goes past expiration; clamp to expiration
        exit_date = expiration_ts.date()

    # Slice chain by expiration → by day
    exp_df = tindex["exp_groups"].get(exp_str)
    if exp_df is None or exp_df.empty:
        return None
    slice_by_day = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}

    entry_chain = slice_by_day.get(entry_date)
    exit_chain = slice_by_day.get(exit_date)
    if entry_chain is None or entry_chain.empty:
        return None

    # Activate slip pricing
    C.activate_slip(slip)

    open_fn = STRUCTURES[structure_name]
    pos = open_fn(entry_chain, pd.Timestamp(entry_date), expiration_ts)
    if pos is None:
        return None

    mp = max_profit(pos)
    underlying_entry = pos.underlying_entry

    # Compute exit
    if exit_offset is None or exit_date == expiration_ts.date():
        last_chain = slice_by_day.get(expiration_ts.date())
        if last_chain is not None and not last_chain.empty:
            underlying_exit = float(last_chain["stkPx"].iloc[0])
            pnl = pos.entry_credit + intrinsic_value_at_expiry(pos, underlying_exit)
            exit_cost = pos.entry_credit - pnl  # implied
        else:
            return None
    else:
        if exit_chain is None or exit_chain.empty:
            return None
        cost = close_cost(pos, exit_chain)
        if cost is None:
            return None
        pnl = pos.entry_credit - cost
        exit_cost = cost
        underlying_exit = float(exit_chain["stkPx"].iloc[0])

    return {
        "ticker": ticker,
        "anchor_date": pd.Timestamp(anchor_date),
        "anchor_kind": anchor_kind,        # "earnings" or "control"
        "structure": structure_name,
        "entry_label": entry_label,
        "exit_rule": exit_label,
        "slip": slip,
        "expiration": expiration_ts,
        "entry_date": pd.Timestamp(entry_date),
        "exit_date": pd.Timestamp(exit_date),
        "dte_at_entry": int((expiration_ts.date() - entry_date).days),
        "entry_credit": float(pos.entry_credit),
        "exit_cost": float(exit_cost) if exit_cost is not None else np.nan,
        "pnl": float(pnl),
        "max_profit": float(mp),
        "pnl_pct_of_max": float(pnl / mp) if mp and mp > 0 else np.nan,
        "underlying_entry": float(underlying_entry),
        "underlying_exit": float(underlying_exit),
    }


# ─────────────────────────────────────────────────────────────
# Synthetic non-earnings anchor sampling
# ─────────────────────────────────────────────────────────────

def sample_control_anchors(events_for_ticker: pd.DataFrame, trading_days: list,
                           n_synth: int, rng: np.random.Generator
                           ) -> list[pd.Timestamp]:
    """Sample n_synth random trading days from the same date range as events,
    EXCLUDING any day within ±10 trading days of a real earnings event."""
    if events_for_ticker.empty:
        return []
    real_dates = set(pd.Timestamp(d).date() for d in events_for_ticker["earnings_date"])
    # Buffer: exclude ±10 trading-day window around each event
    excluded = set()
    td_arr = trading_days  # list of dates
    td_set = set(td_arr)
    for d in real_dates:
        if d not in td_set:
            # snap to nearest trading day in the array
            for cand in td_arr:
                if cand >= d:
                    d = cand
                    break
        if d not in td_set:
            continue
        ix = td_arr.index(d)
        for j in range(max(0, ix - 10), min(len(td_arr), ix + 11)):
            excluded.add(td_arr[j])

    date_range_lo = events_for_ticker["earnings_date"].min().date()
    date_range_hi = events_for_ticker["earnings_date"].max().date()
    candidates = [d for d in td_arr
                  if date_range_lo <= d <= date_range_hi and d not in excluded]
    if not candidates:
        return []
    n_pick = min(n_synth, len(candidates))
    picks = rng.choice(candidates, size=n_pick, replace=False)
    return [pd.Timestamp(p) for p in picks]


# ─────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────

def cohort_universe() -> tuple[list[str], dict[str, list[str]]]:
    """Return (all_tickers_to_run, structure_per_cohort dict)."""
    structure_per_cohort = {
        "T1": ("bull_put", COHORT_T1_BIAS_UP),
        "T2": ("bear_call", COHORT_T2_BIAS_DOWN),
        "T4": ("inverted_fly", COHORT_T4_HIGH_VOL),
    }
    all_tkrs = sorted(set(
        COHORT_T1_BIAS_UP + COHORT_T2_BIAS_DOWN + COHORT_T4_HIGH_VOL
    ))
    return all_tkrs, structure_per_cohort


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, default=None,
                        help="Run only this ticker (smoke test)")
    parser.add_argument("--limit-events", type=int, default=None,
                        help="Limit to first N earnings events per ticker")
    parser.add_argument("--no-control", action="store_true",
                        help="Skip synthetic control sampling (faster smoke)")
    args = parser.parse_args()

    if not EVENTS_PATH.exists():
        log.error("Run earnings_events_cache.py first")
        return
    events = pd.read_parquet(EVENTS_PATH)
    events["earnings_date"] = pd.to_datetime(events["earnings_date"])
    log.info("Loaded %d earnings events for %d tickers",
             len(events), events["ticker"].nunique())

    all_tkrs, cohort_map = cohort_universe()
    if args.ticker:
        all_tkrs = [args.ticker]

    # T4 uses inverted_fly; we need wider wings (10% of spot). Override at structure
    # level via config. The existing config has BFLY_WING_PCT_SPOT_V2 at 0.0025 (0.25%);
    # for T4 we want 0.10 (10%). We'll set this dynamically when running T4 cells.

    rng = np.random.default_rng(RANDOM_SEED)
    raw_rows = []

    for tkr in all_tkrs:
        log.info("Loading %s chain...", tkr)
        tindex = _build_ticker_index(tkr)
        if tindex is None:
            log.warning("  %s: no chain, skip", tkr)
            continue

        # Decide which cohort(s) this ticker belongs to
        my_cohorts = []
        for label, (struct, names) in cohort_map.items():
            if tkr in names:
                my_cohorts.append((label, struct))

        # Real earnings events for this ticker
        tkr_events = events[events["ticker"] == tkr].copy()
        if args.limit_events:
            tkr_events = tkr_events.head(args.limit_events)
        # Synthetic control anchors (CONTROL_RATIO × N events)
        if args.no_control:
            synth_anchors = []
        else:
            n_synth = CONTROL_RATIO * len(tkr_events)
            synth_anchors = sample_control_anchors(
                tkr_events, tindex["trading_days"], n_synth, rng
            )

        # Build the (anchor, kind) iterator
        anchors = [(d, "earnings") for d in tkr_events["earnings_date"]]
        anchors.extend([(d, "control") for d in synth_anchors])

        for cohort_label, structure_name in my_cohorts:
            # Configure wing scaling for this structure
            if structure_name == "inverted_fly":
                # T4: 10% wings (canonical wide-wings cell)
                C.BFLY_WING_PCT_SPOT_V2 = 0.10
                C.IC_WING_PCT_SPOT_V2 = 0.10
                C.VERTICAL_WING_PCT_SPOT_V2 = 0.005
            else:
                # T1/T2 verticals: 0.5% of spot wings (v2 standard)
                C.BFLY_WING_PCT_SPOT_V2 = 0.0025
                C.IC_WING_PCT_SPOT_V2 = 0.0025
                C.VERTICAL_WING_PCT_SPOT_V2 = 0.005

            for anchor_date, kind in anchors:
                for entry_label, entry_off in ENTRY_OFFSETS.items():
                    for exit_label, exit_off in EXIT_RULES.items():
                        for slip in SLIPS:
                            row = simulate_event(
                                tkr, anchor_date, tindex,
                                structure_name,
                                entry_label, entry_off,
                                exit_label, exit_off,
                                slip, kind,
                            )
                            if row is not None:
                                row["cohort"] = cohort_label
                                raw_rows.append(row)

        log.info("  %s done: %d total rows so far", tkr, len(raw_rows))

    if not raw_rows:
        log.error("No rows produced")
        return

    raw = pd.DataFrame(raw_rows)
    RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
    raw.to_parquet(RAW_OUT, index=False)
    log.info("Wrote %d raw rows to %s", len(raw), RAW_OUT)

    # ─── Cohort-level scorecard ───────────────────────────────
    grp_cols = ["cohort", "ticker", "structure", "entry_label", "exit_rule", "slip"]
    score_rows = []
    for keys, sub in raw.groupby(grp_cols + ["anchor_kind"]):
        cohort, tkr, struct, entry_l, exit_l, slip, kind = keys
        s = sub["pnl"].dropna()
        if len(s) == 0:
            continue
        score_rows.append({
            "cohort": cohort, "ticker": tkr, "structure": struct,
            "entry_label": entry_l, "exit_rule": exit_l, "slip": slip,
            "anchor_kind": kind,
            "N": int(len(s)),
            "mean_pnl": round(float(s.mean()), 4),
            "median_pnl": round(float(s.median()), 4),
            "win_rate": round(float((s > 0).mean()), 3),
            "worst": round(float(s.min()), 2),
            "best": round(float(s.max()), 2),
            "total_pnl": round(float(s.sum()), 2),
            "std_pnl": round(float(s.std(ddof=1)), 4) if len(s) > 1 else np.nan,
        })
    score_df = pd.DataFrame(score_rows)

    # Pivot to add lift_vs_control column
    pivot = score_df.pivot_table(
        index=grp_cols, columns="anchor_kind",
        values=["N", "mean_pnl", "win_rate", "median_pnl"],
        aggfunc="first"
    )
    # Flatten columns
    pivot.columns = [f"{a}_{b}" for a, b in pivot.columns.to_flat_index()]
    pivot = pivot.reset_index()
    if "mean_pnl_earnings" in pivot.columns and "mean_pnl_control" in pivot.columns:
        pivot["lift_vs_control"] = (
            pivot["mean_pnl_earnings"] - pivot["mean_pnl_control"]
        ).round(4)

    SCORE_OUT.parent.mkdir(parents=True, exist_ok=True)
    pivot.to_parquet(SCORE_OUT, index=False)
    log.info("Wrote %d scorecard rows to %s", len(pivot), SCORE_OUT)


if __name__ == "__main__":
    main()
