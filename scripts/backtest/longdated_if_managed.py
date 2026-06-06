"""Study A, Gates 2-5 — MANAGED / 50%-only exit pass (decisive for Gate 5).

Per LONGDATED_IF_VOLUME_SIGNAL_PREREG.md. Held-to-expiry (longdated_if_backtest.py)
is the lower bound and structurally understates long-vol because the payoff caps
at the wings — a big move mid-cycle spikes the MARK (time value + vega) but is
"wasted" if held to expiry. This pass marks each cycle forward and exits early:

  - managed:   take-profit at +50% of max payoff (= 0.5*(wing_width - debit));
               stop at -50% of debit; time-stop at 60 DTE remaining; else expiry.
  - 50%-only:  take-profit only; otherwise hold to expiry (the prior IF winner).

Exit slip applied (close long = sell at mid - slip*half-spread; close short =
buy at mid + slip*half-spread). Forward MTM is vectorized per (expiry, strike).

Reads ORATS by_ticker; writes data/profile/longdated_if_managed.parquet.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data" / "orats" / "by_ticker"
OUT = ROOT / "data" / "profile" / "longdated_if_managed.parquet"

IF_UNIVERSE = sorted(set([
    "SPX", "SPY", "QQQ", "GLD", "EFA", "WMT", "NEM", "XOM", "PG", "WFC", "GE",
    "INTC", "BABA", "TSLA", "AMD", "NVDA", "CAR", "AMZN", "GOOGL", "SCCO",
    "GOLD", "CLF", "ISRG", "XLK", "PEP", "STX", "LRCX", "MCD", "JNJ", "PDD",
    "AG", "DELL", "AFRM", "PLTR", "AVGO",
]))
MANIA = {"NVDA", "PLTR", "AMD", "AVGO", "TSLA"}

DTE_TARGETS = [45, 180, 290, 365]
DTE_TOL = 30
WING_PCTS = [0.05, 0.10, 0.15]
SLIP_FRAC = 0.25
SETTLE_MAX_YTE = 7 / 365.0
TIME_STOP_DTE = 60
TP_FRAC = 0.50      # take-profit at +50% of max payoff
STOP_FRAC = 0.50    # stop at -50% of debit

SPLITS = [("2021-01-01", "2023-12-31"), ("2022-01-01", "2024-12-31"),
          ("2023-01-01", "2025-12-31"), ("2024-01-01", "2026-04-30")]

COLS = ["trade_date", "expirDate", "yte", "strike", "stkPx",
        "cBidPx", "cAskPx", "pBidPx", "pAskPx"]


def _valid(b, a):
    return pd.notna(b) and pd.notna(a) and b > 0 and a >= b


def _buy(b, a):
    return (b + a) / 2 + SLIP_FRAC * (a - b) / 2


def _sell(b, a):
    return (b + a) / 2 - SLIP_FRAC * (a - b) / 2


def _monthly(dates):
    s = pd.to_datetime(pd.Series(sorted(pd.Series(dates).unique())))
    return list(s.groupby([s.dt.year, s.dt.month]).min())


def _pick_expiry(day_chain, rung):
    dte = (day_chain["yte"] * 365.0).round().astype(int)
    dc = day_chain.assign(_dte=dte)
    cand = dc[(dc["_dte"] - rung).abs() <= DTE_TOL]
    if cand.empty:
        return None, None, None
    bd = cand.iloc[(cand["_dte"] - rung).abs().argmin()]
    return dc[dc["expirDate"] == bd["expirDate"]], bd["expirDate"], int(bd["_dte"])


def _nearest(chain, target, above, ref):
    side = chain[chain["strike"] > ref] if above else chain[chain["strike"] < ref]
    if side.empty:
        return None
    return side.iloc[(side["strike"] - target).abs().argmin()]


def _series_for_strike(eg_indexed, strike):
    """Rows for one strike across the expiry's dates (index=trade_date)."""
    try:
        s = eg_indexed.xs(strike, level="strike")
    except KeyError:
        return None
    return s


def _process_entry(sub, expir, wp, eg_indexed, entry_date):
    sub = sub.dropna(subset=["strike", "stkPx"])
    if sub.empty:
        return None
    spot = float(sub["stkPx"].iloc[0])
    if spot <= 0:
        return None
    atm = sub.iloc[(sub["strike"] - spot).abs().argmin()]
    if not (_valid(atm["cBidPx"], atm["cAskPx"]) and _valid(atm["pBidPx"], atm["pAskPx"])):
        return None
    k = float(atm["strike"])
    wing = wp * spot
    wc = _nearest(sub, k + wing, True, k)
    wpr = _nearest(sub, k - wing, False, k)
    if wc is None or wpr is None:
        return None
    if not (_valid(wc["cBidPx"], wc["cAskPx"]) and _valid(wpr["pBidPx"], wpr["pAskPx"])):
        return None
    debit = (_buy(atm["cBidPx"], atm["cAskPx"]) + _buy(atm["pBidPx"], atm["pAskPx"])
             - _sell(wc["cBidPx"], wc["cAskPx"]) - _sell(wpr["pBidPx"], wpr["pAskPx"]))
    if debit <= 0:
        return None
    kc, kp = float(wc["strike"]), float(wpr["strike"])
    wing_width = max(kc - k, k - kp)
    max_payoff = wing_width - debit
    target = TP_FRAC * max_payoff if max_payoff > 0 else 0.5 * debit
    stop = -STOP_FRAC * debit

    # forward marks: rows for the 3 strikes after entry_date
    atm_s = _series_for_strike(eg_indexed, k)
    wc_s = _series_for_strike(eg_indexed, kc)
    wp_s = _series_for_strike(eg_indexed, kp)
    if atm_s is None or wc_s is None or wp_s is None:
        return None
    fwd_dates = atm_s.index[atm_s.index > entry_date]
    # held-to-expiry settlement (fallback) — same as backtest
    se = atm_s  # use atm strike's last snapshot for settle spot/yte
    last = se.iloc[se["yte"].values.argmin()]
    held_pnl = np.nan
    if last["yte"] <= SETTLE_MAX_YTE:
        S = float(last["stkPx"])
        val = (max(S - k, 0) + max(k - S, 0) - max(S - kc, 0) - max(kp - S, 0))
        held_pnl = val - debit

    managed_pnl = held_pnl
    managed_reason = "expiry"
    only50_pnl = held_pnl
    only50_reason = "expiry"
    days_held = np.nan

    if len(fwd_dates):
        a = atm_s.loc[fwd_dates]
        c = wc_s.reindex(fwd_dates)
        p = wp_s.reindex(fwd_dates)
        ok = (a[["cBidPx", "cAskPx", "pBidPx", "pAskPx"]].notna().all(axis=1)
              & c[["cBidPx", "cAskPx"]].notna().all(axis=1)
              & p[["pBidPx", "pAskPx"]].notna().all(axis=1))
        a, c, p = a[ok], c[ok], p[ok]
        if len(a):
            # close longs = SELL (mid - slip); close shorts = BUY (mid + slip)
            sell_c = (a["cBidPx"] + a["cAskPx"]) / 2 - SLIP_FRAC * (a["cAskPx"] - a["cBidPx"]) / 2
            sell_p = (a["pBidPx"] + a["pAskPx"]) / 2 - SLIP_FRAC * (a["pAskPx"] - a["pBidPx"]) / 2
            buy_c = (c["cBidPx"] + c["cAskPx"]) / 2 + SLIP_FRAC * (c["cAskPx"] - c["cBidPx"]) / 2
            buy_p = (p["pBidPx"] + p["pAskPx"]) / 2 + SLIP_FRAC * (p["pAskPx"] - p["pBidPx"]) / 2
            val = sell_c + sell_p - buy_c - buy_p
            mtm = val - debit
            dte_rem = a["yte"] * 365.0
            idx = a.index

            tp_hits = np.where(mtm.values >= target)[0]
            # 50%-only: first take-profit, else expiry
            if len(tp_hits):
                j = tp_hits[0]
                only50_pnl = float(mtm.values[j]); only50_reason = "take_profit"
            # managed: first of TP / stop / time-stop
            trig = np.where((mtm.values >= target) | (mtm.values <= stop)
                            | (dte_rem.values <= TIME_STOP_DTE))[0]
            if len(trig):
                j = trig[0]
                managed_pnl = float(mtm.values[j])
                days_held = (idx[j] - entry_date).days
                if mtm.values[j] >= target:
                    managed_reason = "take_profit"
                elif mtm.values[j] <= stop:
                    managed_reason = "stop"
                else:
                    managed_reason = "time_stop"

    return {"spot": spot, "debit": debit, "max_payoff": max_payoff,
            "held_pnl": held_pnl, "held_ppd": held_pnl / debit if pd.notna(held_pnl) else np.nan,
            "managed_pnl": managed_pnl, "managed_ppd": managed_pnl / debit if pd.notna(managed_pnl) else np.nan,
            "managed_reason": managed_reason,
            "only50_pnl": only50_pnl, "only50_ppd": only50_pnl / debit if pd.notna(only50_pnl) else np.nan,
            "days_held": days_held}


def run():
    avail = {p.stem for p in BY_TICKER.glob("*.parquet")}
    universe = [s for s in IF_UNIVERSE if s in avail]
    print(f"  universe {len(universe)}; DTEs {DTE_TARGETS}; wings {[int(w*100) for w in WING_PCTS]}%; "
          f"managed TP=+50% maxpayoff / stop=-50% debit / time-stop 60DTE\n", flush=True)
    rows = []
    for i, sym in enumerate(universe, 1):
        df = pd.read_parquet(BY_TICKER / f"{sym}.parquet", columns=COLS)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        by_day = {d: g for d, g in df.groupby("trade_date")}
        expir_cache = {}

        def _get_indexed(expir):
            if expir not in expir_cache:
                eg = df[df["expirDate"] == expir].set_index(["trade_date", "strike"]).sort_index()
                expir_cache[expir] = eg
            return expir_cache[expir]

        n = 0
        for d in _monthly(df["trade_date"]):
            day_chain = by_day.get(d)
            if day_chain is None:
                continue
            for rung in DTE_TARGETS:
                sub, expir, real_dte = _pick_expiry(day_chain, rung)
                if sub is None:
                    continue
                eg_indexed = _get_indexed(expir)
                for wp in WING_PCTS:
                    r = _process_entry(sub, expir, wp, eg_indexed, d)
                    if r is None:
                        continue
                    r.update(ticker=sym, entry_date=d, dte_target=rung,
                             real_dte=real_dte, wing_pct=wp, mania=sym in MANIA)
                    rows.append(r)
                    n += 1
        print(f"  [{i:2d}/{len(universe)}] {sym:5s} cycles={n}", flush=True)
    res = pd.DataFrame(rows)
    res.to_parquet(OUT, index=False)
    print(f"\n  wrote {len(res):,} cycles -> {OUT}\n", flush=True)
    _report(res)


def _wf_pos(sub, col):
    n = 0
    for a, b in SPLITS:
        m = sub[(sub["entry_date"] >= a) & (sub["entry_date"] <= b)]
        if len(m) >= 30 and m[col].mean() > 0:
            n += 1
    return n


def _report(res):
    print("=" * 92, flush=True)
    print("  MANAGED-EXIT STUDY — mean P/L per $ debit (return on capital at risk)", flush=True)
    print("  cols: held = lower bound | 50only = take-profit-or-hold | mgd = full managed", flush=True)
    print("=" * 92, flush=True)
    for wp in WING_PCTS:
        print(f"\n  Wing = {int(wp*100)}%", flush=True)
        print(f"  {'DTE':>5} {'N':>6} {'held':>8} {'50only':>8} {'mgd':>8} "
              f"{'mgd win%':>8} {'mgd WF>0':>9} {'med days':>9}", flush=True)
        sw = res[res["wing_pct"] == wp]
        for dte in DTE_TARGETS:
            s = sw[sw["dte_target"] == dte]
            if s.empty:
                continue
            print(f"  {dte:>5} {len(s):>6} {s['held_ppd'].mean():>+8.3f} "
                  f"{s['only50_ppd'].mean():>+8.3f} {s['managed_ppd'].mean():>+8.3f} "
                  f"{(s['managed_pnl']>0).mean()*100:>7.0f}% {_wf_pos(s,'managed_ppd')}>0/4 "
                  f"{s['days_held'].median():>9.0f}", flush=True)
    print("\n  " + "-" * 88, flush=True)
    print("  GATE 5 (decisive): 290 vs 45 DTE on MANAGED mean P/L-per-debit:", flush=True)
    for wp in WING_PCTS:
        sw = res[res["wing_pct"] == wp]
        a = sw[sw["dte_target"] == 45]["managed_ppd"].mean()
        b = sw[sw["dte_target"] == 290]["managed_ppd"].mean()
        print(f"    wing {int(wp*100):>2}%:  45 {a:+.3f}  vs  290 {b:+.3f}  -> "
              f"{'290 BEATS 45' if b > a else '45 beats 290'}", flush=True)
    print("\n  Mania sub-cohort @10% wing (managed):", flush=True)
    m = res[(res["mania"]) & (res["wing_pct"] == 0.10)]
    for dte in DTE_TARGETS:
        s = m[m["dte_target"] == dte]
        if s.empty:
            continue
        print(f"    {dte:>3} DTE: N={len(s):>4} held={s['held_ppd'].mean():+.3f} "
              f"mgd={s['managed_ppd'].mean():+.3f} win%={(s['managed_pnl']>0).mean()*100:>3.0f} "
              f"med_days={s['days_held'].median():.0f}", flush=True)
    # exit-reason mix at 290/10%
    print("\n  Exit-reason mix (290 DTE, 10% wing):", flush=True)
    s = res[(res["dte_target"] == 290) & (res["wing_pct"] == 0.10)]
    if len(s):
        print("   ", s["managed_reason"].value_counts(normalize=True).round(2).to_dict(), flush=True)
    print("=" * 92, flush=True)


if __name__ == "__main__":
    run()
