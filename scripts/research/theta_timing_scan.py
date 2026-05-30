#!/usr/bin/env python3.11
"""'Let theta do its thing' — per-ticker time-to-reversion scan (EXPLORATION).

Thesis: a bearish defined-risk spread on a name that's weak vs the market doesn't
need the name to keep falling — it needs the name NOT to rally back into trouble
for ~20 trading days, so time decay banks ~50% of max profit before any bounce.
So this is a TIMING question: after a weakness trigger, how long does a name
historically stay out of danger?

Run as a POPULATION test across the full extracted universe (635 names), with two
controls so we don't fool ourselves:
  - BASELINE: the same survival test entered on signal-free dates (every 5th day).
    The trigger only matters if it beats this.
  - PLACEBO: lift recomputed on N RANDOM entry dates per name (same N as the real
    triggers). The spread of placebo lifts is the finite-sample NOISE FLOOR — the
    real lift distribution must clear it to be a signal, not selection luck.

Definitions (split-clean, gap-filled adjusted close):
  - Weakness trigger: trailing-21d market-stripped residual (r_name − beta·r_SPY,
    beta=rolling 60d) in the name's worst decile AND negative.
  - Danger Y = 1-sigma monthly up move (daily vol * sqrt(21)).
  - DAYS-TO-DANGER over 30 fwd days; survived = stayed < +Y for >= 20 days.
  - FIT = share of triggers that survived; LIFT = FIT − baseline.
"""
from __future__ import annotations

import argparse
import glob
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.adjusted_close import load_adjusted_close  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
BETA_WIN, TRAIL, FWD, SURVIVE, WEAK_PCTL, MIN_EVENTS = 60, 21, 30, 20, 10, 8


def residual_weakness(name: str, spy: pd.Series):
    s = load_adjusted_close(name).dropna().sort_index()
    df = pd.DataFrame({"px": s}).join(spy.rename("spy"), how="inner")
    if len(df) < 400:
        return None
    df["r"] = np.log(df["px"] / df["px"].shift(1))
    df["rm"] = np.log(df["spy"] / df["spy"].shift(1))
    cov = df["r"].rolling(BETA_WIN).cov(df["rm"])
    var = df["rm"].rolling(BETA_WIN).var()
    df["beta"] = (cov / var).clip(-3, 4)
    df["resid"] = df["r"] - df["beta"] * df["rm"]
    df["trail_resid"] = df["resid"].rolling(TRAIL).sum()
    df["vol"] = df["r"].rolling(BETA_WIN).std()
    return df.dropna(subset=["trail_resid", "vol"])


def scan_name(name: str, spy: pd.Series) -> dict | None:
    df = residual_weakness(name, spy)
    if df is None or len(df) < 300:
        return None
    thresh = np.percentile(df["trail_resid"], WEAK_PCTL)
    px, tr, vol, idx = df["px"].values, df["trail_resid"].values, df["vol"].values, df.index
    n = len(df)

    def survives(i):
        entry = px[i]
        Y = vol[i] * np.sqrt(TRAIL)
        w = px[i + 1:i + 1 + FWD]
        if len(w) < SURVIVE:
            return None
        hit = np.argmax(w >= entry * (1 + Y)) if (w >= entry * (1 + Y)).any() else -1
        return (hit + 1) if hit >= 0 else FWD

    valid = [i for i in range(TRAIL, n - 1) if survives(i) is not None]
    if not valid:
        return None
    baseline = float(np.mean([survives(i) >= SURVIVE for i in valid[::5]]))

    ev = []
    i = TRAIL
    while i < n - 1:
        if tr[i] <= thresh and tr[i] < 0:
            d = survives(i)
            if d is not None:
                ev.append((idx[i], d))
            i += TRAIL
        else:
            i += 1
    if len(ev) < MIN_EVENTS:
        return {"name": name, "n": len(ev), "skip": True, "live": _live(df, thresh)}

    d2d = np.array([e[1] for e in ev])
    fit = float((d2d >= SURVIVE).mean())
    mid = len(ev) // 2
    fa = float((np.array([e[1] for e in ev[:mid]]) >= SURVIVE).mean())
    fb = float((np.array([e[1] for e in ev[mid:]]) >= SURVIVE).mean())
    # placebo: same count of RANDOM entry dates -> finite-sample noise floor
    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    pick = rng.choice(valid, size=min(len(ev), len(valid)), replace=False)
    placebo_fit = float(np.mean([survives(int(i)) >= SURVIVE for i in pick]))
    return {"name": name, "n": len(ev), "skip": False, "fit": fit, "baseline": baseline,
            "lift": fit - baseline, "placebo_lift": placebo_fit - baseline,
            "fit_first": fa, "fit_second": fb, "live": _live(df, thresh)}


def _live(df, thresh) -> bool:
    last = df.iloc[-1]
    return bool(last["trail_resid"] <= thresh and last["trail_resid"] < 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", action="store_true", help="scan only the 37 live names")
    args = ap.parse_args()

    cohort = set(pd.read_parquet(ROOT / "data/profile/research_cohort_v15.parquet")
                 ["ticker"].astype(str).str.upper())
    if args.cohort:
        tickers = sorted(cohort)
    else:
        tickers = sorted(p.stem.upper() for p in BY_TICKER.glob("*.parquet"))
    spy = load_adjusted_close("SPY").dropna().sort_index()

    rows = []
    for k, t in enumerate(tickers, 1):
        if t in ("SPY", "SPX"):
            continue
        try:
            r = scan_name(t, spy)
        except Exception:
            continue
        if r:
            rows.append(r)
        if k % 100 == 0:
            print(f"  ...scanned {k}/{len(tickers)}")

    good = [r for r in rows if not r["skip"]]
    lifts = np.array([r["lift"] for r in good])
    plac = np.array([r["placebo_lift"] for r in good])

    print("=" * 100)
    print(f"  'Let theta do its thing' — POPULATION test  ({len(good)} names with ≥{MIN_EVENTS} triggers, "
          f"of {len(rows)} scanned)")
    print("=" * 100)
    print("  Per-name LIFT = trigger-entry FIT − signal-free baseline FIT.  PLACEBO = same on random dates.")
    print(f"  {'':14}{'mean':>8}{'median':>8}{'p25':>8}{'p75':>8}{'p90':>8}{'>+5pp':>8}{'>+10pp':>8}{'<0':>6}")
    for lab, a in [("REAL lift", lifts), ("PLACEBO lift", plac)]:
        print(f"  {lab:14}{a.mean()*100:>+7.1f}{np.median(a)*100:>+8.1f}{np.percentile(a,25)*100:>+8.1f}"
              f"{np.percentile(a,75)*100:>+8.1f}{np.percentile(a,90)*100:>+8.1f}"
              f"{(a>0.05).mean()*100:>7.0f}%{(a>0.10).mean()*100:>7.0f}%{(a<0).mean()*100:>5.0f}%")
    shift = lifts.mean() - plac.mean()
    print(f"\n  REAL minus PLACEBO mean shift: {shift*100:+.2f}pp   "
          f"(near 0 ⇒ trigger adds nothing beyond chance)")
    # excess names above noise: real >+10pp vs placebo >+10pp
    print(f"  Names clearing +10pp: real {(lifts>0.10).sum()} vs placebo {(plac>0.10).sum()} "
          f"(excess = candidate real signals)")

    print("\n  Top 20 by lift (need OOS-stable: 1st≈2nd half):")
    print(f"  {'name':6}{'n':>4}{'FIT':>6}{'base':>6}{'LIFT':>7}{'OOS 1st/2nd':>14}{'live':>6}{'tradeable':>11}")
    for r in sorted(good, key=lambda r: -r["lift"])[:20]:
        oos = f"{r['fit_first']*100:.0f}/{r['fit_second']*100:.0f}"
        tag = "◀" if r["live"] else ""
        trd = "cohort" if r["name"] in cohort else ""
        print(f"  {r['name']:6}{r['n']:>4}{r['fit']*100:>5.0f}%{r['baseline']*100:>5.0f}%"
              f"{r['lift']*100:>+6.0f}pp{oos:>14}{tag:>6}{trd:>11}")

    live_pos = sorted([r for r in good if r["live"] and r["lift"] > 0.05
                       and min(r["fit_first"], r["fit_second"]) > 0.6],
                      key=lambda r: -r["lift"])
    live_str = ", ".join(f"{r['name']} {r['lift']*100:+.0f}pp" for r in live_pos) or "none"
    print(f"\n  LIVE + lift>+5pp + OOS-stable: {live_str}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
