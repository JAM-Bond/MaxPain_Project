#!/usr/bin/env python3.11
"""Breadth-ring sizing gate — backtest corroboration (per sealed pre-reg D).

docs/BREADTH_RING_SIZING_PREREG.md, §4 step 1-2 and §5 gates A/B/C.

Tags every historical long-delta cycle by its ENTRY-DAY breadth-ring state
(join breadth_ring_daily.asof = entry_date) and asks whether 🔴 (narrowing +
extended) entries underperform, whether half-sizing them improves the
risk-adjusted cohort, and whether it holds in both walk-forward splits.

Primary: bull_put (14k cycles, price_breach_stop_results — managed_pnl is the
Window-A managed exit; loss-cap = managed_pnl <= -2x entry_credit, the sealed
2x rule; max_adverse_depth = realized max adverse excursion).
Secondary: zebra (overlay combined-hold P&L; debit structure, no 2x cap, so
tail measured by worst-decile return).

Descriptive corroboration only — promotion is a separate discipline decision.
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
OUT = ROOT / "data/profile/breadth_ring_sizing_corroboration.parquet"

# Sealed Gate-A margins (pre-reg §5)
GATE_A_PNL_MARGIN = 0.10      # 🔴 mean P&L lower by >= this ($/share)
GATE_A_TAIL_MARGIN = 5.0      # 🔴 loss-cap-hit rate higher by >= this (pts)


def ring_states() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    r = pd.read_sql("SELECT asof, status, broadening, top_warning FROM breadth_ring_daily",
                    conn, parse_dates=["asof"])
    conn.close()
    return r.sort_values("asof").reset_index(drop=True)


def tag_entry_state(cycles: pd.DataFrame, ring: pd.DataFrame, date_col: str) -> pd.DataFrame:
    c = cycles.copy()
    c[date_col] = pd.to_datetime(c[date_col])
    c = c.sort_values(date_col)
    merged = pd.merge_asof(c, ring, left_on=date_col, right_on="asof", direction="backward")
    merged["is_red"] = merged["top_warning"] == 1
    merged["grp"] = np.where(merged["is_red"], "🔴",
                     np.where(merged["broadening"] == 1, "🟢", "🟡"))
    return merged


def _stats_bullput(s: pd.DataFrame) -> dict:
    pnl = s["managed_pnl"]
    losscap = (s["managed_pnl"] <= -2.0 * s["entry_credit"]).mean() * 100
    return dict(n=len(s), mean_pnl=pnl.mean(), win=(pnl > 0).mean() * 100,
                losscap_hit=losscap, mae=s["max_adverse_depth"].mean() * 100,
                p10=pnl.quantile(0.10))


def corroborate_bullput(ring: pd.DataFrame) -> pd.DataFrame:
    d = pd.read_parquet(ROOT / "data/profile/price_breach_stop_results.parquet")
    bp = d[d["structure"] == "bull_put"].copy()
    t = tag_entry_state(bp, ring, "entry_date")

    def block(sub, label):
        print(f"\n  [{label}]  N={len(sub)}")
        rows = {g: _stats_bullput(sub[sub.grp == g]) for g in ("🟢", "🟡", "🔴")}
        nonred = _stats_bullput(sub[~sub.is_red])
        for g in ("🟢", "🟡", "🔴"):
            r = rows[g]
            print(f"    {g}  n={r['n']:5}  mean_pnl={r['mean_pnl']:+.3f}  win={r['win']:3.0f}%  "
                  f"loss-cap-hit={r['losscap_hit']:4.1f}%  maxAdvExc={r['mae']:+.1f}%  p10={r['p10']:+.3f}")
        print(f"    non-🔴 (🟢+🟡)  n={nonred['n']:5}  mean_pnl={nonred['mean_pnl']:+.3f}  "
              f"loss-cap-hit={nonred['losscap_hit']:4.1f}%")
        red = rows["🔴"]
        dpnl = red["mean_pnl"] - nonred["mean_pnl"]
        dtail = red["losscap_hit"] - nonred["losscap_hit"]
        gateA = (dpnl <= -GATE_A_PNL_MARGIN) and (dtail >= GATE_A_TAIL_MARGIN)
        print(f"    → 🔴 vs non-🔴: ΔmeanP&L={dpnl:+.3f} (need ≤ -{GATE_A_PNL_MARGIN}), "
              f"Δloss-cap-hit={dtail:+.1f}pts (need ≥ +{GATE_A_TAIL_MARGIN})  "
              f"=> Gate A {'PASS' if gateA else 'FAIL'}")
        return gateA, dpnl, dtail

    print("\n" + "=" * 78)
    print("BULL_PUT corroboration (managed exit; full universe)")
    print("=" * 78)
    full = block(t, "FULL 2013-2026")
    print("\n  -- Gate C: walk-forward stability --")
    tr = block(t[t.entry_date <= TRAIN_END], "TRAIN ≤2019")
    te = block(t[t.entry_date > TRAIN_END], "TEST ≥2020")
    gateC = (tr[1] < 0 and tr[2] > 0) and (te[1] < 0 and te[2] > 0)  # same sign both splits
    print(f"\n  Gate C (Δ same sign in BOTH splits: 🔴 worse P&L + higher tail): "
          f"{'PASS' if gateC else 'FAIL'}")

    # Gate B — counterfactual half-size on 🔴 cycles
    print("\n  -- Gate B: counterfactual half-size on 🔴 entries --")
    t["pnl_full"] = t["managed_pnl"]
    t["pnl_half"] = np.where(t.is_red, t["managed_pnl"] * 0.5, t["managed_pnl"])
    base_tot, cf_tot = t["pnl_full"].sum(), t["pnl_half"].sum()
    base_down = t.loc[t.pnl_full < 0, "pnl_full"].sum()
    cf_down = t.loc[t.pnl_half < 0, "pnl_half"].sum()
    base_sh = t["pnl_full"].mean() / t["pnl_full"].std()
    cf_sh = t["pnl_half"].mean() / t["pnl_half"].std()
    red_share = t.is_red.mean() * 100
    print(f"    🔴 = {red_share:.1f}% of cycles; their mean P&L = {t.loc[t.is_red,'managed_pnl'].mean():+.3f}")
    print(f"    cohort total P&L: full={base_tot:+.1f} → half-🔴={cf_tot:+.1f} "
          f"({100*(cf_tot-base_tot)/abs(base_tot):+.1f}%)")
    print(f"    downside (Σ losses): full={base_down:+.1f} → half-🔴={cf_down:+.1f} "
          f"({100*(cf_down-base_down)/abs(base_down):+.1f}% less downside)")
    print(f"    risk-adjusted (mean/std): full={base_sh:+.4f} → half-🔴={cf_sh:+.4f}")
    gateB = (cf_down > base_down) and (cf_sh >= base_sh)  # less downside, no worse risk-adj
    print(f"    Gate B (less dollar-downside AND risk-adj not worse): {'PASS' if gateB else 'FAIL'}")

    print("\n" + "-" * 78)
    print(f"  BULL_PUT verdict — Gate A (full): {'PASS' if full[0] else 'FAIL'} | "
          f"Gate B: {'PASS' if gateB else 'FAIL'} | Gate C: {'PASS' if gateC else 'FAIL'}")
    return t[["ticker", "entry_date", "grp", "is_red", "managed_pnl", "held_pnl",
              "entry_credit", "max_adverse_depth"]].assign(structure="bull_put")


def corroborate_zebra(ring: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("ZEBRA corroboration (secondary; overlay combined-hold P&L, tier1+tier2)")
    print("=" * 78)
    frames = []
    for f, tier in [("zebra_put_overlay_phase2_results.parquet", "t1"),
                    ("zebra_put_overlay_tier2_results.parquet", "t2")]:
        p = ROOT / "data/profile" / f
        if p.exists():
            z = pd.read_parquet(p)
            pcol = "pnl_combined_hold" if "pnl_combined_hold" in z.columns else "pnl_zebra"
            frames.append(z[["ticker", "entry_date", pcol]].rename(columns={pcol: "pnl"}).assign(tier=tier))
    if not frames:
        print("  (no zebra per-cycle data found)")
        return
    z = pd.concat(frames, ignore_index=True)
    t = tag_entry_state(z, ring, "entry_date")
    print(f"  N={len(t)} zebra cycles  ({t.entry_date.min().date()}–{t.entry_date.max().date()})")
    for g in ("🟢", "🟡", "🔴"):
        s = t[t.grp == g]
        if len(s) == 0:
            print(f"    {g}  n=0"); continue
        print(f"    {g}  n={len(s):4}  mean_pnl={s.pnl.mean():+.3f}  win={(s.pnl>0).mean()*100:3.0f}%  "
              f"worst-decile={s.pnl.quantile(0.10):+.3f}")
    nonred = t[~t.is_red]; red = t[t.is_red]
    if len(red):
        print(f"    → 🔴 mean={red.pnl.mean():+.3f} vs non-🔴 {nonred.pnl.mean():+.3f} "
              f"(Δ {red.pnl.mean()-nonred.pnl.mean():+.3f}); worst-decile 🔴 {red.pnl.quantile(0.10):+.3f} "
              f"vs non-🔴 {nonred.pnl.quantile(0.10):+.3f}")
    print("  (zebra N is small and it's a debit structure — directional check only, not a gate)")


def main() -> int:
    ring = ring_states()
    print(f"Ring states: {len(ring)} days {ring['asof'].min().date()}→{ring['asof'].max().date()}")
    bp = corroborate_bullput(ring)
    corroborate_zebra(ring)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    bp.to_parquet(OUT, index=False)
    print(f"\nWrote tagged bull_put cycles → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
