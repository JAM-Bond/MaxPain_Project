#!/usr/bin/env python3.11
"""
ETF Money-Flow → GICS Sector Rotation — study.
Implements docs/SECTOR_FLOW_ROTATION_PREREG.md (sealed 2026-06-05).

Does creation/redemption-derived net ETF flow predict next-month sector-ETF
EXCESS return vs SPY? Three sealed hypotheses on one expanding-window walk-forward:
  H1 flow momentum      (predicted excess ∝ +FLOW3)
  H2 price-flow diverge (predicted excess ∝ -[z(PRICE3) - z(FLOW3)])
  H3 flow→fwd-excess    (expanding-window pooled OLS, slope fit OOS)
Shared metric battery + sealed gates A/B/C. ~180 OOS months (2011-06→2026-05).

Usage: python3.11 -m scripts.backtest.sector_flow_rotation_study
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
           "XLP", "XLRE", "XLU", "XLV", "XLY"]
FLOWS = ROOT / "data/flows/sector_flows_monthly.parquet"
OUT = ROOT / "data/profile/sector_flow_rotation_study.parquet"

WARMUP_MONTHS = 60                  # expanding-window warmup
OOS_START = pd.Timestamp("2011-06-30")
OOS_END = pd.Timestamp("2026-05-31")

# Sealed gates
GATE_A_RANKCORR = 0.08
GATE_B_SPREAD = 0.20 / 100.0        # %/mo → fraction
GATE_B_POSFRAC = 0.53
GATE_C_TSTAT = 2.0


# ─── Data ─────────────────────────────────────────────────────────────

def monthly_excess() -> pd.DataFrame:
    """Month-end total-return excess vs SPY for the 11 sectors (index=month-end)."""
    daily = yf.download(SECTORS + ["SPY"], start="2005-06-01", end="2026-06-05",
                        interval="1d", auto_adjust=True, progress=False)["Close"]
    m = daily.resample("ME").last()
    rets = m.pct_change()
    excess = rets[SECTORS].sub(rets["SPY"], axis=0)
    price3 = m[SECTORS].pct_change(3)          # trailing 3-month total return
    return excess, price3


SEASON_MONTHS = 12          # inception-ramp seasoning (drop each fund's first year)
FLOW_CAP = 0.30             # beyond-physical guard: |organic monthly flow| > 30% = artifact


def flow_signals() -> tuple[pd.DataFrame, pd.DataFrame]:
    """FLOW3 (3m cumulative organic flow%) and FLOW1 (1m), index=month-end.

    Data hygiene (artifacts, not signal tuning — see pre-reg):
      • splits already removed upstream in lib.ssga_flows.reconstruct_flows;
      • each fund's first SEASON_MONTHS months dropped (seeding ramp off a near-zero
        AUM base makes flow% meaningless — e.g. XLRE 2016, XLC 2018);
      • |monthly organic flow| > FLOW_CAP set to NaN (a fund cannot organically take
        in >30% of assets in a month; such values are restatements/glitches).
    """
    f = pd.read_parquet(FLOWS)
    f["date"] = pd.to_datetime(f["date"])
    flow = f.pivot_table(index="date", columns="ticker", values="flow")
    aum = f.pivot_table(index="date", columns="ticker", values="aum")
    flow = flow.reindex(columns=SECTORS).sort_index()
    aum = aum.reindex(columns=SECTORS).sort_index()
    # organic flow rate: flow_t / AUM_{t-1}
    flow_pct = flow / aum.shift(1)
    # inception seasoning: NaN each fund's first SEASON_MONTHS observed months
    for t in SECTORS:
        valid = aum[t].dropna()
        if len(valid):
            cutoff = valid.index[min(SEASON_MONTHS, len(valid) - 1)]
            flow_pct.loc[flow_pct.index < cutoff, t] = np.nan
    # beyond-physical guard
    n_capped = int((flow_pct.abs() > FLOW_CAP).sum().sum())
    flow_pct = flow_pct.where(flow_pct.abs() <= FLOW_CAP)
    flow1 = flow_pct
    flow3 = flow_pct.rolling(3, min_periods=3).sum()
    print(f"  hygiene: {n_capped} sector-months capped (|flow%|>{FLOW_CAP:.0%}); "
          f"first {SEASON_MONTHS}mo per fund seasoned out")
    return flow3, flow1


# ─── Metrics ──────────────────────────────────────────────────────────

def _spearman(pred: pd.Series, real: pd.Series) -> float:
    df = pd.concat([pred, real], axis=1).dropna()
    if len(df) < 3:
        return np.nan
    ra, rb = df.iloc[:, 0].rank(), df.iloc[:, 1].rank()
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def evaluate(pred_by_month: dict, real_next: pd.DataFrame, oos_months) -> dict:
    """pred_by_month: {month_ts: Series(sector→predicted excess)}.
    real_next: DataFrame at index t holding realized excess at t+1."""
    rcs, hits, spreads = [], [], []
    for t in oos_months:
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
        top = pred.idxmax()
        hits.append(1 if real[top] > 0 else 0)
        n = len(pred)
        k = max(1, n // 3)
        order = pred.sort_values(ascending=False).index
        spreads.append(real[order[:k]].mean() - real[order[-k:]].mean())
    rcs, hits, spreads = np.array(rcs), np.array(hits), np.array(spreads)
    tstat = (float(spreads.mean() / spreads.std(ddof=1) * np.sqrt(len(spreads)))
             if len(spreads) > 1 and spreads.std(ddof=1) > 0 else float("nan"))
    return {
        "n_months": int(len(spreads)),
        "mean_rankcorr": float(np.nanmean(rcs)) if len(rcs) else float("nan"),
        "top_hitrate": float(hits.mean()) if len(hits) else float("nan"),
        "mean_spread": float(np.nanmean(spreads)) if len(spreads) else float("nan"),
        "spread_posfrac": float((spreads > 0).mean()) if len(spreads) else float("nan"),
        "spread_tstat": tstat,
    }


def gates(m: dict) -> dict:
    gA = m["mean_rankcorr"] >= GATE_A_RANKCORR if m["mean_rankcorr"] == m["mean_rankcorr"] else False
    gB = (m["mean_spread"] >= GATE_B_SPREAD and m["spread_posfrac"] >= GATE_B_POSFRAC)
    gC = m["spread_tstat"] >= GATE_C_TSTAT if m["spread_tstat"] == m["spread_tstat"] else False
    return {"A": bool(gA), "B": bool(gB), "C": bool(gC),
            "PASS": bool(gA and gB and gC)}


# ─── Hypotheses ───────────────────────────────────────────────────────

def predict_h1(flow3, real_next, oos_months) -> dict:
    """Flow momentum: predicted excess ∝ +FLOW3 (cross-section, no fit)."""
    pred = {t: flow3.loc[t].dropna() for t in oos_months if t in flow3.index}
    return evaluate(pred, real_next, oos_months)


def predict_h2(flow3, price3, real_next, oos_months) -> dict:
    """Price-flow divergence: predicted excess ∝ -(z(PRICE3) - z(FLOW3))."""
    def z(s):
        s = s.dropna()
        return (s - s.mean()) / s.std(ddof=0) if len(s) > 1 and s.std(ddof=0) > 0 else s * 0.0
    pred = {}
    for t in oos_months:
        if t not in flow3.index or t not in price3.index:
            continue
        zf = z(flow3.loc[t])
        zp = z(price3.loc[t])
        common = zf.index.intersection(zp.index)
        if len(common) < 4:
            continue
        d = zp[common] - zf[common]
        pred[t] = -d                       # low divergence (accumulation) → top
    return evaluate(pred, real_next, oos_months)


def predict_h3(flow3, real_next, oos_months) -> dict:
    """Flow→fwd-excess: expanding-window pooled OLS, slope fit on s ≤ t-1."""
    months = sorted(real_next.index)
    pred = {}
    for t in oos_months:
        # pooled training pairs (FLOW3_s, Excess_{s+1}) for all s strictly before t
        xs, ys = [], []
        for s in months:
            if s >= t:
                break
            if s not in flow3.index:
                continue
            fv = flow3.loc[s].dropna()
            ev = real_next.loc[s].dropna()
            common = fv.index.intersection(ev.index)
            xs.extend(fv[common].values)
            ys.extend(ev[common].values)
        if len(xs) < 100:
            continue
        X = np.column_stack([np.ones(len(xs)), np.asarray(xs)])
        beta, *_ = np.linalg.lstsq(X, np.asarray(ys), rcond=None)
        fv_t = flow3.loc[t].dropna() if t in flow3.index else pd.Series(dtype=float)
        if len(fv_t) < 4:
            continue
        pred[t] = beta[0] + beta[1] * fv_t
    m = evaluate(pred, real_next, oos_months)
    return m


# ─── Main ─────────────────────────────────────────────────────────────

def main() -> int:
    excess, price3 = monthly_excess()
    flow3, flow1 = flow_signals()

    # realized t+1 excess at index t; trim incomplete trailing month
    real_next = excess.shift(-1).iloc[:-1]
    real_next = real_next[real_next.index <= OOS_END]

    idx = real_next.index.intersection(flow3.index)
    oos_months = [d for d in idx if OOS_START <= d <= OOS_END]

    # coverage report
    cov = flow3.loc[oos_months].notna().sum(axis=1)
    print(f"Sector flow-rotation study — {len(SECTORS)} SSGA sector SPDRs, monthly")
    print(f"  OOS span {oos_months[0].date()}→{oos_months[-1].date()}  |  "
          f"{len(oos_months)} months  |  cross-section {int(cov.min())}-{int(cov.max())} sectors")
    print("=" * 78)

    rows = []
    for name, m in [
        ("H1_flow_momentum", predict_h1(flow3, real_next, oos_months)),
        ("H2_price_flow_divergence", predict_h2(flow3, price3, real_next, oos_months)),
        ("H3_flow_fwd_excess_ols", predict_h3(flow3, real_next, oos_months)),
    ]:
        g = gates(m)
        rows.append({"hypothesis": name, **m, **{f"gate_{k}": v for k, v in g.items()}})
        title = {"H1_flow_momentum": "H1 — flow momentum (predicted ∝ +FLOW3)",
                 "H2_price_flow_divergence": "H2 — price–flow divergence (predicted ∝ −[z(P3)−z(F3)])",
                 "H3_flow_fwd_excess_ols": "H3 — flow→fwd-excess (expanding-window pooled OLS)"}[name]
        print(f"\n{title}")
        print(f"  n={m['n_months']}  rank-corr {m['mean_rankcorr']:+.3f} "
              f"(A≥{GATE_A_RANKCORR}: {g['A']})  |  top-pick {m['top_hitrate']:.1%}")
        print(f"  tercile spread {m['mean_spread']*100:+.3f}%/mo, pos {m['spread_posfrac']:.0%}, "
              f"t={m['spread_tstat']:+.2f}  (B:{g['B']} C:{g['C']})  →  PASS={g['PASS']}")

    overall = any(r["gate_PASS"] for r in rows)
    print("\n" + "=" * 78)
    print(f"STUDY VERDICT: {'PASS — promote to sector positioning overlay (see pre-reg)' if overall else 'NULL (terminal — adequate OOS N; flow-rotation → graveyard)'}")
    print("  (selection-edge hunt; holdout = EU crisis, taper, COVID, 2022 shock, AI boom)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(OUT, index=False)
    print(f"\nWrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
