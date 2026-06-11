#!/usr/bin/env python3.11
"""Chaikin Money Flow (CMF-50) on RSP × RSP 200-DMA — does the pair predict the
broad-market trend? (DESCRIPTIVE / exploratory — NOT a gate, NOT an edge claim.)

User question (2026-06-11): set CMF length to 50 on RSP to cut noise / read trend;
pair it with RSP's 200-DMA and test how well the combination predicts the trend,
measured across the 150-name cohort.

Design (confirmed with user):
  • Signal is MARKET-LEVEL, computed on RSP only:
      - trend leg : RSP adj-close ≥ 200-DMA  → "up"   ; else "down"
      - flow  leg : CMF-50 > +BAND → "in" ; < −BAND → "out" ; else "neutral"
        (primary analysis collapses neutral away by using sign at BAND=0; a ±0.05
         banded 2×3 is reported as a robustness view)
  • Target is the EQUAL-WEIGHTED forward return of the 150-name cohort at
    21 / 42 / 63 trading days (broad-trend proxy); RSP's own fwd return is also
    reported for reference.
  • CONTROL: the 200-DMA-alone baseline (fwd return by trend leg, ignoring CMF).
    The question of interest is whether CMF adds *incremental* separation ON TOP
    of the 200-DMA we already use — i.e. within each trend leg, does CMF-in beat
    CMF-out, and is that lift the same SIGN in train (≤2019) and validation (≥2020)?

Honest caveats (printed with the results):
  • Overlapping forward windows → autocorrelated observations; a non-overlapping
    subsample (every h-th day) is reported so the separation isn't read off
    inflated N. No t-stat is presented as if independent.
  • Survivorship: universe_v1 is the *current* 150 names carried back in time. This
    inflates absolute forward returns, but applies across all regime cells, so the
    *relative* comparison (which the study is about) is largely unaffected. Flagged.
  • CMF length 50 and the 2×2 cell definition are a-priori (user-chosen / pre-set),
    not optimized to the outcome — this keeps the read honest.

Usage: python3.11 scripts/research/cmf_rsp_trend_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

UNIVERSE = ROOT / "data/profile/universe_v1.parquet"
OUT = ROOT / "data/profile/cmf_rsp_trend_study.parquet"

CMF_WIN = int(sys.argv[1]) if len(sys.argv) > 1 else 50   # a-priori length (arg overrides)
MA_WIN = 200                  # RSP trend filter
HORIZONS = (21, 42, 63)       # forward trading days (~1, 2, 3 months)
BAND = 0.05                   # ±band for the 2×3 robustness view
TRAIN_END = pd.Timestamp("2019-12-31")   # walk-forward split


def cmf(df: pd.DataFrame, win: int = CMF_WIN) -> pd.Series:
    """Chaikin Money Flow on a single-ticker OHLCV frame (same formula as
    scripts/research/zebra_moneyflow_scan.py)."""
    hi, lo, cl, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (hi - lo).replace(0, np.nan)
    mfm = ((cl - lo) - (hi - cl)) / rng        # money-flow multiplier, [-1, +1]
    mfv = mfm.fillna(0) * vol                   # money-flow volume
    return mfv.rolling(win).sum() / vol.rolling(win).sum()


def fetch_rsp() -> pd.DataFrame:
    """RSP daily OHLCV (raw for CMF) + adjusted close (for trend & returns)."""
    import yfinance as yf
    df = yf.download("RSP", period="max", interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise SystemExit("RSP fetch failed")
    # flatten any multiindex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={"Adj Close": "AdjClose"})
    return df[["Open", "High", "Low", "Close", "AdjClose", "Volume"]].dropna()


def fetch_cohort_closes() -> pd.DataFrame:
    """Adjusted daily closes for the 150 universe_v1 names (wide: date × ticker)."""
    import yfinance as yf
    tickers = pd.read_parquet(UNIVERSE)["ticker"].tolist()
    px = yf.download(tickers, period="max", interval="1d",
                     auto_adjust=True, progress=False, threads=True)["Close"]
    if isinstance(px, pd.Series):
        px = px.to_frame()
    return px.sort_index()


def cohort_forward_returns(closes: pd.DataFrame) -> dict:
    """Equal-weighted cohort forward return at each horizon: mean over available
    names of close(t+h)/close(t)−1. Returns {h: Series indexed by date}."""
    out = {}
    for h in HORIZONS:
        fwd = closes.shift(-h) / closes - 1.0        # per-name fwd return
        out[h] = fwd.mean(axis=1, skipna=True)       # equal-weight across names
    return out


def build_panel() -> pd.DataFrame:
    rsp = fetch_rsp()
    rsp["cmf"] = cmf(rsp)
    rsp["ma200"] = rsp["AdjClose"].rolling(MA_WIN).mean()
    panel = pd.DataFrame(index=rsp.index)
    panel["cmf"] = rsp["cmf"]
    panel["trend"] = np.where(rsp["AdjClose"] >= rsp["ma200"], "up", "down")
    panel["flow_sign"] = np.where(rsp["cmf"] >= 0, "in", "out")
    panel["flow_band"] = np.where(rsp["cmf"] > BAND, "in",
                          np.where(rsp["cmf"] < -BAND, "out", "neutral"))
    # RSP's own forward returns (reference)
    for h in HORIZONS:
        panel[f"rsp_fwd_{h}"] = rsp["AdjClose"].shift(-h) / rsp["AdjClose"] - 1.0

    closes = fetch_cohort_closes()
    coh = cohort_forward_returns(closes)
    for h in HORIZONS:
        panel[f"coh_fwd_{h}"] = coh[h].reindex(panel.index)
        panel[f"n_names_{h}"] = (closes.shift(-h) / closes - 1.0)\
            .reindex(panel.index).notna().sum(axis=1)

    panel = panel.dropna(subset=["cmf", "trend"])
    panel["window"] = np.where(panel.index <= TRAIN_END, "train", "val")
    return panel


# ─── reporting ────────────────────────────────────────────────────────────────

def _cell_stats(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) == 0:
        return {"n": 0, "mean": np.nan, "median": np.nan, "hit": np.nan}
    return {"n": int(len(s)), "mean": float(s.mean()),
            "median": float(s.median()), "hit": float((s > 0).mean())}


def _pct(x): return "  n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:+6.2f}%"


def report(panel: pd.DataFrame, target_prefix: str, target_label: str) -> None:
    print(f"\n{'='*92}\nTARGET: {target_label} forward return — by RSP regime cell\n{'='*92}")
    for h in HORIZONS:
        col = f"{target_prefix}_fwd_{h}"
        print(f"\n── horizon {h} trading days "
              f"{'(~%d cal mo)' % round(h/21)} {'-'*40}")
        # 200-DMA-alone control (trend leg only)
        print(f"  CONTROL (200-DMA alone):")
        for tr in ("up", "down"):
            st = _cell_stats(panel.loc[panel.trend == tr, col])
            print(f"    trend={tr:<4}            n={st['n']:>5}  "
                  f"mean={_pct(st['mean'])}  median={_pct(st['median'])}  hit={_pct(st['hit'])}")
        # 2×2 with CMF sign
        print(f"  + CMF-50 sign (incremental):")
        lifts = {}
        for tr in ("up", "down"):
            cells = {}
            for fl in ("in", "out"):
                st = _cell_stats(panel.loc[(panel.trend == tr) & (panel.flow_sign == fl), col])
                cells[fl] = st
                print(f"    trend={tr:<4} cmf={fl:<3}   n={st['n']:>5}  "
                      f"mean={_pct(st['mean'])}  median={_pct(st['median'])}  hit={_pct(st['hit'])}")
            if not np.isnan(cells["in"]["mean"]) and not np.isnan(cells["out"]["mean"]):
                lifts[tr] = cells["in"]["mean"] - cells["out"]["mean"]
                print(f"      → CMF marginal lift (in − out) within {tr}: {_pct(lifts[tr])}")
        # walk-forward sign stability of the lift
        print(f"  WALK-FORWARD sign-stability of CMF lift (train ≤2019 / val ≥2020):")
        for tr in ("up", "down"):
            row = []
            for win in ("train", "val"):
                sub = panel[(panel.window == win) & (panel.trend == tr)]
                mi = _cell_stats(sub.loc[sub.flow_sign == "in", col])["mean"]
                mo = _cell_stats(sub.loc[sub.flow_sign == "out", col])["mean"]
                lift = (mi - mo) if (not np.isnan(mi) and not np.isnan(mo)) else np.nan
                row.append((win, lift))
            stable = (not any(np.isnan(l) for _, l in row)
                      and np.sign(row[0][1]) == np.sign(row[1][1]) and row[0][1] != 0)
            txt = "  ".join(f"{w}={_pct(l)}" for w, l in row)
            print(f"    trend={tr:<4}  {txt}   {'✓ same sign' if stable else '✗ flips / n/a'}")
        # continuous IC + non-overlapping robustness
        ic = panel[["cmf", col]].dropna().corr(method="spearman").iloc[0, 1]
        nonov = panel.iloc[::h]          # every h-th day → ~non-overlapping
        ic_no = nonov[["cmf", col]].dropna().corr(method="spearman").iloc[0, 1]
        within = {}
        for tr in ("up", "down"):
            d = panel.loc[panel.trend == tr, ["cmf", col]].dropna()
            within[tr] = d.corr(method="spearman").iloc[0, 1] if len(d) > 30 else np.nan
        print(f"  IC (Spearman cmf vs fwd): overall={ic:+.3f}  "
              f"non-overlap={ic_no:+.3f}  | within up={within['up']:+.3f}  within down={within['down']:+.3f}")


def main() -> int:
    print("CMF-50(RSP) × RSP 200-DMA → broad-trend prediction  [DESCRIPTIVE / exploratory]")
    print(f"CMF window={CMF_WIN}  MA={MA_WIN}  horizons={HORIZONS}  split={TRAIN_END.date()}")
    panel = build_panel()
    span = f"{panel.index.min().date()} → {panel.index.max().date()}"
    print(f"panel: {len(panel)} trading days  ({span})  "
          f"train={int((panel.window=='train').sum())}  val={int((panel.window=='val').sum())}")
    # regime occupancy
    occ = panel.groupby(["trend", "flow_sign"]).size()
    print("regime occupancy (days):")
    for (tr, fl), n in occ.items():
        print(f"  trend={tr:<4} cmf={fl:<3}  {n:>5}  ({n/len(panel)*100:4.1f}%)")

    report(panel, "coh", "150-name COHORT (equal-weight)")
    report(panel, "rsp", "RSP (reference)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT)
    print(f"\nsaved panel → {OUT.relative_to(ROOT)}")
    print("\nCAVEATS: overlapping fwd windows (autocorrelated — see non-overlap IC); "
          "survivorship in universe_v1 (relative comparison robust, absolute inflated); "
          "descriptive only — pre-register before this gates anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
