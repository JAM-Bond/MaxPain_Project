#!/usr/bin/env python3.11
"""Price-breach stop-loss discovery study — bull put & bear call verticals.

Question (user-specified): on the 45-DTE managed credit verticals, when the
underlying VIOLATES the short strike by 2% / 5% / 7% at any point during the
hold, is it more profitable to CLOSE the spread at the breach or to WAIT for a
possible recovery?

Breach is measured RELATIVE TO THE SHORT STRIKE:
  - bull_put  (short put high strike): breach_X  when spot <= short_K * (1 - X)
    (adverse direction is DOWN — the short put goes in-the-money).
  - bear_call (short call low strike): breach_X  when spot >= short_K * (1 + X)
    (adverse direction is UP).

For every (ticker, monthly cycle, structure) we open at ~45 DTE and walk each
trading day, recording the daily spot, the slipped cost-to-close (MTM), and DTE.
From that single walk we derive every trigger date and price each exit:

  held_pnl    : hold to OpEx        = credit + intrinsic_at_expiry
  managed_pnl : first of {50% profit (t_50), 21-DTE (t_21)} else held
  breach_pnl_X: close SAME DAY the breach first occurs, at slipped close_cost

Two baselines are reported per the study spec ("run both"):
  A) close-at-breach  vs  hold-to-expiry
  B) close-at-breach  vs  managed-hold (the 50%/21-DTE baseline)
Both comparisons are restricted to the cycles where the breach actually fires
(un-breached cycles never trigger the stop, so they are irrelevant). For the
managed comparison the stop only *binds* when the breach day is strictly earlier
than the 50%/21-DTE exit; otherwise the managed exit already took the trade off.

Pricing: ORATS EOD chains, slip=0.50 (mid +/- a quarter-spread haircut), the
standard friction used across the engine. Universe: full 150 (universe_v1).
P&L is per-share (per 1 contract; multiply by 100 for per-contract dollars),
matching the rest of the system's reporting convention.

Output: data/profile/price_breach_stop_results.parquet  (one row per cycle/struct)
        + a printed summary table.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BACKTEST_DIR = ROOT / "scripts/backtest"
sys.path.insert(0, str(BACKTEST_DIR))

import config as C  # noqa: E402
from structures import (  # noqa: E402
    open_bull_put,
    open_bear_call,
    close_cost,
    intrinsic_value_at_expiry,
)
from opex_calendar import (  # noqa: E402
    monthly_opex_dates,
    nearest_trading_day_on_or_before,
)

# Standard friction for all backtests (entry AND exit slipped consistently).
C.activate_slip(0.50)

BY_TICKER = ROOT / "data/orats/by_ticker"
UNIVERSE_PATH = ROOT / "data/profile/universe_v1.parquet"
RESULTS_OUT = ROOT / "data/profile/price_breach_stop_results.parquet"

ENTRY_DTE = 45
DTE_MANAGE = 21          # 21-DTE management cue
PROFIT_FRAC = 0.50       # 50%-of-credit managed profit target
BREACH_LEVELS = [0.02, 0.05, 0.07]

OPENERS = {"bull_put": open_bull_put, "bear_call": open_bear_call}


def _parse_exp(s) -> pd.Timestamp | None:
    ts = pd.to_datetime(s, errors="coerce")
    return ts if pd.notna(ts) else None


def _breached(structure: str, spot: float, short_k: float, level: float) -> bool:
    """True when `spot` is `level` beyond the short strike (adverse side)."""
    if structure == "bull_put":
        return spot <= short_k * (1.0 - level)
    return spot >= short_k * (1.0 + level)  # bear_call


def _realized_depth(structure: str, spot: float, short_k: float) -> float:
    """Signed adverse penetration of the short strike (>=0 means in-the-money side)."""
    if structure == "bull_put":
        return (short_k - spot) / short_k
    return (spot - short_k) / short_k  # bear_call


def simulate_cycle(structure, slice_by_day, available_days, entry_date,
                   expiration, ticker) -> dict | None:
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    pos = OPENERS[structure](entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None

    short_k = pos.notes["short_put_k"] if structure == "bull_put" else pos.notes["short_call_k"]
    credit = pos.entry_credit
    exp_date = expiration.date()

    forward_days = [d for d in available_days if d > entry_date and d <= exp_date]
    if not forward_days:
        return None

    # Single forward walk: record spot, MTM cost, DTE per day.
    day_cost: dict = {}
    day_spot: dict = {}
    t_50 = None
    t_21 = None
    t_breach = {lvl: None for lvl in BREACH_LEVELS}
    max_depth = -np.inf

    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        spot = float(chain_d["stkPx"].iloc[0])
        cost = close_cost(pos, chain_d)
        if cost is None:
            continue
        day_cost[d] = cost
        day_spot[d] = spot
        dte = (exp_date - d).days

        if t_50 is None and cost <= PROFIT_FRAC * credit:
            t_50 = d
        if t_21 is None and dte <= DTE_MANAGE:
            t_21 = d
        depth = _realized_depth(structure, spot, short_k)
        if depth > max_depth:
            max_depth = depth
        for lvl in BREACH_LEVELS:
            if t_breach[lvl] is None and _breached(structure, spot, short_k, lvl):
                t_breach[lvl] = d

    # Held-to-expiry P&L (intrinsic at the OpEx spot).
    exp_chain = slice_by_day.get(exp_date)
    if exp_chain is not None and not exp_chain.empty:
        s_exp = float(exp_chain["stkPx"].iloc[0])
    elif day_spot:
        s_exp = day_spot[max(day_spot)]
    else:
        return None
    held_pnl = credit + intrinsic_value_at_expiry(pos, s_exp)

    def pnl_on(day) -> float:
        return credit - day_cost[day]

    # Managed baseline: earliest of {50% profit, 21-DTE}, else held.
    managed_exits = [t for t in (t_50, t_21) if t is not None]
    if managed_exits:
        managed_day = min(managed_exits)
        managed_pnl = pnl_on(managed_day)
        managed_reason = "profit_50" if managed_day == t_50 else "dte_21"
    else:
        managed_day = None
        managed_pnl = held_pnl
        managed_reason = "held"

    row = {
        "ticker": ticker,
        "structure": structure,
        "entry_date": pd.Timestamp(entry_date),
        "expiration": expiration,
        "short_strike": float(short_k),
        "long_strike": float(pos.legs[1].strike),
        "wing": float(pos.notes["wing_width"]),
        "entry_credit": float(credit),
        "spot_entry": float(pos.underlying_entry),
        "spot_exit": float(s_exp),
        "max_adverse_depth": float(max_depth),
        "held_pnl": float(held_pnl),
        "managed_pnl": float(managed_pnl),
        "managed_reason": managed_reason,
    }

    for lvl in BREACH_LEVELS:
        tag = f"{int(lvl * 100)}"
        bday = t_breach[lvl]
        fired = bday is not None
        row[f"breach{tag}_fired"] = int(fired)
        if fired:
            row[f"breach{tag}_pnl"] = float(pnl_on(bday))  # close SAME day, slipped
            row[f"breach{tag}_date"] = pd.Timestamp(bday)
            # Does the stop BIND under the managed baseline? (breach strictly first)
            earlier_managed = [t for t in (t_50, t_21) if t is not None and t <= bday]
            row[f"breach{tag}_binds_managed"] = int(len(earlier_managed) == 0)
            # managed+stop exit P&L
            cand = [t for t in (t_50, t_21, bday) if t is not None]
            mday = min(cand)
            row[f"breach{tag}_managed_stop_pnl"] = float(pnl_on(mday))
        else:
            row[f"breach{tag}_pnl"] = np.nan
            row[f"breach{tag}_date"] = pd.NaT
            row[f"breach{tag}_binds_managed"] = 0
            row[f"breach{tag}_managed_stop_pnl"] = float(managed_pnl)
    return row


def simulate_ticker(ticker: str) -> list[dict]:
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

    exp_to_str: dict[pd.Timestamp, str] = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None and ts not in exp_to_str:
            exp_to_str[ts] = s

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]
    sorted_dates = sorted(tdf["date_only"].unique())

    rows: list[dict] = []
    for opex in opex_eligible:
        opex_ts = pd.Timestamp(opex)
        # Match the OpEx to an expiration string (tolerate +/-1 day).
        exp_str = exp_to_str.get(opex_ts)
        if exp_str is None:
            for ts, s in exp_to_str.items():
                if abs((ts - opex_ts).days) <= 1:
                    exp_str = s
                    opex_ts = ts
                    break
        if exp_str is None:
            continue

        target_entry = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target_entry, sorted_dates)
        if entry_date is None:
            continue

        cycle_df = tdf[tdf["expirDate"] == exp_str]
        if cycle_df.empty:
            continue
        slice_by_day = {d: g.sort_values("strike").reset_index(drop=True)
                        for d, g in cycle_df.groupby("date_only")}
        available_days = sorted(slice_by_day.keys())

        for structure in OPENERS:
            r = simulate_cycle(structure, slice_by_day, available_days,
                               entry_date, opex_ts, ticker)
            if r is not None:
                rows.append(r)
    return rows


def _fmt(x) -> str:
    return f"{x:+.4f}" if pd.notna(x) else "   n/a"


def summarize(df: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("PRICE-BREACH STOP STUDY — per-share P&L (x100 = per contract), slip=0.50")
    print("=" * 78)
    for structure in OPENERS:
        sdf = df[df["structure"] == structure]
        n = len(sdf)
        print(f"\n### {structure.upper()}  (N={n} cycles)")
        print(f"  baseline mean P&L:  held={_fmt(sdf['held_pnl'].mean())}   "
              f"managed={_fmt(sdf['managed_pnl'].mean())}")
        print(f"  {'depth':>6} | {'breached':>8} {'(% of N)':>8} | "
              f"{'A: close@breach':>15} {'vs hold':>9} {'Δ stop−hold':>12} | "
              f"{'B: mgd+stop':>11} {'vs mgd':>8} {'Δ (binds)':>10} {'bind N':>6}")
        print("  " + "-" * 96)
        for lvl in BREACH_LEVELS:
            tag = f"{int(lvl * 100)}"
            fired = sdf[sdf[f"breach{tag}_fired"] == 1]
            nb = len(fired)
            if nb == 0:
                print(f"  {tag+'%':>6} | {0:>8} {'0.0%':>8} |  no breaches")
                continue
            pct = 100.0 * nb / n if n else 0.0

            # Baseline A: close-at-breach vs hold-to-expiry, over breached cycles.
            stop_pnl = fired[f"breach{tag}_pnl"].mean()
            hold_pnl = fired["held_pnl"].mean()
            dA = stop_pnl - hold_pnl

            # Baseline B: managed+stop vs managed, over cycles where the stop binds.
            binds = fired[fired[f"breach{tag}_binds_managed"] == 1]
            nbind = len(binds)
            if nbind:
                mgd_stop = binds[f"breach{tag}_managed_stop_pnl"].mean()
                mgd_base = binds["managed_pnl"].mean()
                dB = mgd_stop - mgd_base
            else:
                mgd_stop = mgd_base = dB = np.nan

            print(f"  {tag+'%':>6} | {nb:>8} {pct:>7.1f}% | "
                  f"{_fmt(stop_pnl):>15} {_fmt(hold_pnl):>9} {_fmt(dA):>12} | "
                  f"{_fmt(mgd_stop):>11} {_fmt(mgd_base):>8} {_fmt(dB):>10} {nbind:>6}")

            # Recovery diagnostics: among breached cycles, how often does waiting win?
            recov = (fired["held_pnl"] > fired[f"breach{tag}_pnl"]).mean()
            print(f"  {'':>6} |   waiting (hold) beat the stop in "
                  f"{100*recov:.1f}% of breached cycles  "
                  f"(stop helps when this is LOW)")
    print("\n" + "=" * 78)
    print("Reading it: Δ stop−hold > 0  => the price stop BEATS holding to expiry.")
    print("            Δ (binds)  > 0  => adding the stop to the 50%/21-DTE managed")
    print("                               baseline helps, on cycles where it binds.")
    print("Negative Δ everywhere => recoveries dominate; do NOT add a price stop.")
    print("=" * 78)


def main() -> int:
    universe = pd.read_parquet(UNIVERSE_PATH)["ticker"].tolist()
    tickers = [t for t in universe if (BY_TICKER / f"{t}.parquet").exists()]
    print(f"Universe: {len(universe)} names, {len(tickers)} with ORATS by_ticker data.")

    all_rows: list[dict] = []
    for i, t in enumerate(tickers, 1):
        try:
            rows = simulate_ticker(t)
        except Exception as e:  # noqa: BLE001
            print(f"  {t} FAILED: {e}")
            rows = []
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(tickers):
            print(f"  [{i}/{len(tickers)}] {t:<6} cumulative rows: {len(all_rows)}")

    if not all_rows:
        print("Zero rows produced — aborting.")
        return 1

    df = pd.DataFrame(all_rows)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    print(f"\nWrote {len(df)} rows -> {RESULTS_OUT}")
    summarize(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
