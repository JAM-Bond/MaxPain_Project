"""Study A, Gates 2-5 — Long-dated inverted-fly structure study.

Per LONGDATED_IF_VOLUME_SIGNAL_PREREG.md (sealed 2026-06-03; Gate A1 passed,
DTE*=365). Runs the inverted fly (long ATM call+put, short OTM wings; net debit)
across DTE targets [45, 180, 290, 365] and wing widths [5,10,15]%, monthly
entries, ENTRY slip applied. This pass = HELD-TO-EXPIRY settlement only — the
LOWER BOUND per feedback_backtest_held_to_expiry_lower_bound. Managed / 50%-only
MTM exits are a separate pass; NO Gate 2-5 verdict is taken on held-to-expiry
alone (it can only under-state a long-vol structure).

Key metric for Gate 5: P/L per dollar of debit = (expiry_value - debit)/debit
(unit-free return on capital at risk), so 290 DTE vs 45 DTE is apples-to-apples.

Reads ORATS by_ticker; writes data/profile/longdated_if_results.parquet.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data" / "orats" / "by_ticker"
OUT = ROOT / "data" / "profile" / "longdated_if_results.parquet"

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
SLIP_FRAC = 0.25                 # primary (entry only; held-to-expiry settles at intrinsic)
SETTLE_MAX_YTE = 7 / 365.0       # require a snapshot within ~1wk of expiry to settle

# 4-split walk-forward (matches house convention)
SPLITS = [("2021-01-01", "2023-12-31"), ("2022-01-01", "2024-12-31"),
          ("2023-01-01", "2025-12-31"), ("2024-01-01", "2026-04-30")]

COLS = ["trade_date", "expirDate", "yte", "strike", "stkPx",
        "cBidPx", "cAskPx", "pBidPx", "pAskPx"]


def _valid(b, a):
    return pd.notna(b) and pd.notna(a) and b > 0 and a >= b


def _buy(b, a):   # long fill = mid + frac*(spread/2)
    return (b + a) / 2 + SLIP_FRAC * (a - b) / 2


def _sell(b, a):  # short fill = mid - frac*(spread/2)
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


def _open_and_settle(sub, expir, wing_pct, settle_df):
    """Build IF at entry (slip), settle at intrinsic at expiry. Return dict|None."""
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
    wing = wing_pct * spot
    wc = _nearest(sub, k + wing, True, k)
    wp = _nearest(sub, k - wing, False, k)
    if wc is None or wp is None:
        return None
    if not (_valid(wc["cBidPx"], wc["cAskPx"]) and _valid(wp["pBidPx"], wp["pAskPx"])):
        return None
    debit = (_buy(atm["cBidPx"], atm["cAskPx"]) + _buy(atm["pBidPx"], atm["pAskPx"])
             - _sell(wc["cBidPx"], wc["cAskPx"]) - _sell(wp["pBidPx"], wp["pAskPx"]))
    if debit <= 0:
        return None
    # settlement underlying: snapshot of this expiry nearest expiry
    se = settle_df[settle_df["expirDate"] == expir]
    if se.empty:
        return None
    srow = se.iloc[se["yte"].argmin()]
    if srow["yte"] > SETTLE_MAX_YTE:
        return None  # never reached expiry within data window → unsettled, drop
    S = float(srow["stkPx"])
    kc, kp = float(wc["strike"]), float(wp["strike"])
    value = (max(S - k, 0) + max(k - S, 0)            # long ATM call + put
             - max(S - kc, 0) - max(kp - S, 0))       # short wing call + put
    pnl = value - debit
    return {"spot": spot, "atm_k": k, "wing_call_k": kc, "wing_put_k": kp,
            "debit": debit, "settle_spot": S, "expiry_value": value,
            "pnl": pnl, "pnl_per_debit": pnl / debit,
            "abs_move_pct": abs(S / spot - 1.0)}


def run():
    avail = {p.stem for p in BY_TICKER.glob("*.parquet")}
    universe = [s for s in IF_UNIVERSE if s in avail]
    print(f"  universe: {len(universe)} names; DTEs={DTE_TARGETS}; wings={[int(w*100) for w in WING_PCTS]}%; slip={SLIP_FRAC}\n")
    rows = []
    for i, sym in enumerate(universe, 1):
        df = pd.read_parquet(BY_TICKER / f"{sym}.parquet", columns=COLS)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        by_day = {d: g for d, g in df.groupby("trade_date")}
        # settle_df: for each expirDate, we need rows near expiry → use full df
        settle_df = df[["expirDate", "yte", "stkPx"]]
        n = 0
        for d in _monthly(df["trade_date"]):
            day_chain = by_day.get(d)
            if day_chain is None:
                continue
            for rung in DTE_TARGETS:
                sub, expir, real_dte = _pick_expiry(day_chain, rung)
                if sub is None:
                    continue
                for wp in WING_PCTS:
                    r = _open_and_settle(sub, expir, wp, settle_df)
                    if r is None:
                        continue
                    r.update(ticker=sym, entry_date=d, dte_target=rung,
                             real_dte=real_dte, wing_pct=wp,
                             mania=sym in MANIA)
                    rows.append(r)
                    n += 1
        print(f"  [{i:2d}/{len(universe)}] {sym:5s} settled_cycles={n}")
    res = pd.DataFrame(rows)
    res.to_parquet(OUT, index=False)
    print(f"\n  wrote {len(res):,} settled cycles -> {OUT}\n")
    _report(res)


def _wf(sub):
    """Per-split mean pnl_per_debit; returns list, and #splits>0 with N>=30."""
    out = []
    for a, b in SPLITS:
        m = sub[(sub["entry_date"] >= a) & (sub["entry_date"] <= b)]
        out.append((len(m), m["pnl_per_debit"].mean() if len(m) else np.nan))
    return out


def _report(res):
    print("=" * 84)
    print("  STRUCTURE STUDY — HELD-TO-EXPIRY (LOWER BOUND; managed/50%-only exits pending)")
    print("  metric = mean P/L per $ of debit (return on capital at risk)")
    print("=" * 84)
    for wp in WING_PCTS:
        print(f"\n  Wing = {int(wp*100)}% of spot")
        print(f"  {'DTE':>5} {'N':>6} {'mean$/debit':>12} {'win%':>6} {'medDebit':>9} {'WF splits>0':>12}")
        sw = res[res["wing_pct"] == wp]
        for dte in DTE_TARGETS:
            s = sw[sw["dte_target"] == dte]
            if s.empty:
                continue
            wf = _wf(s)
            n_pos = sum(1 for nn, mm in wf if nn >= 30 and mm > 0)
            n_ok = sum(1 for nn, _ in wf if nn >= 30)
            mean_ppd = s["pnl_per_debit"].mean()
            win = (s["pnl"] > 0).mean()
            print(f"  {dte:>5} {len(s):>6} {mean_ppd:>+12.3f} {win*100:>5.0f}% "
                  f"{s['debit'].median():>9.2f} {n_pos:>6}/{n_ok:<2} (≥30N)")
    # Gate 5 framing: 290 vs 45 on mean $/debit, at 10% wing
    print("\n  " + "-" * 80)
    print("  Gate-5 read (290 vs 45 DTE, mean P/L per $ debit) — held-to-expiry lower bound:")
    for wp in WING_PCTS:
        sw = res[res["wing_pct"] == wp]
        a = sw[sw["dte_target"] == 45]["pnl_per_debit"].mean()
        b = sw[sw["dte_target"] == 290]["pnl_per_debit"].mean()
        verdict = "290 BEATS 45" if b > a else "45 beats 290"
        print(f"    wing {int(wp*100):>2}%:  45DTE {a:+.3f}   vs   290DTE {b:+.3f}   -> {verdict}")
    print("\n  Mania sub-cohort (NVDA/PLTR/AMD/AVGO/TSLA) @ 10% wing, held-to-expiry:")
    m = res[(res["mania"]) & (res["wing_pct"] == 0.10)]
    for dte in DTE_TARGETS:
        s = m[m["dte_target"] == dte]
        if s.empty:
            continue
        print(f"    {dte:>3} DTE: N={len(s):>4}  mean$/debit={s['pnl_per_debit'].mean():+.3f}  "
              f"win%={ (s['pnl']>0).mean()*100:>3.0f}  mean|move|={s['abs_move_pct'].mean()*100:.1f}%")
    print("=" * 84)
    print("  NOTE: held-to-expiry is the LOWER BOUND for a long-vol structure. A negative")
    print("  here does NOT reject — managed/50%-only exits capture mid-cycle vol spikes the")
    print("  hold-to-expiry path gives back. Gates 2-5 verdict waits on the managed pass.")
    print("=" * 84)


if __name__ == "__main__":
    run()
