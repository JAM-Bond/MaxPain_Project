#!/usr/bin/env python3.11
"""Per-ticker breach-recovery / stop-loss study — CURRENT cohort.

Answers, per (ticker, structure), three questions:
  1. Does the name MEAN-REVERT after a short-strike breach, or keep going?
  2. If it does NOT revert, what breach depth is its "point of no return" — the
     stop where closing finally BEATS holding (and stays better deeper)?
  3. If it DOES revert, how many trading days until recovery (spot back across
     the short strike)?

Method (per cycle, 45-DTE managed window):
  - open the vertical; walk each trading day.
  - track signed penetration of the short strike (>0 = breached).
  - FIRST-CROSSING of each grid depth → price the cut SAME DAY (slipped close).
  - breach/recovery timing: first day spot crosses the short strike, first day it
    crosses back (un-breach) → days_to_recovery.
  - baselines: held-to-expiry and managed (first of 50% profit / 21-DTE).

Stop value at depth d = mean(cut@d − held) over cycles that crossed d. d* = the
smallest depth where this is reliably positive AND stays positive deeper. A name
with no such d* (cut never beats hold) is a MEAN-REVERTER → leave alone.

slip=0.50, ORATS EOD, current cohort from gate_config + the live open book.
Per-cycle parquet + a per-(ticker,structure) profile parquet + printed summary.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts/backtest"))

import config as C  # noqa: E402
from structures import open_bull_put, open_bear_call, close_cost, intrinsic_value_at_expiry  # noqa: E402
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before  # noqa: E402
import scripts.qualifier.gate_config as G  # noqa: E402

C.activate_slip(0.50)
BY = ROOT / "data/orats/by_ticker"
PER_CYCLE = ROOT / "data/profile/per_ticker_stop_cycles.parquet"
PROFILE = ROOT / "data/profile/per_ticker_stop_profile.parquet"

ENTRY_DTE = 45
DTE_MANAGE = 21
PROFIT_FRAC = 0.50
DEPTHS = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.15, 0.20]
OPENERS = {"bull_put": open_bull_put, "bear_call": open_bear_call}
TRAIN_END = pd.Timestamp("2019-12-31")

OPEN_NAMES = {"CSCO", "QQQ", "XLE", "ROST", "BURL", "TMUS", "COP", "RIOT", "STZ", "VST"}
COHORT = sorted((set(G.COHORT_BULL_PUT) | set(G.COHORT_BEAR_CALL) | OPEN_NAMES))


def _depth(structure, spot, short_k):
    return (short_k - spot) / short_k if structure == "bull_put" else (spot - short_k) / short_k


def simulate_cycle(structure, slice_by_day, days, entry_date, expiration, ticker):
    ec = slice_by_day.get(entry_date)
    if ec is None or ec.empty:
        return None
    pos = OPENERS[structure](ec, pd.Timestamp(entry_date), expiration)
    if pos is None:
        return None
    short_k = pos.notes["short_put_k"] if structure == "bull_put" else pos.notes["short_call_k"]
    credit = pos.entry_credit
    exp_date = expiration.date()
    fwd = [d for d in days if d > entry_date and d <= exp_date]
    if not fwd:
        return None

    cut = {d: np.nan for d in DEPTHS}
    crossed = {d: False for d in DEPTHS}
    t_50 = t_21 = None
    breach_idx = recover_idx = None
    last_cost = None
    s_exp = None
    step = 0
    for d in fwd:
        ch = slice_by_day.get(d)
        if ch is None or ch.empty:
            continue
        spot = float(ch["stkPx"].iloc[0])
        cost = close_cost(pos, ch)
        if cost is None:
            continue
        last_cost = cost
        s_exp = spot
        dte = (exp_date - d).days
        if t_50 is None and cost <= PROFIT_FRAC * credit:
            t_50 = d
        if t_21 is None and dte <= DTE_MANAGE:
            t_21 = d
        dep = _depth(structure, spot, short_k)
        # breach / recovery timing (crossing depth 0)
        if breach_idx is None and dep >= 0:
            breach_idx = step
        elif breach_idx is not None and recover_idx is None and dep < 0:
            recover_idx = step
        # first-crossing cut pricing per grid depth
        for gd in DEPTHS:
            if not crossed[gd] and dep >= gd:
                crossed[gd] = True
                cut[gd] = credit - cost   # close SAME day, slipped
        step += 1

    # held-to-expiry
    exp_ch = slice_by_day.get(exp_date)
    if exp_ch is not None and not exp_ch.empty:
        s_exp = float(exp_ch["stkPx"].iloc[0])
    if s_exp is None:
        return None
    held = credit + intrinsic_value_at_expiry(pos, s_exp)
    managed = (credit - last_cost) if (t_50 or t_21) and last_cost is not None else held

    row = {"ticker": ticker, "structure": structure, "entry_date": pd.Timestamp(entry_date),
           "entry_credit": float(credit), "held_pnl": float(held), "managed_pnl": float(managed),
           "breached": int(breach_idx is not None),
           "recovered": int(breach_idx is not None and recover_idx is not None),
           "days_to_recovery": (recover_idx - breach_idx) if (breach_idx is not None and recover_idx is not None) else np.nan}
    for gd in DEPTHS:
        tag = f"{int(gd*100)}"
        row[f"crossed_{tag}"] = int(crossed[gd])
        row[f"cut_{tag}"] = cut[gd]
    return row


def simulate_ticker(ticker):
    p = BY / f"{ticker}.parquet"
    if not p.exists():
        return []
    tdf = pd.read_parquet(p)
    if tdf.empty:
        return []
    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"])
    tdf["date_only"] = tdf["trade_date"].dt.date
    first, last = tdf["trade_date"].min().date(), tdf["trade_date"].max().date()
    exp_to_str = {}
    for s in tdf["expirDate"].unique():
        ts = pd.to_datetime(s, errors="coerce")
        if pd.notna(ts) and ts not in exp_to_str:
            exp_to_str[ts] = s
    opex = [d for d in monthly_opex_dates(first.year, last.year + 1) if first <= d <= last]
    sorted_dates = sorted(tdf["date_only"].unique())
    rows = []
    for od in opex:
        ots = pd.Timestamp(od)
        es = exp_to_str.get(ots)
        if es is None:
            for ts, s in exp_to_str.items():
                if abs((ts - ots).days) <= 1:
                    es, ots = s, ts
                    break
        if es is None:
            continue
        entry = nearest_trading_day_on_or_before((ots - pd.Timedelta(days=ENTRY_DTE)).date(), sorted_dates)
        if entry is None:
            continue
        cyc = tdf[tdf["expirDate"] == es]
        if cyc.empty:
            continue
        sbd = {d: g.sort_values("strike").reset_index(drop=True) for d, g in cyc.groupby("date_only")}
        days = sorted(sbd.keys())
        for st in OPENERS:
            r = simulate_cycle(st, sbd, days, entry, ots, ticker)
            if r:
                rows.append(r)
    return rows


def build_profile(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (tk, st), g in df.groupby(["ticker", "structure"]):
        n = len(g)
        nb = int(g.breached.sum())
        rec = g[g.breached == 1]
        rec_rate = rec.recovered.mean() if len(rec) else np.nan
        med_days = rec.loc[rec.recovered == 1, "days_to_recovery"].median() if (rec.recovered == 1).any() else np.nan
        # stop value per depth (full + walk-forward)
        sv, svn = {}, {}
        for gd in DEPTHS:
            tag = f"{int(gd*100)}"
            f = g[g[f"crossed_{tag}"] == 1].dropna(subset=[f"cut_{tag}", "held_pnl"])
            svn[gd] = len(f)
            sv[gd] = (f[f"cut_{tag}"].mean() - f.held_pnl.mean()) if len(f) else np.nan
        # d* = smallest depth where stop beats hold by >0.05 with N>=8 and not negative deeper
        dstar = None
        for i, gd in enumerate(DEPTHS):
            if svn[gd] >= 8 and pd.notna(sv[gd]) and sv[gd] > 0.05:
                deeper = [sv[d2] for d2 in DEPTHS[i:] if svn[d2] >= 5 and pd.notna(sv[d2])]
                if deeper and min(deeper) > -0.05:   # stays non-negative deeper
                    dstar = gd
                    break
        # walk-forward sign check at d* (or at 7% as default probe)
        probe = dstar if dstar else 0.07
        ptag = f"{int(probe*100)}"
        def sval(sub):
            f = sub[sub[f"crossed_{ptag}"] == 1].dropna(subset=[f"cut_{ptag}", "held_pnl"])
            return (f[f"cut_{ptag}"].mean() - f.held_pnl.mean()) if len(f) >= 5 else np.nan
        sv_tr, sv_te = sval(g[g.entry_date <= TRAIN_END]), sval(g[g.entry_date > TRAIN_END])
        wf_stable = (pd.notna(sv_tr) and pd.notna(sv_te) and np.sign(sv_tr) == np.sign(sv_te))

        if nb < 12:
            cls = "INSUFFICIENT"
        elif dstar is not None:
            cls = "NON_REVERT"     # stop helps
        else:
            cls = "MEAN_REVERT"    # hold wins at every depth
        out.append({
            "ticker": tk, "structure": st, "n_cycles": n, "n_breached": nb,
            "classification": cls, "stop_depth": dstar,
            "recovery_rate": round(rec_rate, 3) if pd.notna(rec_rate) else np.nan,
            "median_recovery_days": med_days,
            "stop_value_at_dstar_or_7": round(sv.get(dstar, sv[0.07]), 3) if pd.notna(sv.get(dstar, sv[0.07])) else np.nan,
            "wf_train": round(sv_tr, 3) if pd.notna(sv_tr) else np.nan,
            "wf_test": round(sv_te, 3) if pd.notna(sv_te) else np.nan,
            "wf_stable": wf_stable,
        })
    return pd.DataFrame(out)


def main():
    print(f"Cohort: {len(COHORT)} names × {len(OPENERS)} structures. slip=0.50, depths={DEPTHS}")
    all_rows = []
    for i, tk in enumerate(COHORT, 1):
        try:
            all_rows.extend(simulate_ticker(tk))
        except Exception as e:
            print(f"  {tk} FAILED: {e}")
        if i % 10 == 0 or i == len(COHORT):
            print(f"  [{i}/{len(COHORT)}] {tk:<6} cumulative cycles: {len(all_rows)}")
    if not all_rows:
        print("zero rows"); return 1
    df = pd.DataFrame(all_rows)
    df.to_parquet(PER_CYCLE, index=False)
    prof = build_profile(df)
    prof.to_parquet(PROFILE, index=False)
    print(f"\nWrote {len(df)} cycles → {PER_CYCLE}\nWrote {len(prof)} profiles → {PROFILE}")

    # summary
    for st in OPENERS:
        p = prof[prof.structure == st]
        print(f"\n=== {st.upper()} ===  ({len(p)} names)")
        print(f"  MEAN_REVERT: {(p.classification=='MEAN_REVERT').sum()}  "
              f"NON_REVERT: {(p.classification=='NON_REVERT').sum()}  "
              f"INSUFFICIENT: {(p.classification=='INSUFFICIENT').sum()}")
        nr = p[p.classification == "NON_REVERT"].sort_values("stop_depth")
        if len(nr):
            print("  -- NON-REVERTERS (stop helps) — stop depth, value, WF --")
            for _, r in nr.iterrows():
                print(f"    {r.ticker:<6} stop@{r.stop_depth*100:.0f}%  val={r.stop_value_at_dstar_or_7:+.2f}  "
                      f"recov_rate={r.recovery_rate}  WF(tr/te)={r.wf_train}/{r.wf_test} {'✓' if r.wf_stable else '⚠'}")
        mr = p[p.classification == "MEAN_REVERT"].sort_values("median_recovery_days")
        if len(mr):
            print("  -- MEAN-REVERTERS (hold) — recovery rate, median days to recover --")
            for _, r in mr.iterrows():
                md = f"{r.median_recovery_days:.0f}d" if pd.notna(r.median_recovery_days) else "n/a"
                print(f"    {r.ticker:<6} recov_rate={r.recovery_rate}  median_recover={md}  (n_breach={r.n_breached})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
