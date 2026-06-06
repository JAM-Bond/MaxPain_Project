#!/usr/bin/env python3.11
"""
Macro-State → GICS Sector Rotation — study.
Implements docs/SECTOR_ROTATION_MACRO_PREREG.md (sealed 2026-06-04).

Does the macro state predict next-month sector-ETF EXCESS return vs SPY?
Monthly, 13 ETFs (11 SPDR sectors + GLD + SLV). Fit ≤2019-12, forward-test 2020+.
Two methods: (A) 8-regime conditional means, (B) per-sector OLS. Sealed OOS gates.

Usage: python3.11 -m scripts.backtest.sector_rotation_macro_study
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from lib.adjusted_close import load_adjusted_close  # noqa: E402

ETFS = ["XLE", "XLF", "XLK", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB",
        "XLRE", "XLC", "GLD", "SLV"]
TRAIN_END = pd.Timestamp("2019-12-31")
FRED = ROOT / "data/macro/fred_daily_13y.parquet"
OUT = ROOT / "data/profile/sector_rotation_macro_study.parquet"

# Sealed OOS gates
GATE_A_RANKCORR = 0.15
GATE_B_HITRATE = 0.55
GATE_C_SPREAD = 0.30 / 100.0      # %/mo → fraction
GATE_C_POSFRAC = 0.55

# Method-B sealed 6-feature subset
B_FEATURES = ["d3_DGS10", "d3_T10YIE", "VIXCLS", "d3_credit", "d3_DTWEXBGS", "oil_3m"]


def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ─── Data ─────────────────────────────────────────────────────────────

def monthly_excess() -> pd.DataFrame:
    """Month-end excess returns vs SPY for the 13 ETFs."""
    px = {}
    for t in ETFS + ["SPY"]:
        s = load_adjusted_close(t).dropna()
        s.index = pd.to_datetime(s.index)
        px[t] = s.resample("ME").last()
    px = pd.DataFrame(px)
    rets = px.pct_change()
    excess = rets[ETFS].sub(rets["SPY"], axis=0)
    return excess


def macro_features() -> pd.DataFrame:
    """Month-end macro feature panel from the deep FRED store."""
    f = pd.read_parquet(FRED)
    f["date"] = pd.to_datetime(f["date"])
    w = f.pivot_table(index="date", columns="series_id", values="value").sort_index().ffill()
    m = w.resample("ME").last()

    feat = pd.DataFrame(index=m.index)
    feat["DGS10"] = m["DGS10"]
    feat["d3_DGS10"] = m["DGS10"].diff(3)
    feat["T10Y2Y"] = m["T10Y2Y"]
    feat["d3_T10Y2Y"] = m["T10Y2Y"].diff(3)
    feat["T10YIE"] = m["T10YIE"]
    feat["d3_T10YIE"] = m["T10YIE"].diff(3)
    feat["cpi_yoy"] = m["CPIAUCSL"].pct_change(12)
    feat["core_cpi_yoy"] = m["CPILFESL"].pct_change(12)
    feat["food_yoy"] = m["CPIUFDSL"].pct_change(12)
    feat["oil_3m"] = m["DCOILWTICO"].pct_change(3)
    feat["gas_3m"] = m["GASREGW"].pct_change(3)
    feat["d3_DTWEXBGS"] = m["DTWEXBGS"].diff(3)
    feat["VIXCLS"] = m["VIXCLS"]
    feat["credit"] = m["DBAA"] - m["DAAA"]
    feat["d3_credit"] = (m["DBAA"] - m["DAAA"]).diff(3)
    feat["NFCI"] = m.get("NFCI")
    feat["UNRATE"] = m["UNRATE"]
    # Sahm gap (3mo-avg unemployment minus trailing-12mo low of that avg)
    ur3 = m["UNRATE"].rolling(3).mean()
    feat["sahm_gap"] = ur3 - ur3.rolling(12, min_periods=12).min()
    # Estrella-Mishkin probit from 3m10y
    spread_3m10y = m["DGS10"] - m["DTB3"]
    feat["em_probit"] = (-0.6045 - 0.7374 * spread_3m10y).apply(lambda z: _norm_cdf(z) * 100)
    # Near-term forward spread (interp 1.5y/1.75y from 1y/2y; fwd 3m − cur 3m)
    y15 = m["DTB1YR"] + (m["DGS2"] - m["DTB1YR"]) * 0.5
    y175 = m["DTB1YR"] + (m["DGS2"] - m["DTB1YR"]) * 0.75
    feat["ntfs"] = (y175 * 1.75 - y15 * 1.5) / 0.25 - m["DTB3"]
    return feat


# ─── Regime label (Method A) ──────────────────────────────────────────

def regime_label(feat: pd.DataFrame) -> pd.Series:
    rate = np.sign(feat["d3_DGS10"]).map({1: "R+", -1: "R-", 0: "R0"})
    infl = np.sign(feat["d3_T10YIE"]).map({1: "I+", -1: "I-", 0: "I0"})
    vix_med = feat["VIXCLS"].rolling(12, min_periods=6).median()
    risk = (feat["VIXCLS"] > vix_med).map({True: "Voff", False: "Von"})
    return rate.fillna("R0") + "|" + infl.fillna("I0") + "|" + risk.fillna("Von")


# ─── Metrics ──────────────────────────────────────────────────────────

def _spearman(pred: pd.Series, real: pd.Series) -> float:
    df = pd.concat([pred, real], axis=1).dropna()
    if len(df) < 3:
        return np.nan
    ra, rb = df.iloc[:, 0].rank(), df.iloc[:, 1].rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate(pred_by_month: dict, real_next: pd.DataFrame, test_months) -> dict:
    """pred_by_month: {month_ts: Series(ETF→predicted excess)}. real_next:
    DataFrame index month t, value = excess at t+1."""
    rcs, hits, spreads = [], [], []
    for t in test_months:
        if t not in pred_by_month or t not in real_next.index:
            continue
        pred = pred_by_month[t].dropna()
        real = real_next.loc[t].dropna()
        common = pred.index.intersection(real.index)
        if len(common) < 4:
            continue
        pred, real = pred[common], real[common]
        rc = _spearman(pred, real)
        if rc == rc:
            rcs.append(rc)
        # top-pick beats SPY?
        top = pred.idxmax()
        hits.append(1 if real[top] > 0 else 0)
        # tercile spread
        n = len(pred)
        k = max(1, n // 3)
        order = pred.sort_values(ascending=False).index
        top_k, bot_k = order[:k], order[-k:]
        spreads.append(real[top_k].mean() - real[bot_k].mean())
    rcs, hits, spreads = np.array(rcs), np.array(hits), np.array(spreads)
    return {
        "n_months": int(len(spreads)),
        "mean_rankcorr": float(np.nanmean(rcs)) if len(rcs) else float("nan"),
        "top_hitrate": float(hits.mean()) if len(hits) else float("nan"),
        "mean_spread": float(np.nanmean(spreads)) if len(spreads) else float("nan"),
        "spread_posfrac": float((spreads > 0).mean()) if len(spreads) else float("nan"),
    }


def gates(m: dict) -> dict:
    gA = m["mean_rankcorr"] >= GATE_A_RANKCORR if m["mean_rankcorr"] == m["mean_rankcorr"] else False
    gB = m["top_hitrate"] >= GATE_B_HITRATE if m["top_hitrate"] == m["top_hitrate"] else False
    gC = (m["mean_spread"] >= GATE_C_SPREAD and m["spread_posfrac"] >= GATE_C_POSFRAC)
    return {"A": bool(gA), "B": bool(gB), "C": bool(gC),
            "PASS": bool(gA and (gB or gC))}


# ─── Methods ──────────────────────────────────────────────────────────

def method_a(feat, regime, real_next, train_months, test_months) -> dict:
    """Regime-conditional training means → OOS predicted ranking."""
    # training conditional means per regime
    train_excess = real_next.loc[real_next.index.isin(train_months)]
    train_reg = regime.loc[train_excess.index]
    means = {}
    for r, idx in train_reg.groupby(train_reg).groups.items():
        means[r] = train_excess.loc[idx].mean()  # Series ETF→mean
    overall = train_excess.mean()
    pred = {}
    seen, unseen = 0, 0
    for t in test_months:
        r = regime.get(t)
        if r in means:
            pred[t] = means[r]; seen += 1
        else:
            pred[t] = overall; unseen += 1
    m = evaluate(pred, real_next, test_months)
    m.update({"regimes_in_train": len(means), "test_seen": seen, "test_unseen_fallback": unseen})
    return m


def method_b(feat, real_next, train_months, test_months) -> dict:
    """Per-sector OLS on the sealed feature subset."""
    X = feat[B_FEATURES]
    pred = {t: {} for t in test_months}
    for etf in ETFS:
        y = real_next[etf]
        tr = [d for d in train_months if d in X.index and d in y.index
              and X.loc[d].notna().all() and pd.notna(y.loc[d])]
        if len(tr) < 30:
            continue
        Xtr = np.column_stack([np.ones(len(tr))] + [X.loc[tr, c].values for c in B_FEATURES])
        ytr = y.loc[tr].values
        beta, *_ = np.linalg.lstsq(Xtr, ytr, rcond=None)
        for t in test_months:
            if t in X.index and X.loc[t].notna().all():
                xv = np.concatenate([[1.0], X.loc[t, B_FEATURES].values])
                pred[t][etf] = float(xv @ beta)
    pred = {t: pd.Series(v) for t, v in pred.items() if v}
    return evaluate(pred, real_next, test_months)


# ─── Main ─────────────────────────────────────────────────────────────

def main() -> int:
    excess = monthly_excess()
    feat = macro_features()
    idx = excess.index.intersection(feat.index)
    excess, feat = excess.loc[idx], feat.loc[idx]
    regime = regime_label(feat)
    real_next = excess.shift(-1).iloc[:-1]   # at t: excess at t+1
    months = real_next.index
    train_months = [d for d in months if d <= TRAIN_END]
    test_months = [d for d in months if d > TRAIN_END]

    print(f"Sector-rotation macro study — {len(ETFS)} ETFs, monthly")
    print(f"  span {months.min().date()}→{months.max().date()}  |  "
          f"train≤2019: {len(train_months)} mo  |  forward-test 2020+: {len(test_months)} mo")
    print("=" * 74)

    A = method_a(feat, regime, real_next, train_months, test_months)
    gA = gates(A)
    print("\nMETHOD A — 8-regime conditional means (rate_dir × infl_dir × risk)")
    print(f"  regimes seen in train: {A['regimes_in_train']}/8  |  "
          f"test months on a seen regime: {A['test_seen']}, fallback: {A['test_unseen_fallback']}")
    print(f"  OOS rank-corr {A['mean_rankcorr']:+.3f} (Gate A≥{GATE_A_RANKCORR}: {gA['A']})  |  "
          f"top-pick hit {A['top_hitrate']:.1%} (Gate B≥{GATE_B_HITRATE:.0%}: {gA['B']})")
    print(f"  tercile spread {A['mean_spread']*100:+.3f}%/mo, pos {A['spread_posfrac']:.0%} "
          f"(Gate C: {gA['C']})  →  PASS={gA['PASS']}")

    B = method_b(feat, real_next, train_months, test_months)
    gB = gates(B)
    print("\nMETHOD B — per-sector OLS on {Δ3m rates, Δ3m infl, VIX, Δ3m credit, Δ3m $, oil 3m}")
    print(f"  OOS rank-corr {B['mean_rankcorr']:+.3f} (Gate A≥{GATE_A_RANKCORR}: {gB['A']})  |  "
          f"top-pick hit {B['top_hitrate']:.1%} (Gate B≥{GATE_B_HITRATE:.0%}: {gB['B']})")
    print(f"  tercile spread {B['mean_spread']*100:+.3f}%/mo, pos {B['spread_posfrac']:.0%} "
          f"(Gate C: {gB['C']})  →  PASS={gB['PASS']}")

    overall_pass = gA["PASS"] or gB["PASS"]
    print("\n" + "=" * 74)
    print(f"STUDY VERDICT: {'PASS — promote to overlay (see pre-reg)' if overall_pass else 'NULL (terminal — adequate OOS N)'}")
    print("  (selection-edge hunt; holdout spans COVID + 2022 shock + AI boom — "
          "non-stationary by design)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"method": "A_regime", **A, **{f"gate_{k}": v for k, v in gA.items()}},
                  {"method": "B_ols", **B, **{f"gate_{k}": v for k, v in gB.items()}}]
                 ).to_parquet(OUT, index=False)
    print(f"\nWrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
