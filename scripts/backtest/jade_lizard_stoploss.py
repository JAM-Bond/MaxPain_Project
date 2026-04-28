"""Jade Lizard with stop-loss on the naked short put — tests whether a daily
MTM-based stop would be enough to defang the bloodbath cycles.

Models overnight-gap risk: when the stop triggers (MTM loss exceeds threshold
at today's close), the short put is bought back at the NEXT trading day's
chain, simulating a stop-on-open execution that ALWAYS suffers any overnight
move. This is conservative — actual stop-limit orders sometimes execute
intraday, which would be cheaper.

After the stop fires, the bear-call spread continues to run to expiration.

Stop thresholds tested:
  - 2x credit (close put when its MTM loss alone >= 2x total entry credit)
  - 3x credit
  - spot down 5% from entry
  - short-put strike breached at close

Output: data/profile/jade_lizard_stoploss_results.parquet
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
from structures import (
    open_jade_lizard, close_cost_call, close_cost_put,
    intrinsic_value_at_expiry,
)

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
SKEW_UNIVERSE = ROOT / "data/profile/skew_universe.parquet"
OUT = ROOT / "data/profile/jade_lizard_stoploss_results.parquet"

ENTRY_DTE = 45
SLIP = 0.50  # use the harsher slip (matches main jade study)


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def short_put_mtm(pos, chain) -> float | None:
    """Close cost of the short-put leg at this chain (positive = $ to buy back)."""
    sp = pos.legs[0]  # leg 0 is the short put per open_jade_lizard
    row = chain[chain["strike"] == sp.strike]
    if row.empty:
        return None
    px = close_cost_put(row.iloc[0], "short")
    return float(px) if px is not None else None


def bear_call_close_cost(pos, chain) -> float | None:
    """Close cost of the 2-leg bear-call spread (legs 1 and 2)."""
    sc, lc = pos.legs[1], pos.legs[2]
    sc_row = chain[chain["strike"] == sc.strike]
    lc_row = chain[chain["strike"] == lc.strike]
    if sc_row.empty or lc_row.empty:
        return None
    sc_px = close_cost_call(sc_row.iloc[0], "short")
    lc_px = close_cost_call(lc_row.iloc[0], "long")
    if sc_px is None or lc_px is None:
        return None
    # Closing short call = pay sc_px; closing long call = receive lc_px
    return float(sc_px) - float(lc_px)


def simulate_cycle_with_stops(slice_by_day, available_days, entry_date,
                               expiration, ticker):
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    pos = open_jade_lizard(entry_chain, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None

    credit = pos.entry_credit
    spot_entry = pos.underlying_entry
    short_put_k = pos.notes["short_put_k"]

    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]

    # Track triggers per stop policy
    policies = {
        "no_stop":   {"fired": False, "trigger_day": None},
        "2x_credit": {"fired": False, "trigger_day": None, "thresh": 2.0 * credit},
        "3x_credit": {"fired": False, "trigger_day": None, "thresh": 3.0 * credit},
        "spot_5pct": {"fired": False, "trigger_day": None, "thresh": spot_entry * 0.95},
        "strike_brk":{"fired": False, "trigger_day": None, "thresh": short_put_k},
    }

    # Walk forward, looking for trigger days (stop applied on the close)
    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        sp_mtm = short_put_mtm(pos, chain_d)
        if sp_mtm is None:
            continue
        sp_loss = sp_mtm - pos.legs[0].price  # cost to close - credit received = MTM loss

        spot = float(chain_d["stkPx"].iloc[0])

        for name, p in policies.items():
            if p["fired"] or name == "no_stop":
                continue
            triggered = False
            if name in ("2x_credit", "3x_credit"):
                if sp_loss >= p["thresh"]:
                    triggered = True
            elif name == "spot_5pct":
                if spot <= p["thresh"]:
                    triggered = True
            elif name == "strike_brk":
                if spot <= p["thresh"]:
                    triggered = True
            if triggered:
                p["fired"] = True
                p["trigger_day"] = d

    # Compute final P&L per policy
    last_chain = slice_by_day.get(expiration.date())
    underlying_exp = float(last_chain["stkPx"].iloc[0]) if last_chain is not None and not last_chain.empty else None
    held_pnl = pos.entry_credit + intrinsic_value_at_expiry(pos, underlying_exp) if underlying_exp is not None else None

    out = {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "credit": float(credit),
        "spot_entry": float(spot_entry),
        "short_put_k": float(short_put_k),
        "underlying_at_exp": underlying_exp,
        "pnl_no_stop": float(held_pnl) if held_pnl is not None else np.nan,
    }

    for name, p in policies.items():
        if name == "no_stop":
            continue
        if not p["fired"]:
            out[f"pnl_{name}"] = float(held_pnl) if held_pnl is not None else np.nan
            out[f"{name}_fired"] = False
            out[f"{name}_trigger_day"] = None
            continue

        # Stop fired on trigger_day. Buy back the put on the NEXT trading day
        # (gap-risk model: we live with whatever the next session's chain says).
        trig = p["trigger_day"]
        next_days = [d for d in available_days if d > trig and d <= expiration.date()]
        if not next_days:
            # trigger on last trading day before expiry → settle at expiry
            sp_close_cost = max(0.0, short_put_k - underlying_exp) if underlying_exp is not None else None
            bc_close_cost = 0.0  # bear call also settles at expiry
            if sp_close_cost is None:
                out[f"pnl_{name}"] = np.nan
                out[f"{name}_fired"] = True
                out[f"{name}_trigger_day"] = pd.Timestamp(trig)
                continue
        else:
            next_day = next_days[0]
            next_chain = slice_by_day.get(next_day)
            if next_chain is None or next_chain.empty:
                out[f"pnl_{name}"] = np.nan
                out[f"{name}_fired"] = True
                out[f"{name}_trigger_day"] = pd.Timestamp(trig)
                continue
            sp_close_cost = short_put_mtm(pos, next_chain)
            if sp_close_cost is None:
                out[f"pnl_{name}"] = np.nan
                out[f"{name}_fired"] = True
                out[f"{name}_trigger_day"] = pd.Timestamp(trig)
                continue

        # Bear-call legs continue running to expiry. Compute their final value.
        if underlying_exp is not None:
            bc_intrinsic = (
                -max(0.0, underlying_exp - pos.legs[1].strike)   # short call
                + max(0.0, underlying_exp - pos.legs[2].strike)  # long call
            )
            bear_call_finalpx = pos.legs[1].price - pos.legs[2].price + bc_intrinsic
        else:
            bear_call_finalpx = None

        if bear_call_finalpx is None:
            out[f"pnl_{name}"] = np.nan
        else:
            # Decompose:
            # Entry: credit = sp_credit + sc_credit - lc_cost
            # Stopped pnl = sp_credit - sp_close_cost (closed early via stop)
            #             + sc_credit - sc_intrinsic_at_exp
            #             - lc_cost + lc_intrinsic_at_exp
            sp_credit = pos.legs[0].price
            sc_credit = pos.legs[1].price
            lc_cost = pos.legs[2].price
            sp_part = sp_credit - sp_close_cost
            sc_part = sc_credit - max(0.0, underlying_exp - pos.legs[1].strike)
            lc_part = -lc_cost + max(0.0, underlying_exp - pos.legs[2].strike)
            stopped_pnl = sp_part + sc_part + lc_part
            out[f"pnl_{name}"] = float(stopped_pnl)

        out[f"{name}_fired"] = True
        out[f"{name}_trigger_day"] = pd.Timestamp(trig)

    return out


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
    rows = []

    C.activate_slip(SLIP)
    for opex_ts, exp_str in opex_to_exp.items():
        exp_df = exp_groups[exp_str]
        slice_by_day = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}
        available_days = sorted(slice_by_day.keys())

        target = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target, available_days)
        if entry_date is None:
            continue
        result = simulate_cycle_with_stops(slice_by_day, available_days,
                                            entry_date, opex_ts, ticker)
        if result is not None:
            rows.append(result)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", nargs="+")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    skew = pd.read_parquet(SKEW_UNIVERSE)
    universe = skew["ticker"].tolist()
    if args.ticker:
        universe = [t for t in universe if t in args.ticker]
    if args.limit:
        universe = universe[:args.limit]

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("jade_stop")
    log.info("Stop-loss test on %d tickers (slip=%.2f, 45-DTE entry)",
             len(universe), SLIP)

    all_rows = []
    for i, t in enumerate(universe, 1):
        rows = simulate_ticker(t)
        all_rows.extend(rows)
        if i % 10 == 0 or i == len(universe):
            log.info("  [%d/%d] %s: %d rows", i, len(universe), t, len(all_rows))

    df = pd.DataFrame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    log.info("Wrote %d rows to %s", len(df), OUT)

    # Quick summary
    print("\n=== Stop-loss policy comparison (slip=0.50, held-to-expiration) ===")
    print(f"Total cycles: {len(df):,}")
    for col, label in [("pnl_no_stop", "no stop (baseline)"),
                       ("pnl_2x_credit", "stop @ 2x credit"),
                       ("pnl_3x_credit", "stop @ 3x credit"),
                       ("pnl_spot_5pct", "stop @ 5% spot drop"),
                       ("pnl_strike_brk", "stop @ short-put strike breached")]:
        s = df[col].dropna()
        if len(s) == 0: continue
        fire_col = col.replace("pnl_", "") + "_fired"
        if fire_col in df.columns and col != "pnl_no_stop":
            fire_rate = df[fire_col].mean()
        else:
            fire_rate = np.nan
        print(f"  {label:>34}: N={len(s):>5}  mean=${s.mean():>+8.4f}  "
              f"worst=${s.min():>+10.2f}  total=${s.sum():>+10.2f}  "
              f"fire_rate={fire_rate:.0%}" if pd.notna(fire_rate) else
              f"  {label:>34}: N={len(s):>5}  mean=${s.mean():>+8.4f}  "
              f"worst=${s.min():>+10.2f}  total=${s.sum():>+10.2f}")


if __name__ == "__main__":
    main()
