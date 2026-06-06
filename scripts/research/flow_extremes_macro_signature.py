#!/usr/bin/env python3.11
"""
Flow-extreme macro signatures — DESCRIPTIVE (retrospective, not predictive).

For each Select Sector SPDR, find the peaks (inflow surges) and valleys (outflow
surges) in its smoothed organic flow (FLOW3), snapshot the macro weather at each,
and ask whether a recurring *trend* signature rhymes across episodes — e.g. "XLE
inflow peaks tend to land when oil is rising and the dollar is falling."

NOT a forecast: we condition on a flow extreme having happened and characterize
the macro around it. Small N per ETF + many indicators → suggestive, not tested.

Usage: python3.11 -m scripts.research.flow_extremes_macro_signature
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from scripts.backtest.sector_flow_rotation_study import flow_signals, SECTORS  # noqa: E402

FRED_DEEP = ROOT / "data/macro/fred_daily_deep.parquet"
MIN_SEP = 6           # months between distinct extrema
CONSISTENT = 0.67     # a trend "rhymes" if ≥ this share of episodes agree

IND = {  # indicator: (FRED expr, human label, trend-up word, trend-down word)
    "rates":   ("DGS10",        "10y rate",   "rising",      "falling"),
    "curve":   ("T10Y2Y",       "2s10s curve","steepening",  "flattening"),
    "inflexp": ("T10YIE",       "infl-exp",   "rising",      "falling"),
    "vix":     ("VIXCLS",       "VIX",        "rising",      "falling"),
    "credit":  ("DBAA-DAAA",    "credit sprd","widening",    "tightening"),
    "oil":     ("DCOILWTICO",   "oil",        "rising",      "falling"),
    "dollar":  ("DTWEXBGS",     "dollar",     "rising",      "falling"),
    "unemp":   ("UNRATE",       "unemp",      "rising",      "falling"),
}


def macro_panel() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Month-end macro levels, 3m-trend signs, and level percentiles (0-100)."""
    f = pd.read_parquet(FRED_DEEP)
    f["date"] = pd.to_datetime(f["date"])
    w = f.pivot_table(index="date", columns="series_id", values="value").sort_index().ffill()
    m = w.resample("ME").last()
    lvl = pd.DataFrame(index=m.index)
    lvl["rates"] = m["DGS10"]
    lvl["curve"] = m["T10Y2Y"]
    lvl["inflexp"] = m["T10YIE"]
    lvl["vix"] = m["VIXCLS"]
    lvl["credit"] = m["DBAA"] - m["DAAA"]
    lvl["oil"] = m["DCOILWTICO"]
    lvl["dollar"] = m["DTWEXBGS"]
    lvl["unemp"] = m["UNRATE"]
    trend = np.sign(lvl.diff(3))                       # +1 rising / -1 falling over 3m
    pct = lvl.rank(pct=True) * 100                      # full-history level percentile
    return lvl, trend, pct


def find_extrema(s: pd.Series, prominence: float) -> tuple[list, list]:
    """Local maxima (inflow peaks) and minima (outflow peaks) of a smoothed series,
    each at least MIN_SEP months from the next, exceeding `prominence`."""
    s = s.dropna()
    v = s.values
    peaks, valleys = [], []
    for i in range(1, len(v) - 1):
        lo = max(0, i - MIN_SEP); hi = min(len(v), i + MIN_SEP + 1)
        if v[i] == v[lo:hi].max() and v[i] - v[lo:hi].min() >= prominence and v[i] > 0:
            peaks.append(s.index[i])
        if v[i] == v[lo:hi].min() and v[lo:hi].max() - v[i] >= prominence and v[i] < 0:
            valleys.append(s.index[i])
    # enforce separation (keep the most extreme within any MIN_SEP cluster)
    def thin(idxs, sign):
        idxs = sorted(idxs)
        kept = []
        for t in idxs:
            if kept and (t - kept[-1]).days < MIN_SEP * 28:
                if sign * s[t] > sign * s[kept[-1]]:
                    kept[-1] = t
            else:
                kept.append(t)
        return kept
    return thin(peaks, +1), thin(valleys, -1)


def signature(dates, trend, pct) -> dict:
    """Aggregate macro trend-direction consistency + mean level-percentile over episodes."""
    out = {}
    for k in IND:
        tr = trend.loc[[d for d in dates if d in trend.index], k].dropna()
        pc = pct.loc[[d for d in dates if d in pct.index], k].dropna()
        if len(tr) == 0:
            continue
        up = float((tr > 0).mean())
        out[k] = {"up_share": up, "n": len(tr), "mean_pct": float(pc.mean())}
    return out


def fmt_sig(sig: dict) -> str:
    """Compact line of only the indicators that rhyme (≥ CONSISTENT one direction)."""
    bits = []
    for k, d in sig.items():
        up = d["up_share"]
        if up >= CONSISTENT or up <= 1 - CONSISTENT:
            word = IND[k][2] if up >= 0.5 else IND[k][3]
            share = up if up >= 0.5 else 1 - up
            bits.append(f"{IND[k][1]} {word} {share:.0%}")
    return "; ".join(bits) if bits else "(no consistent trend signature)"


def main() -> int:
    flow3, _ = flow_signals()
    lvl, trend, pct = macro_panel()

    print("FLOW-EXTREME MACRO SIGNATURES — descriptive, retrospective")
    print(f"  smoothed organic flow (FLOW3); extrema ≥{MIN_SEP}mo apart; "
          f"a trend 'rhymes' at ≥{CONSISTENT:.0%} of episodes")
    print("=" * 80)

    rows = []
    agg_in, agg_out = {k: [] for k in IND}, {k: [] for k in IND}
    detail = {}
    for s in SECTORS:
        ser = flow3[s].dropna()
        if len(ser) < 36:
            continue
        prom = max(0.04, float(ser.std()) * 0.75)
        peaks, valleys = find_extrema(ser, prom)
        sig_in = signature(peaks, trend, pct)
        sig_out = signature(valleys, trend, pct)
        detail[s] = (peaks, valleys)
        print(f"\n{s}  ({IND_NAME.get(s, s)})  inflow-peaks n={len(peaks)}, "
              f"outflow-peaks n={len(valleys)}")
        print(f"   INFLOW peaks → {fmt_sig(sig_in)}")
        print(f"   OUTFLOW peaks→ {fmt_sig(sig_out)}")
        for k, d in sig_in.items():
            agg_in[k].append(d["up_share"])
        for k, d in sig_out.items():
            agg_out[k].append(d["up_share"])
        rows.append({"sector": s, "n_in": len(peaks), "n_out": len(valleys),
                     **{f"in_{k}": sig_in.get(k, {}).get("up_share") for k in IND},
                     **{f"out_{k}": sig_out.get(k, {}).get("up_share") for k in IND}})

    # detailed macro snapshots for two macro-driven sectors
    for s in ["XLE", "XLF"]:
        if s not in detail:
            continue
        peaks, valleys = detail[s]
        print(f"\n--- {s} macro SNAPSHOTS (level percentile / 3m trend) ---")
        for label, dates in [("INFLOW peak", peaks), ("OUTFLOW peak", valleys)]:
            for d in dates:
                if d not in pct.index:
                    continue
                cells = " ".join(
                    f"{IND[k][1]}={pct.at[d,k]:.0f}%{'↑' if trend.at[d,k]>0 else '↓'}"
                    for k in ["rates", "curve", "vix", "oil", "dollar", "credit"])
                print(f"   {label:12} {d.date()}  flow3={flow3.at[d,s]*100:+5.1f}%  {cells}")

    # cross-ETF: which macro trends most consistently mark inflow vs outflow extremes
    print("\n" + "=" * 80)
    print("CROSS-ETF — mean 'rising-share' across sectors at flow extremes "
          "(0%=always falling, 100%=always rising):")
    print(f"  {'indicator':12} {'@INFLOW':>9} {'@OUTFLOW':>9}   read")
    for k in IND:
        mi = np.mean(agg_in[k]) if agg_in[k] else np.nan
        mo = np.mean(agg_out[k]) if agg_out[k] else np.nan
        read = ""
        if not np.isnan(mi) and not np.isnan(mo):
            if abs(mi - mo) >= 0.15:
                read = f"inflow↔outflow differ ({IND[k][2] if mi>mo else IND[k][3]} at inflows)"
        print(f"  {IND[k][1]:12} {mi*100:>8.0f}% {mo*100:>8.0f}%   {read}")

    OUT = ROOT / "data/profile/flow_extremes_macro_signature.parquet"
    pd.DataFrame(rows).to_parquet(OUT, index=False)
    print(f"\nWrote {OUT.relative_to(ROOT)}")
    return 0


IND_NAME = {"XLE": "energy", "XLF": "financials", "XLK": "tech", "XLV": "healthcare",
            "XLI": "industrials", "XLP": "staples", "XLY": "discretionary",
            "XLU": "utilities", "XLB": "materials", "XLRE": "real estate", "XLC": "comms"}

if __name__ == "__main__":
    sys.exit(main())
