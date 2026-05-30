#!/usr/bin/env python3.11
"""Sector relative-strength persistence — sealed validation.

Implements docs/SECTOR_RS_PERSISTENCE_PREREG.md (SEALED 2026-05-30) exactly.

Question: does a sector's trailing-6m relative strength vs SPY persist over the
next 45 trading days, or mean-revert? Cross-sectional tercile of trailing
relative return -> forward 45td relative return. Structure-agnostic phenomenon
test — NO option pricing, NO new exit rules (minimal added degrees of freedom).

All closes via lib.adjusted_close (split-clean) — the precondition that unblocked
this test: 6 of the 12 cohort ETFs split in-sample (raw stkPx would inject
phantom -50% relative moves on split dates).

Idempotent: writes data/profile/sector_rs_persistence.parquet and a dated report
under reports/. Re-running overwrites both.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.adjusted_close import load_adjusted_close  # noqa: E402
from lib.opex_calendar import monthly_opex_dates, calendar_days_before  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"

# ── Sealed parameters (§6, §7, §8) ───────────────────────────────────────────
COHORT = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
          "XLP", "XLU", "XLV", "XLY", "IYR"]           # 11 GICS sectors (SMH excluded)
BENCH = "SPY"
TRAIL = 126          # trailing trading days (~6 months) for the rank key
FWD = 45             # forward trading days (the predicted horizon, ~45-DTE)
DTE_OFFSET = 45      # entry = monthly OpEx − 45 calendar days
START_YEAR, END_YEAR = 2013, 2026

GATE_A_SPREAD = 0.010        # |TOP−BOTTOM| mean fwd-RS spread
GATE_C_RELIABILITY = 0.55    # directional hit-rate, each side
GATE_E_TOTAL = 150           # obs/tercile, full sample
GATE_E_WINDOW = 30           # obs/tercile, per walk-forward window
WF_WINDOWS = [(2021, 2023), (2022, 2024), (2023, 2025), (2024, 2026)]  # inclusive


def build_rs_frame() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (rs, rs_trail, rs_fwd) DataFrames indexed by SPY trade_date,
    columns = cohort sectors. rs = adj_close(sector)/adj_close(SPY)."""
    spy = load_adjusted_close(BENCH).dropna().sort_index()
    cols = {}
    for s in COHORT:
        adj = load_adjusted_close(s).dropna().sort_index()
        cols[s] = adj.reindex(spy.index) / spy   # NaN where sector lacks history
    rs = pd.DataFrame(cols)
    rs_trail = np.log(rs / rs.shift(TRAIL))        # ln(rs_t / rs_{t-126})
    rs_fwd = np.log(rs.shift(-FWD) / rs)           # ln(rs_{t+45} / rs_t)
    return rs, rs_trail, rs_fwd


def sampling_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Monthly OpEx entry dates (OpEx − 45 cal days), mapped to nearest prior
    trade date in `index`."""
    out = []
    for ox in monthly_opex_dates(START_YEAR, END_YEAR):
        target = pd.Timestamp(calendar_days_before(ox, DTE_OFFSET))
        prior = index[index <= target]
        if len(prior):
            out.append(prior[-1])
    return sorted(set(out))


def build_observations(rs_trail: pd.DataFrame, rs_fwd: pd.DataFrame,
                       dates: list[pd.Timestamp]) -> pd.DataFrame:
    """One row per (sample date, sector) with a valid trail+fwd pair, tagged
    into cross-sectional terciles by rs_trail (rank-based so ties never break
    qcut)."""
    rows = []
    for d in dates:
        trail = rs_trail.loc[d]
        fwd = rs_fwd.loc[d]
        valid = trail.notna() & fwd.notna()
        sub = pd.DataFrame({"rs_trail": trail[valid], "rs_fwd": fwd[valid]})
        if len(sub) < 3:                   # need ≥3 sectors to form terciles
            continue
        ranks = sub["rs_trail"].rank(method="first")
        sub["tercile"] = pd.qcut(ranks, 3, labels=["BOTTOM", "MID", "TOP"])
        sub["date"] = d
        sub["sector"] = sub.index
        rows.append(sub.reset_index(drop=True))
    obs = pd.concat(rows, ignore_index=True)
    obs["tercile"] = obs["tercile"].astype(str)
    return obs


def tercile_means(obs: pd.DataFrame) -> pd.Series:
    return obs.groupby("tercile")["rs_fwd"].mean()


def evaluate(obs: pd.DataFrame) -> dict:
    g = tercile_means(obs)
    counts = obs.groupby("tercile")["rs_fwd"].count()
    spread = float(g["TOP"] - g["BOTTOM"])
    mono_up = bool(g["BOTTOM"] < g["MID"] < g["TOP"])
    mono_down = bool(g["BOTTOM"] > g["MID"] > g["TOP"])

    bottom = obs[obs.tercile == "BOTTOM"]["rs_fwd"]
    top = obs[obs.tercile == "TOP"]["rs_fwd"]
    bottom_neg = float((bottom < 0).mean())
    top_pos = float((top > 0).mean())
    bottom_pos = float((bottom > 0).mean())
    top_neg = float((top < 0).mean())

    # Walk-forward TOP−BOTTOM spread per window
    wf = []
    for y0, y1 in WF_WINDOWS:
        w = obs[(obs.date.dt.year >= y0) & (obs.date.dt.year <= y1)]
        wg = w.groupby("tercile")["rs_fwd"].mean()
        wc = w.groupby("tercile")["rs_fwd"].count()
        wsp = float(wg.get("TOP", np.nan) - wg.get("BOTTOM", np.nan))
        min_per_terc = int(wc.reindex(["BOTTOM", "MID", "TOP"]).min())
        wf.append({"window": f"{y0}-{y1}", "spread": wsp, "min_tercile_n": min_per_terc})
    wf_pos = sum(1 for w in wf if w["spread"] > 0)
    wf_neg = sum(1 for w in wf if w["spread"] < 0)

    # Gate E — adequacy
    e_total_ok = bool(counts.reindex(["BOTTOM", "MID", "TOP"]).min() >= GATE_E_TOTAL)
    e_window_ok = all(w["min_tercile_n"] >= GATE_E_WINDOW for w in wf)
    e_adequate = e_total_ok and e_window_ok

    # Persistence branch
    persistence = {
        "A": spread >= GATE_A_SPREAD,
        "B": mono_up,
        "C": bottom_neg >= GATE_C_RELIABILITY and top_pos >= GATE_C_RELIABILITY,
        "D": wf_pos >= 3,
    }
    # Reversion branch (mirror)
    reversion = {
        "A": spread <= -GATE_A_SPREAD,
        "B": mono_down,
        "C": bottom_pos >= GATE_C_RELIABILITY and top_neg >= GATE_C_RELIABILITY,
        "D": wf_neg >= 3,
    }

    if not e_adequate:
        verdict = "INCONCLUSIVE"
    elif all(persistence.values()):
        verdict = "PERSISTENCE CONFIRMED"
    elif all(reversion.values()):
        verdict = "MEAN-REVERSION CONFIRMED"
    else:
        verdict = "NULL"

    return {
        "means": g, "counts": counts, "spread": spread,
        "mono_up": mono_up, "mono_down": mono_down,
        "bottom_neg": bottom_neg, "top_pos": top_pos,
        "bottom_pos": bottom_pos, "top_neg": top_neg,
        "wf": wf, "wf_pos": wf_pos, "wf_neg": wf_neg,
        "e_total_ok": e_total_ok, "e_window_ok": e_window_ok, "e_adequate": e_adequate,
        "persistence": persistence, "reversion": reversion, "verdict": verdict,
    }


def xlv_asof_read(rs_trail: pd.DataFrame, verdict: str) -> dict:
    """Cross-sectional tercile of XLV on the most recent date where every cohort
    member with history has a trailing-RS value, plus the implied posture."""
    latest = rs_trail.dropna(how="all").index[-1]
    row = rs_trail.loc[latest].dropna()
    ranks = row.rank(method="first")
    terc = pd.qcut(ranks, 3, labels=["BOTTOM", "MID", "TOP"]).astype(str)
    xlv_terc = terc.get("XLV", "n/a")
    # rank position (1 = weakest)
    pos = int(row.rank().get("XLV", np.nan)) if "XLV" in row else None
    if verdict == "PERSISTENCE CONFIRMED" and xlv_terc == "BOTTOM":
        impl = "persistence + XLV BOTTOM → expect continued lag; avoid selling premium (bull-puts) on healthcare into the downtrend."
    elif verdict == "MEAN-REVERSION CONFIRMED" and xlv_terc == "BOTTOM":
        impl = "reversion + XLV BOTTOM → weakness likely to snap back; do NOT fade it; future bull-put candidate AFTER stabilization (timing unmodeled)."
    elif verdict in ("NULL", "INCONCLUSIVE"):
        impl = f"verdict {verdict} → XLV's tercile ({xlv_terc}) carries no validated 45d signal; read stays discretionary."
    else:
        impl = f"XLV is {xlv_terc} tercile; verdict {verdict} most directly informs the BOTTOM/TOP extremes."
    return {"asof": str(latest.date()), "xlv_tercile": xlv_terc,
            "xlv_rank": pos, "n_sectors": int(len(row)), "implication": impl}


def fmt_pct(x: float) -> str:
    return f"{x*100:+.2f}%" if x == x else "n/a"


def main() -> int:
    rs, rs_trail, rs_fwd = build_rs_frame()
    dates = sampling_dates(rs.index)
    obs = build_observations(rs_trail, rs_fwd, dates)

    out_parquet = ROOT / "data/profile/sector_rs_persistence.parquet"
    obs[["date", "sector", "rs_trail", "tercile", "rs_fwd"]].to_parquet(out_parquet, index=False)

    r = evaluate(obs)
    xlv = xlv_asof_read(rs_trail, r["verdict"])

    # ── console ──
    g, c = r["means"], r["counts"]
    print("=" * 72)
    print("  Sector RS persistence — sealed validation (SECTOR_RS_PERSISTENCE_PREREG)")
    print("=" * 72)
    print(f"  Sample dates: {len(dates)}   Observations: {len(obs)}   "
          f"Sectors: {obs.sector.nunique()}   "
          f"Span: {obs.date.min().date()} → {obs.date.max().date()}")
    print()
    print("  Forward 45td relative return vs SPY, by trailing-RS tercile:")
    for t in ["BOTTOM", "MID", "TOP"]:
        print(f"    {t:6}  mean {fmt_pct(float(g[t])):>8}   n={int(c[t])}")
    print(f"    TOP − BOTTOM spread: {fmt_pct(r['spread'])}   "
          f"(gradient {'UP' if r['mono_up'] else ('DOWN' if r['mono_down'] else 'non-monotonic')})")
    print(f"    reliability: BOTTOM<0 {r['bottom_neg']*100:.0f}% | TOP>0 {r['top_pos']*100:.0f}%  "
          f"(mirror: BOTTOM>0 {r['bottom_pos']*100:.0f}% | TOP<0 {r['top_neg']*100:.0f}%)")
    print()
    print("  Walk-forward TOP−BOTTOM spread:")
    for w in r["wf"]:
        print(f"    {w['window']}  {fmt_pct(w['spread']):>8}   (min tercile n={w['min_tercile_n']})")
    print(f"    windows spread>0: {r['wf_pos']}/4   spread<0: {r['wf_neg']}/4")
    print()
    pe, re_ = r["persistence"], r["reversion"]
    print(f"  Gate E adequacy: total≥{GATE_E_TOTAL} {r['e_total_ok']}  "
          f"window≥{GATE_E_WINDOW} {r['e_window_ok']}  → adequate={r['e_adequate']}")
    print(f"  Persistence gates: A {pe['A']}  B {pe['B']}  C {pe['C']}  D {pe['D']}")
    print(f"  Reversion   gates: A {re_['A']}  B {re_['B']}  C {re_['C']}  D {re_['D']}")
    print()
    print(f"  ►► VERDICT: {r['verdict']}")
    print(f"  XLV as-of {xlv['asof']}: tercile {xlv['xlv_tercile']} "
          f"(rank {xlv['xlv_rank']}/{xlv['n_sectors']}, 1=weakest)")
    print(f"     {xlv['implication']}")
    print("=" * 72)

    # ── report ──
    report = ROOT / f"reports/sector_rs_persistence_validation_{date.today().isoformat()}.md"
    report.parent.mkdir(exist_ok=True)
    L = []
    L.append(f"# Sector RS Persistence — Validation Report ({date.today().isoformat()})\n")
    L.append(f"**Verdict: {r['verdict']}**\n")
    L.append(f"Sealed pre-reg: `docs/SECTOR_RS_PERSISTENCE_PREREG.md`. "
             f"Split-clean closes via `lib.adjusted_close`. No option pricing.\n")
    L.append(f"- Sample dates: {len(dates)} | observations: {len(obs)} | "
             f"sectors: {obs.sector.nunique()} | span {obs.date.min().date()}→{obs.date.max().date()}\n")
    L.append("\n## Forward 45td relative return vs SPY, by trailing-RS tercile\n")
    L.append("| Tercile | mean fwd-RS | n |\n|---|---|---|")
    for t in ["BOTTOM", "MID", "TOP"]:
        L.append(f"| {t} | {fmt_pct(float(g[t]))} | {int(c[t])} |")
    L.append(f"\n**TOP − BOTTOM spread: {fmt_pct(r['spread'])}** "
             f"(gradient {'UP' if r['mono_up'] else ('DOWN' if r['mono_down'] else 'non-monotonic')})\n")
    L.append(f"Directional reliability — BOTTOM<0: {r['bottom_neg']*100:.0f}% | "
             f"TOP>0: {r['top_pos']*100:.0f}% (mirror BOTTOM>0 {r['bottom_pos']*100:.0f}% | "
             f"TOP<0 {r['top_neg']*100:.0f}%)\n")
    L.append("\n## Walk-forward (TOP−BOTTOM spread)\n")
    L.append("| Window | spread | min tercile n |\n|---|---|---|")
    for w in r["wf"]:
        L.append(f"| {w['window']} | {fmt_pct(w['spread'])} | {w['min_tercile_n']} |")
    L.append(f"\nwindows spread>0: {r['wf_pos']}/4 · spread<0: {r['wf_neg']}/4\n")
    L.append("\n## Gate scorecard\n")
    L.append("| Branch | A (spread) | B (monotonic) | C (reliability) | D (walk-fwd) | E (adequacy) |\n|---|---|---|---|---|---|")
    L.append(f"| Persistence | {pe['A']} | {pe['B']} | {pe['C']} | {pe['D']} | {r['e_adequate']} |")
    L.append(f"| Reversion | {re_['A']} | {re_['B']} | {re_['C']} | {re_['D']} | {r['e_adequate']} |")
    L.append(f"\n## XLV as-of read ({xlv['asof']})\n")
    L.append(f"XLV tercile **{xlv['xlv_tercile']}** (rank {xlv['xlv_rank']}/{xlv['n_sectors']}, 1=weakest). "
             f"{xlv['implication']}\n")
    L.append(f"\nArtifact: `data/profile/sector_rs_persistence.parquet` "
             f"(per-observation date/sector/rs_trail/tercile/rs_fwd).\n")
    report.write_text("\n".join(L))
    print(f"  Wrote {out_parquet.relative_to(ROOT)} and {report.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
