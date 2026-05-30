#!/usr/bin/env python3.11
"""Bear-call LIVE-STATE filter — sealed validation.

Implements docs/BEARCALL_LIVE_STATE_FILTER_PREREG.md (SEALED 2026-05-30).

Question: the auto-promotion pipeline selects bear-call names by walk-forward
expectancy, but that's a look-back fossil (UNH: big backtest mean, yet recovered
+62%). Does a CURRENT-STATE filter — below a FALLING 200-DMA + persistent
relative weakness (126d) + IV-rank >= 50th pctile — applied to Gate-B-eligible
cycles, lift forward P/L, beat a placebo, hold walk-forward, and is the RECOVERED
state negative? And does the IV-rank ingredient carry its own weight?

All on split-clean adjusted close. Substrate = OTM managed-50% cycles. P/L is
substrate-native (gross); Gate B (absolute>0) is the slip-sensitive one and is
flagged accordingly — the relative gates (A/C/E/F) are slip-invariant.
"""
from __future__ import annotations

import sys, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.adjusted_close import load_adjusted_close  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"
SUB = ROOT / "data/profile/bear_call_moneyness_results.parquet"
# Sealed thresholds
IV_PCTL, GATE_A, GATE_F = 50.0, 0.05, 0.03
GATEB_MEAN, GATEB_N = 0.05, 12          # Gate-B-as-of-entry: prior mean>$0.05/sh, n>=12
PERSIST = 126
WINDOWS = [(2021, 2023), (2022, 2024), (2023, 2025), (2024, 2026)]


def gate_b_eligible(o: pd.DataFrame) -> pd.Series:
    """Per-cycle out-of-sample eligibility: name's PRIOR fully-resolved OTM cycles
    (expiration < this entry) number >=12, mean mgd50 > $0.05/sh, and the most
    recent 12 priors are also positive (the 'recent split positive' condition)."""
    elig = np.zeros(len(o), dtype=bool)
    for tkr, g in o.groupby("ticker"):
        g = g.sort_values("entry_date")
        ent = g["entry_date"].values
        exp = g["expiration"].values
        pnl = g["mgd50_pnl"].values
        idx = g.index.values
        for k in range(len(g)):
            prior = exp < ent[k]                     # fully resolved before entry
            if prior.sum() < GATEB_N:
                continue
            pp = pnl[prior]
            if pp.mean() > GATEB_MEAN and pp[-12:].mean() > 0:
                elig[np.searchsorted(o.index.values, idx[k])] = True
    return pd.Series(elig, index=o.index)


def iv_series(tkr: str) -> pd.Series | None:
    try:
        raw = pd.read_parquet(ROOT / f"data/orats/by_ticker/{tkr}.parquet",
                              columns=["trade_date", "expirDate", "delta", "cMidIv"])
    except Exception:
        return None
    raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    raw["exp"] = pd.to_datetime(raw["expirDate"], format="%m/%d/%Y", errors="coerce")
    raw["dte"] = (raw["exp"] - raw["trade_date"]).dt.days
    f = raw[(raw["dte"] >= 25) & (raw["dte"] <= 35)].copy()
    if len(f) < 60:
        return None
    f["ad"] = (f["delta"] - 0.5).abs()
    return f.sort_values(["trade_date", "ad"]).drop_duplicates("trade_date").set_index("trade_date")["cMidIv"].sort_index()


def tag_states(elig: pd.DataFrame, spy: pd.Series) -> pd.DataFrame:
    """Tag each eligible cycle with below_falling_ma, weak126, ivrank>=50, recovered."""
    rows = []
    for tkr, g in elig.groupby("ticker"):
        try:
            px = load_adjusted_close(tkr).dropna().sort_index()
        except Exception:
            continue
        if len(px) < 260:
            continue
        ma = px.rolling(200).mean(); slope = ma / ma.shift(21) - 1
        rr = px / spy.reindex(px.index).ffill(); rel = rr / rr.shift(PERSIST) - 1
        iv = iv_series(tkr)
        ivr = None
        if iv is not None:
            ivr = iv.rolling(252, min_periods=120).apply(lambda w: (w <= w[-1]).mean() * 100, raw=True)
        for _, c in g.iterrows():
            d = pd.Timestamp(c["entry_date"])
            pe = px.index[px.index <= d]
            if len(pe) == 0:
                continue
            t = pe[-1]
            below = bool(px.loc[t] < ma.loc[t]) if pd.notna(ma.loc[t]) else None
            sl = float(slope.loc[t]) if pd.notna(slope.loc[t]) else None
            rl = float(rel.loc[t]) if pd.notna(rel.loc[t]) else None
            ir = None
            if ivr is not None:
                ie = ivr.index[ivr.index <= d]
                if len(ie):
                    ir = float(ivr.loc[ie[-1]]) if pd.notna(ivr.loc[ie[-1]]) else None
            if below is None or sl is None or rl is None or ir is None:
                continue
            rows.append({**c.to_dict(),
                         "below_ma": below, "ma_falling": sl < 0, "weak126": rl < 0,
                         "rich_iv": ir >= IV_PCTL,
                         "live": below and sl < 0 and rl < 0 and ir >= IV_PCTL,
                         "live_noiv": below and sl < 0 and rl < 0,
                         "recovered": (not below) and sl > 0,
                         "yr": d.year})
    return pd.DataFrame(rows)


def wf_mean_pos(df, mask):
    out = []
    for y0, y1 in WINDOWS:
        w = df[mask & (df.yr >= y0) & (df.yr <= y1)]
        out.append(w["mgd50_pnl"].mean() if len(w) >= 1 else np.nan)
    return out


def main():
    o = pd.read_parquet(SUB)
    o = o[o.moneyness == "OTM"].reset_index(drop=True)
    o["entry_date"] = pd.to_datetime(o["entry_date"]); o["expiration"] = pd.to_datetime(o["expiration"])
    print(f"OTM cycles: {len(o)} | all-OTM mean mgd50 ${o.mgd50_pnl.mean():+.3f}/sh (gross baseline)")
    o["elig"] = gate_b_eligible(o)
    elig = o[o.elig].copy()
    print(f"Gate-B-eligible (out-of-sample): {len(elig)} cycles, {elig.ticker.nunique()} names | mean ${elig.mgd50_pnl.mean():+.3f}/sh")

    spy = load_adjusted_close("SPY").dropna().sort_index()
    df = tag_states(elig, spy)
    print(f"Tagged (have full state): {len(df)} cycles")

    base = df["mgd50_pnl"].mean()
    live = df[df.live]; rec = df[df.recovered]; livenoiv = df[df.live_noiv]
    m_live = live["mgd50_pnl"].mean(); m_rec = rec["mgd50_pnl"].mean(); m_lnoiv = livenoiv["mgd50_pnl"].mean()
    lift = m_live - base; lift_noiv = m_lnoiv - base

    # Gate C placebo: random filter of equal size
    rng = np.random.default_rng(42); n_live = len(live); pls = []
    if n_live > 0:
        for _ in range(1000):
            s = df["mgd50_pnl"].sample(n=n_live, random_state=int(rng.integers(1e9)))
            pls.append(s.mean() - base)
    pls = np.array(pls); plac95 = np.percentile(pls, 95) if len(pls) else np.nan

    wf = wf_mean_pos(df, df.live); wf_pos = sum(1 for x in wf if x == x and x > 0)
    perwin_n = [int(((df.live) & (df.yr >= y0) & (df.yr <= y1)).sum()) for y0, y1 in WINDOWS]

    A = lift >= GATE_A
    B = m_live > 0
    C = (lift > plac95) if plac95 == plac95 else False
    D = wf_pos >= 3
    E = m_rec < 0
    F = (lift - lift_noiv) >= GATE_F
    G = (n_live >= 150) and all(n >= 30 for n in perwin_n)
    verdict = ("INCONCLUSIVE" if not G else
               ("PROMOTE" if (A and B and C and D and F) else "REJECT"))

    print("=" * 84)
    print("  BEAR-CALL LIVE-STATE FILTER — sealed validation (gross/substrate-native P&L)")
    print("=" * 84)
    print(f"  all eligible mean   ${base:+.3f}/sh   (n={len(df)})")
    print(f"  LIVE-state mean     ${m_live:+.3f}/sh   (n={n_live})   lift {lift:+.3f}")
    print(f"  LIVE w/o IV-rank    ${m_lnoiv:+.3f}/sh   (n={len(livenoiv)})   lift {lift_noiv:+.3f}")
    print(f"  RECOVERED mean      ${m_rec:+.3f}/sh   (n={len(rec)})")
    print(f"  placebo lift 95th   {plac95:+.3f}   | walk-forward live means {[round(x,3) if x==x else None for x in wf]}  ({wf_pos}/4 >0)")
    print(f"  per-window live n   {perwin_n}")
    print("-" * 84)
    print(f"  A filter-lift≥{GATE_A}: {A} ({lift:+.3f})   B live>0: {B} ({m_live:+.3f}, slip-sensitive)")
    print(f"  C beats placebo: {C}   D walk-fwd≥3/4: {D} ({wf_pos}/4)   E recovered<0: {E} ({m_rec:+.3f})")
    print(f"  F IV-rank carries (≥{GATE_F}): {F} (Δlift {lift-lift_noiv:+.3f})   G N-adequate: {G} (n={n_live}, win {perwin_n})")
    print(f"\n  ►► VERDICT: {verdict}")
    print("=" * 84)

    out = ROOT / "data/profile/bearcall_live_state_filter.parquet"
    df.to_parquet(out, index=False)
    rep = ROOT / f"reports/bearcall_live_state_filter_{pd.Timestamp('2026-05-30').date()}.md"
    rep.parent.mkdir(exist_ok=True)
    L = [f"# Bear-call live-state filter — validation (2026-05-30)\n", f"**Verdict: {verdict}** (gross/substrate P&L; Gate B slip-sensitive)\n",
         f"- all-OTM baseline ${o.mgd50_pnl.mean():+.3f} | Gate-B-eligible ${elig.mgd50_pnl.mean():+.3f} | tagged n={len(df)}\n",
         "| cell | mean $/sh | n |\n|---|---|---|",
         f"| all eligible | {base:+.3f} | {len(df)} |",
         f"| LIVE-state | {m_live:+.3f} | {n_live} |",
         f"| LIVE w/o IV-rank | {m_lnoiv:+.3f} | {len(livenoiv)} |",
         f"| RECOVERED | {m_rec:+.3f} | {len(rec)} |",
         f"\n| gate | result |\n|---|---|",
         f"| A lift≥{GATE_A} | {A} ({lift:+.3f}) |", f"| B live>0 | {B} ({m_live:+.3f}) |",
         f"| C placebo | {C} (95th {plac95:+.3f}) |", f"| D walk-fwd | {D} ({wf_pos}/4) |",
         f"| E recovered<0 | {E} ({m_rec:+.3f}) |", f"| F IV-rank carries | {F} (Δ {lift-lift_noiv:+.3f}) |",
         f"| G N-adequate | {G} (n={n_live}) |\n"]
    rep.write_text("\n".join(L))
    print(f"  wrote {out.name}, {rep.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
