#!/usr/bin/env python3.11
"""SPY + RSP (+ S&P breadth) combined trend signal — walk-forward study.

Question (user): do SPY (cap-weight) and RSP (equal-weight) used TOGETHER give a
finer, more reliable read on market DIRECTION/TREND than SPY alone? Three parts:
  1. Walk-forward the RSP/SPY relative-strength signal (train 2013-19 -> test 2020-26,
     rule fixed a priori, no retuning on test).
  2. Add a third leg: true breadth (% of S&P above 50dma, breadth_spx500_v2).
  3. Build a CONTINUOUS causal breadth-quality score and ask, out-of-sample, whether
     the combined (RSP + breadth + SPY) score separates forward SPY returns / downside
     tail better than a SPY-trend-only score.

Discipline ("stick with the data"): out-of-sample test; causal/expanding standardization
(no look-ahead); fixed a-priori lookbacks (sensitivity shown, not optimized); forward
windows overlap so we report episode counts AND a non-overlapping 21-day-sampled view;
2013-26 is bull-dominated so RELATIVE separation across states is the trustworthy signal,
not absolute levels. Descriptive, not a promoted rule.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path.home() / "MaxPain_Project"
TRAIN_END = pd.Timestamp("2019-12-31")
HORIZONS = [21, 42, 63]
TAIL = -0.05  # downside-tail threshold for P(fwd42 < -5%)


def closes(t: str) -> pd.Series:
    df = yf.download(t, start="2011-01-01", end="2026-06-10", auto_adjust=True, progress=False)
    return pd.Series(np.asarray(df["Close"]).ravel(), index=pd.to_datetime(df.index), name=t)


def zexp(s: pd.Series, minp: int = 252) -> pd.Series:
    """Causal expanding z-score (uses only past+present)."""
    m = s.expanding(min_periods=minp).mean()
    sd = s.expanding(min_periods=minp).std()
    return (s - m) / sd


def episodes(mask: pd.Series) -> int:
    m = mask.astype(int).values
    if len(m) == 0:
        return 0
    return int(((m[1:] == 1) & (m[:-1] == 0)).sum() + (m[0] == 1))


def fwd_stats(sub: pd.DataFrame, h: int) -> tuple:
    v = sub[f"fwd{h}"].dropna()
    if len(v) == 0:
        return (np.nan, np.nan, np.nan, 0)
    return (v.mean() * 100, 100 * (v > 0).mean(), 100 * (v < TAIL).mean(), len(v))


def build() -> pd.DataFrame:
    spy, rsp = closes("SPY"), closes("RSP")
    d = pd.DataFrame({"SPY": spy, "RSP": rsp}).dropna()
    d["SPY200"] = d.SPY.rolling(200).mean()
    d["spy_above"] = d.SPY > d.SPY200
    d["spy_trend"] = d.SPY / d.SPY200 - 1.0           # continuous SPY trend
    d["ratio"] = d.RSP / d.SPY                         # equal-wt relative strength
    d["ratio50"] = d.ratio.rolling(50).mean()
    d["rs"] = d.ratio / d.ratio50 - 1.0               # >0 = breadth broadening
    d["broadening"] = d.rs > 0
    # third leg: true S&P breadth
    br = pd.read_parquet(ROOT / "data/profile/breadth_spx500_v2.parquet")
    br["date"] = pd.to_datetime(br["date"])
    br = br[["date", "pct_above_50dma"]].set_index("date")
    d = d.join(br, how="left")
    d["breadth"] = d["pct_above_50dma"]
    for h in HORIZONS:
        d[f"fwd{h}"] = d.SPY.shift(-h) / d.SPY - 1.0
    return d.dropna(subset=["SPY200", "ratio50", "breadth"]).copy()


def part1_walkforward(d: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("PART 1 — WALK-FORWARD: RSP/SPY relative-strength (broadening vs narrowing)")
    print(" rule FIXED a priori (ratio vs its 50dma); train<=2019, test>=2020; no retune")
    print("=" * 80)
    for split, sub in [("TRAIN 2013-2019", d[d.index <= TRAIN_END]),
                       ("TEST  2020-2026", d[d.index > TRAIN_END])]:
        print(f"\n  [{split}]")
        for lab, m in [("broadening (RSP outperf)", sub.broadening),
                       ("narrowing  (SPY outperf)", ~sub.broadening)]:
            s = sub[m]
            mean42, pos42, tail42, n = fwd_stats(s, 42)
            print(f"    {lab:26} days={n:5} epis={episodes(m):3} | "
                  f"fwd42 mean={mean42:+5.2f}%  pos={pos42:3.0f}%  P(<-5%)={tail42:3.0f}%")
        # separation = narrowing-tail minus broadening-tail (risk lift)
        b = sub[sub.broadening]; nw = sub[~sub.broadening]
        sep = (100*(nw.fwd42 < TAIL).mean()) - (100*(b.fwd42 < TAIL).mean())
        dret = (b.fwd42.mean() - nw.fwd42.mean()) * 100
        print(f"    -> separation: broadening fwd42 beats narrowing by {dret:+.2f}%; "
              f"narrowing tail-risk +{sep:.0f}pts")

    print("\n  Lookback sensitivity (ratio-MA window) on TEST only — a priori was 50:")
    test = d[d.index > TRAIN_END]
    for w in [21, 50, 100]:
        ratio_w = (test.ratio / test.ratio.rolling(w).mean() - 1.0) > 0
        b = test[ratio_w.fillna(False)]; nw = test[~ratio_w.fillna(False)]
        dret = (b.fwd42.mean() - nw.fwd42.mean()) * 100
        tl = (100*(nw.fwd42 < TAIL).mean()) - (100*(b.fwd42 < TAIL).mean())
        print(f"    MA{w:>3}: broadening−narrowing fwd42 = {dret:+.2f}%   tail-risk lift = {tl:+.0f}pts")

    print("\n  Non-overlapping check (sample every 21 trading days, TEST):")
    nov = test.iloc[::21]
    b = nov[nov.broadening]; nw = nov[~nov.broadening]
    print(f"    broadening n={len(b)} fwd42 mean={b.fwd42.mean()*100:+.2f}% pos={100*(b.fwd42>0).mean():.0f}% | "
          f"narrowing n={len(nw)} fwd42 mean={nw.fwd42.mean()*100:+.2f}% pos={100*(nw.fwd42>0).mean():.0f}%")


def part2_breadth(d: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("PART 2 — THIRD LEG: true S&P breadth (% above 50dma) as a standalone signal")
    print("=" * 80)
    for split, sub in [("TRAIN 2013-2019", d[d.index <= TRAIN_END]),
                       ("TEST  2020-2026", d[d.index > TRAIN_END])]:
        print(f"\n  [{split}]  breadth quintiles -> fwd42 SPY return")
        q = pd.qcut(sub.breadth, 5, labels=["Q1 low", "Q2", "Q3", "Q4", "Q5 high"])
        for ql in ["Q1 low", "Q2", "Q3", "Q4", "Q5 high"]:
            s = sub[q == ql]
            mean42, pos42, tail42, n = fwd_stats(s, 42)
            print(f"    {ql:8} breadth~[{s.breadth.min():.2f},{s.breadth.max():.2f}] n={n:5} | "
                  f"fwd42={mean42:+5.2f}%  pos={pos42:3.0f}%  P(<-5%)={tail42:3.0f}%")
    # interaction with RSP relative strength (does breadth add to RS?), TEST
    print("\n  Does breadth ADD to RSP relative-strength? (TEST, within SPY>200 uptrends)")
    test = d[(d.index > TRAIN_END) & (d.spy_above)]
    for rs_lab, rs_m in [("broadening", test.broadening), ("narrowing", ~test.broadening)]:
        for br_lab, br_m in [("hi-breadth", test.breadth >= test.breadth.median()),
                             ("lo-breadth", test.breadth < test.breadth.median())]:
            s = test[rs_m & br_m]
            mean42, pos42, tail42, n = fwd_stats(s, 42)
            print(f"    RS={rs_lab:10} x {br_lab:10} n={n:4} | fwd42={mean42:+5.2f}% pos={pos42:3.0f}% P(<-5%)={tail42:3.0f}%")


def part3_score(d: pd.DataFrame) -> None:
    print("\n" + "=" * 80)
    print("PART 3 — CONTINUOUS breadth-quality SCORE (causal z) vs SPY-trend-only")
    print(" SPY-only score = z(spy_trend);  COMBINED = z(spy_trend)+z(rs)+z(breadth)")
    print(" Standardization is causal/expanding (no look-ahead). Evaluate on TEST.")
    print("=" * 80)
    d = d.copy()
    d["z_spy"] = zexp(d.spy_trend)
    d["z_rs"] = zexp(d.rs)
    d["z_breadth"] = zexp(d.breadth)
    d["score_spy_only"] = d.z_spy
    d["score_combined"] = d[["z_spy", "z_rs", "z_breadth"]].mean(axis=1)
    test = d[d.index > TRAIN_END].dropna(subset=["score_spy_only", "score_combined", "fwd42"])

    for col, name in [("score_spy_only", "SPY-trend ONLY"), ("score_combined", "COMBINED (SPY+RSP+breadth)")]:
        print(f"\n  [{name}]  quintile of score -> fwd42 SPY return (TEST)")
        q = pd.qcut(test[col], 5, labels=["Q1 worst", "Q2", "Q3", "Q4", "Q5 best"])
        means = {}
        for ql in ["Q1 worst", "Q2", "Q3", "Q4", "Q5 best"]:
            s = test[q == ql]
            mean42, pos42, tail42, n = fwd_stats(s, 42)
            means[ql] = mean42
            print(f"    {ql:9} n={n:4} | fwd42={mean42:+5.2f}%  pos={pos42:3.0f}%  P(<-5%)={tail42:3.0f}%")
        spread = means["Q5 best"] - means["Q1 worst"]
        # monotonicity: how many of the 4 steps go up
        seq = [means[k] for k in ["Q1 worst", "Q2", "Q3", "Q4", "Q5 best"]]
        mono = sum(1 for i in range(4) if seq[i+1] > seq[i])
        # rank correlation (Spearman) score vs fwd42
        rho = test[col].corr(test["fwd42"], method="spearman")
        print(f"    -> Q5−Q1 spread={spread:+.2f}%  monotone-steps={mono}/4  Spearman(score,fwd42)={rho:+.3f}")


def main() -> int:
    d = build()
    print(f"Data: {d.index.min().date()} -> {d.index.max().date()}  N={len(d)} trading days "
          f"(train<=2019: {(d.index<=TRAIN_END).sum()}, test: {(d.index>TRAIN_END).sum()})")
    part1_walkforward(d)
    part2_breadth(d)
    part3_score(d)
    print("\n" + "=" * 80)
    print("Reads: Part1 — does broadening>narrowing separation hold OUT-OF-SAMPLE?")
    print("       Part2 — is breadth monotone in forward return? does it add to RS?")
    print("       Part3 — does COMBINED beat SPY-ONLY on Q5−Q1 spread / Spearman / tail, on TEST?")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
