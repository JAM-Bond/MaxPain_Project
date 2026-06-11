#!/usr/bin/env python3.11
"""Breadth ring × ZEBRA — robustness study (per docs/BREADTH_RING_ZEBRA_FINDING.md §5).

The exploratory finding: zebra entries opened on a 🔴 (narrowing+extended) breadth-ring
day earn ~0 vs ~+17/cycle and carry a fatter left tail. §5 sets the bar a confirmatory
study must clear before a sizing-gate pre-reg is warranted. This runs that battery:

  1. Sign stability (combined-hold, walk-forward) — already shown; re-confirmed here.
  2. Overlay-variant robustness — does the 🔴 penalty hold across ATM / OTM-5 / OTM-10
     overlays, not just OTM-10?
  3. Drop-any-year — does it survive leave-one-year-out, and dropping 2021 & 2023 (the
     biggest small-N contributors)?
  4. Tail-reduction counterfactual — does half-sizing / skipping 🔴 entries cut the
     cohort left tail (CVaR / worst-decile) while total P&L barely moves?
  5. Adequacy — 🔴 N per split and per year; flag thin cells.

These are robustness checks on the full 13-yr record (the walk-forward split is the only
out-of-sample element); they can falsify the signal but cannot by themselves prove it
generalizes to future data. Descriptive — no gate is built here.

Tier-1: zebra_put_overlay_results.parquet · Tier-2: zebra_put_overlay_tier2_results.parquet
(both carry pnl_v{1_atm,2_otm5,3_otm10}_combined). Tagged by breadth_ring_daily entry state.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from lib.db import DB_PATH  # noqa: E402

TRAIN_END = pd.Timestamp("2019-12-31")
VARIANTS = {"ATM": "pnl_v1_atm_combined", "OTM5": "pnl_v2_otm5_combined", "OTM10": "pnl_v3_otm10_combined"}
TIERS = [("zebra_put_overlay_results.parquet", "t1"),
         ("zebra_put_overlay_tier2_results.parquet", "t2")]


def ring() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    r = pd.read_sql("SELECT asof, top_warning FROM breadth_ring_daily", conn, parse_dates=["asof"])
    conn.close()
    return r.sort_values("asof").reset_index(drop=True)


def cohort(variant_col: str, ring_df: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for f, tier in TIERS:
        z = pd.read_parquet(ROOT / "data/profile" / f)
        frames.append(z[["ticker", "entry_date", variant_col]]
                      .rename(columns={variant_col: "pnl"}).assign(tier=tier))
    c = pd.concat(frames, ignore_index=True)
    c["entry_date"] = pd.to_datetime(c["entry_date"])
    c = c.sort_values("entry_date")
    c = pd.merge_asof(c, ring_df, left_on="entry_date", right_on="asof", direction="backward")
    c["is_red"] = c["top_warning"] == 1
    c["yr"] = c["entry_date"].dt.year
    return c


def delta(c: pd.DataFrame) -> tuple:
    r, nr = c[c.is_red], c[~c.is_red]
    if len(r) == 0 or len(nr) == 0:
        return (np.nan, np.nan, np.nan, len(r))
    return (r.pnl.mean() - nr.pnl.mean(),            # Δmean (want < 0)
            r.pnl.quantile(0.10) - nr.pnl.quantile(0.10),  # Δ worst-decile (want < 0 = fatter)
            r.pnl.mean(), len(r))


def main() -> int:
    rd = ring()
    print("=" * 84)
    print("ZEBRA ROBUSTNESS — breadth-ring 🔴 entry penalty (combined parent+overlay hold P&L)")
    print("=" * 84)

    # ---- Test 1+2: overlay-variant robustness × walk-forward ----
    print("\n[2] OVERLAY-VARIANT ROBUSTNESS  (Δmean = 🔴 − non-🔴; want < 0 in every cell)")
    print(f"  {'variant':7} | {'FULL Δmean':>11} {'(🔴wd−nr)':>9} | {'TRAIN Δ':>8} | {'TEST Δ':>8} | {'🔴N full':>7}")
    variant_pass = {}
    base = None
    for name, col in VARIANTS.items():
        c = cohort(col, rd)
        if name == "OTM10":
            base = c
        df, dwd, _, n = delta(c)
        dtr = delta(c[c.entry_date <= TRAIN_END])[0]
        dte = delta(c[c.entry_date > TRAIN_END])[0]
        ok = (df < 0) and (dtr < 0) and (dte < 0)
        variant_pass[name] = ok
        print(f"  {name:7} | {df:>+11.2f} {dwd:>+9.1f} | {dtr:>+8.2f} | {dte:>+8.2f} | {n:>7}  {'✓' if ok else '✗ sign flips'}")
    test2 = all(variant_pass.values())
    print(f"  → Test 2 {'PASS' if test2 else 'FAIL'}: 🔴 underperforms in {'all' if test2 else 'NOT all'} variants × both splits")

    # ---- Test 3: drop-any-year + drop 2021/2023 (on OTM10) ----
    print("\n[3] DROP-YEAR STABILITY  (OTM10; full-sample Δmean recomputed leaving years out)")
    full_d = delta(base)[0]
    years = sorted(base.yr.unique())
    loo = {y: delta(base[base.yr != y])[0] for y in years}
    worst_y = max(loo, key=lambda y: loo[y])   # the year whose removal most weakens (raises) Δ
    print(f"  baseline full Δmean = {full_d:+.2f}")
    print(f"  leave-one-year-out Δmean range: [{min(loo.values()):+.2f}, {max(loo.values()):+.2f}]  "
          f"(weakest when dropping {worst_y}: {loo[worst_y]:+.2f})")
    d21 = delta(base[base.yr != 2021])[0]
    d23 = delta(base[base.yr != 2023])[0]
    d2123 = delta(base[~base.yr.isin([2021, 2023])])[0]
    print(f"  drop 2021: {d21:+.2f} | drop 2023: {d23:+.2f} | drop BOTH: {d2123:+.2f}")
    test3 = (max(loo.values()) < 0) and (d2123 < 0)   # stays negative under every single drop AND drop-both
    print(f"  → Test 3 {'PASS' if test3 else 'FAIL'}: Δmean stays negative under every leave-out "
          f"{'(incl. drop-both)' if test3 else '(FLIPS — effect leans on those years)'}")

    # ---- Test 4: tail-reduction counterfactual (OTM10) ----
    print("\n[4] TAIL-REDUCTION COUNTERFACTUAL  (half-size / skip 🔴 entries)")
    def cvar(x, q=0.10):
        thr = x.quantile(q)
        return x[x <= thr].mean()
    full_pnl = base.pnl
    half_pnl = np.where(base.is_red, base.pnl * 0.5, base.pnl)
    skip_pnl = base.loc[~base.is_red, "pnl"]
    for lbl, x in [("baseline", full_pnl), ("half-🔴", pd.Series(half_pnl)), ("skip-🔴", skip_pnl)]:
        print(f"  {lbl:9} total={x.sum():>+8.0f}  mean={x.mean():>+6.2f}  "
              f"worst-decile(p10)={x.quantile(0.10):>+7.1f}  CVaR10={cvar(x):>+7.1f}")
    base_cvar, half_cvar = cvar(full_pnl), cvar(pd.Series(half_pnl))
    tot_chg = 100 * (pd.Series(half_pnl).sum() - full_pnl.sum()) / abs(full_pnl.sum())
    cvar_chg = 100 * (half_cvar - base_cvar) / abs(base_cvar)
    test4 = (half_cvar > base_cvar) and (abs(tot_chg) <= 3.0)  # less tail, total within 3%
    print(f"  half-🔴 vs baseline: CVaR10 {cvar_chg:+.1f}% (less tail if >0), total P&L {tot_chg:+.2f}%")
    print(f"  → Test 4 {'PASS' if test4 else 'FAIL'}: tail reduced AND total P&L within 3%")

    # ---- Test 5: adequacy ----
    print("\n[5] ADEQUACY  (🔴 N per split / per year; thin = <15)")
    ntr = int((base[base.entry_date <= TRAIN_END].is_red).sum())
    nte = int((base[base.entry_date > TRAIN_END].is_red).sum())
    print(f"  🔴 N: train={ntr}  test={nte}")
    per_yr = base[base.is_red].groupby("yr").size()
    thin = per_yr[per_yr < 15]
    print(f"  per-year 🔴 N: {dict(per_yr)}")
    print(f"  thin years (<15): {dict(thin)}")
    test5 = (ntr >= 30) and (nte >= 30)
    print(f"  → Test 5 {'PASS' if test5 else 'FAIL'}: both splits ≥30 🔴 cycles "
          f"(note: most individual YEARS are thin — per-year inference unreliable)")

    print("\n" + "=" * 84)
    verdict = {"2 overlay-variant": test2, "3 drop-year": test3, "4 tail-counterfactual": test4,
               "5 adequacy": test5}
    for k, v in verdict.items():
        print(f"  Test {k:24} {'PASS' if v else 'FAIL'}")
    allpass = all(verdict.values())
    print(f"\n  §5 VERDICT: {'ALL PASS → a zebra sizing-gate pre-reg is warranted' if allpass else 'NOT ALL PASS → do NOT pre-register a gate; ring stays descriptive'}")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
