#!/usr/bin/env python3.11
"""Option open-interest VELOCITY on SPY — does the speed of positioning carry
information that price does not? (DESCRIPTIVE / exploratory — NOT a gate.)

Premise (from the CMF post-mortem): Chaikin Money Flow failed because it is
direction×volume → it re-expresses price (coincident, not predictive). Open
interest is different: OI counts contracts outstanding and can build or unwind
in EITHER price direction, so its *velocity* (rate of positioning change) may be
orthogonal to spot. This study tests that premise, then asks whether OI velocity
predicts anything — guided by the honest prior that participation/positioning
metrics forecast VOLATILITY / regime transitions far more reliably than RETURN
DIRECTION.

Signal (SPY, near-term chain ≤90 DTE — where active positioning lives), a-priori:
  • total_oi, call_oi, put_oi, pc_oi = put_oi/call_oi, churn = total_vol/total_oi
  • oi_vel = log(total_oi / total_oi[t−W])   — intensity: positioning accelerating?
  • pc_vel = log(pc_oi   / pc_oi[t−W])        — direction: puts building vs calls?
  (W = 5 trading days default; arg overrides. z-scored over trailing 252d for terciles.)

Tests (a-priori; walk-forward train ≤2019 / val ≥2020):
  ORTHOGONALITY — corr(signal, contemporaneous & trailing SPY return). The premise
    requires this to be LOW (else it's another price proxy, like CMF).
  H_A intensity→vol — does oi_vel predict forward realized vol (21d)? Control: does
    it add beyond current SPY IV-rank (which already prices expected vol)?
  H_B positioning→return/drawdown — does pc_vel (puts building) predict lower forward
    return / deeper forward drawdown (a positioning early-warning)?
  (oi_vel vs forward RETURN is also reported — expected null per the prior.)

Caveats printed with results: SPY only (market-level first cut; per-name is a
follow-up); overlapping forward windows (autocorrelated — non-overlap IC shown);
SPY has no splits in-sample so OI is clean (per-name OI velocity would need
split-date handling); descriptive only — pre-register before this gates anything.

Usage: python3.11 scripts/research/oi_velocity_study.py [W]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

SPY_ORATS = ROOT / "data/orats/by_ticker/SPY.parquet"
ATM_IV = ROOT / "data/profile/atm_iv_series.parquet"
OUT = ROOT / "data/profile/oi_velocity_study.parquet"

W = int(sys.argv[1]) if len(sys.argv) > 1 else 5     # velocity lookback (trading days)
DTE_MAX_YR = 0.25                                    # near-term chain (≤~90 DTE)
ZWIN = 252                                           # trailing window for z-score
HORIZONS = (21, 42, 63)
TRAIN_END = pd.Timestamp("2019-12-31")


def daily_oi() -> pd.DataFrame:
    df = pd.read_parquet(SPY_ORATS, columns=["yte", "cOi", "pOi", "cVolu", "pVolu", "trade_date"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df[df["yte"] <= DTE_MAX_YR]
    g = df.groupby("trade_date").agg(
        call_oi=("cOi", "sum"), put_oi=("pOi", "sum"),
        call_vol=("cVolu", "sum"), put_vol=("pVolu", "sum")).sort_index()
    g["total_oi"] = g["call_oi"] + g["put_oi"]
    g["total_vol"] = g["call_vol"] + g["put_vol"]
    g["pc_oi"] = g["put_oi"] / g["call_oi"]
    g["churn"] = g["total_vol"] / g["total_oi"]
    g["oi_vel"] = np.log(g["total_oi"] / g["total_oi"].shift(W))
    g["pc_vel"] = np.log(g["pc_oi"] / g["pc_oi"].shift(W))
    for c in ("oi_vel", "pc_vel", "churn"):
        m = g[c].rolling(ZWIN).mean()
        s = g[c].rolling(ZWIN).std()
        g[f"{c}_z"] = (g[c] - m) / s
    return g


def spy_targets(index) -> pd.DataFrame:
    import yfinance as yf
    spy = yf.download("SPY", period="max", interval="1d", auto_adjust=True, progress=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    px = spy["Close"].dropna()
    lr = np.log(px / px.shift(1))
    out = pd.DataFrame(index=px.index)
    out["ret_1"] = px / px.shift(1) - 1.0                      # contemporaneous (today)
    out["ret_tw"] = px / px.shift(W) - 1.0                     # trailing W-day return
    for h in HORIZONS:
        out[f"fwd_ret_{h}"] = px.shift(-h) / px - 1.0
        # forward realized vol (annualized) over next h days
        out[f"fwd_rv_{h}"] = lr.shift(-h).rolling(h).std().shift(-(0)) * np.sqrt(252)
        # forward max drawdown over next h days
        fwd_min = px.shift(-1).rolling(h).apply(lambda w: (w / np.maximum.accumulate(w) - 1).min(), raw=True)
        out[f"fwd_maxdd_{h}"] = fwd_min.shift(-(h - 1))
    return out.reindex(index)


def _ic(d: pd.DataFrame, a: str, b: str) -> float:
    dd = d[[a, b]].dropna()
    return dd.corr(method="spearman").iloc[0, 1] if len(dd) > 30 else np.nan


def _tercile_table(panel, sig, tgt, label):
    d = panel[[sig, tgt, "window"]].dropna()
    if len(d) < 60:
        print(f"    {label}: insufficient n"); return
    q = d[sig].quantile([1/3, 2/3]).values
    d = d.assign(tb=np.where(d[sig] <= q[0], "low", np.where(d[sig] >= q[1], "high", "mid")))
    print(f"    {label}: mean {tgt} by {sig} tercile")
    for tb in ("low", "mid", "high"):
        s = d.loc[d.tb == tb, tgt]
        print(f"      {tb:<4} n={len(s):>4}  mean={s.mean()*100:+6.2f}%  median={s.median()*100:+6.2f}%")
    hi = d.loc[d.tb == "high", tgt].mean(); lo = d.loc[d.tb == "low", tgt].mean()
    # walk-forward sign of high−low spread
    row = []
    for win in ("train", "val"):
        sub = d[d.window == win]
        h = sub.loc[sub.tb == "high", tgt].mean(); l = sub.loc[sub.tb == "low", tgt].mean()
        row.append((win, h - l))
    stable = (not any(np.isnan(x) for _, x in row)) and np.sign(row[0][1]) == np.sign(row[1][1])
    print(f"      → high−low spread = {(hi-lo)*100:+.2f}%  | WF "
          f"{'  '.join(f'{w}={d_*100:+.2f}%' for w,d_ in row)}  {'✓ same sign' if stable else '✗ flips'}")


def main() -> int:
    print(f"SPY option-OI VELOCITY study  [DESCRIPTIVE / exploratory]")
    print(f"W={W}td  near-term≤{DTE_MAX_YR}yr  z-win={ZWIN}  horizons={HORIZONS}  split={TRAIN_END.date()}")
    g = daily_oi()
    tg = spy_targets(g.index)
    panel = g.join(tg)
    panel = panel.dropna(subset=["oi_vel", "pc_vel"])
    panel["window"] = np.where(panel.index <= TRAIN_END, "train", "val")
    print(f"panel: {len(panel)} days ({panel.index.min().date()} → {panel.index.max().date()})  "
          f"train={int((panel.window=='train').sum())} val={int((panel.window=='val').sum())}")

    # ── ORTHOGONALITY: is OI velocity decoupled from price? (the whole premise) ──
    print(f"\n{'='*80}\nORTHOGONALITY — is OI velocity decoupled from price? (premise check)\n{'='*80}")
    for sig in ("oi_vel", "pc_vel", "churn_z"):
        c_now = _ic(panel, sig, "ret_1")
        c_tw = _ic(panel, sig, "ret_tw")
        verdict = "DECOUPLED ✓" if abs(c_tw) < 0.30 else "price-coupled ✗"
        print(f"  {sig:<8} corr vs same-day ret={c_now:+.3f}  vs trailing-{W}d ret={c_tw:+.3f}   {verdict}")
    print("  (CMF's trailing-return corr was effectively ~1 by construction; we want these near 0.)")

    # ── H_A: intensity (oi_vel) → forward realized vol; control vs IV-rank ──
    print(f"\n{'='*80}\nH_A — does OI-velocity predict forward VOLATILITY? (the prior's best bet)\n{'='*80}")
    iv = pd.read_parquet(ATM_IV)
    iv = iv[iv["ticker"] == "SPY"][["trade_date", "iv_rank"]].copy()
    iv["trade_date"] = pd.to_datetime(iv["trade_date"])
    panel["iv_rank"] = iv.set_index("trade_date")["iv_rank"].reindex(panel.index, method="ffill")
    for h in HORIZONS:
        ic = _ic(panel, "oi_vel", f"fwd_rv_{h}")
        nonov = panel.iloc[::h]
        ic_no = _ic(nonov, "oi_vel", f"fwd_rv_{h}")
        ic_iv = _ic(panel, "iv_rank", f"fwd_rv_{h}")
        print(f"  fwd_rv_{h}:  IC(oi_vel)={ic:+.3f}  non-overlap={ic_no:+.3f}  | "
              f"control IC(iv_rank)={ic_iv:+.3f}")
    print("  terciles (forward 21d realized vol by OI-velocity):")
    _tercile_table(panel, "oi_vel", "fwd_rv_21", "oi_vel → fwd_rv_21")

    # ── H_B: positioning (pc_vel) → forward return & drawdown ──
    print(f"\n{'='*80}\nH_B — does put/call-OI velocity predict forward RETURN / DRAWDOWN?\n{'='*80}")
    for h in HORIZONS:
        icr = _ic(panel, "pc_vel", f"fwd_ret_{h}")
        icd = _ic(panel, "pc_vel", f"fwd_maxdd_{h}")
        print(f"  h={h}:  IC(pc_vel, fwd_ret)={icr:+.3f}   IC(pc_vel, fwd_maxdd)={icd:+.3f}")
    print("  terciles (forward 42d return by put/call-OI velocity — high = puts building fastest):")
    _tercile_table(panel, "pc_vel", "fwd_ret_42", "pc_vel → fwd_ret_42")
    print("  terciles (forward 42d max drawdown by put/call-OI velocity):")
    _tercile_table(panel, "pc_vel", "fwd_maxdd_42", "pc_vel → fwd_maxdd_42")

    # ── control: does intensity predict DIRECTION? (expected null) ──
    print(f"\n{'='*80}\nCONTROL — OI-velocity vs forward RETURN (expected ~null per the prior)\n{'='*80}")
    for h in HORIZONS:
        print(f"  h={h}:  IC(oi_vel, fwd_ret)={_ic(panel,'oi_vel',f'fwd_ret_{h}'):+.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT)
    print(f"\nsaved → {OUT.relative_to(ROOT)}")
    print("CAVEATS: SPY only (per-name = follow-up); overlapping fwd windows (see non-overlap IC); "
          "descriptive/exploratory — pre-register before gating.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
