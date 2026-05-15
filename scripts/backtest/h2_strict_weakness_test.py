"""H2 weakness — strict definitions.

CANSLIM RS rank alone fails under bull-extended SPY (h2_canslim_rs_test.py).
This script tests stricter definitions of "weak" that better capture
Burry's framing: names essentially crashing while the index rallies.

Variants:
  W1 absolute_negative_6m : trailing 6-month total return < 0 AND SPY bull-extended.
                            Burry-literal: name losing money while index rallies.
  W2 stage4_weinstein     : price below 30-week (150-day) MA AND MA sloping down
                            over the last 30 days. Weinstein's Stage 4 decline.
  W3 multi_filter         : RS bottom decile AND below own 200dma AND
                            distance from 52w high < -30%. Triple confirmation.
  W4 new_52w_lows_recent  : making a new 52-week low within last 20 trading days.
                            Hindenburg-style acceleration of weakness.

For each variant, on every bull-extended date:
  - Identify names matching the weakness criterion
  - Compute forward 75-day return per name
  - Compare to SPY baseline + universe baseline

Question: does any of these flip the median negative + show consistent
year-over-year underperformance vs SPY?
"""
from __future__ import annotations

import sys
from pathlib import Path
import logging

import pandas as pd
import numpy as np

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"

BULL_EXTENSION_THRESHOLD = 0.07
LOOKBACK_6M = 126
LOOKBACK_252 = 252
LOOKBACK_30W = 150       # 30-week MA = ~150 trading days
LOOKBACK_52W = 252
FORWARD_DAYS = 75
MIN_NAMES_PER_DATE = 50


def build_close_panel():
    closes = {}
    log = logging.getLogger("h2s")
    files = sorted(BY_TICKER.glob("*.parquet"))
    log.info("Loading %d ticker parquets...", len(files))
    for i, p in enumerate(files, 1):
        ticker = p.stem
        try:
            df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
        except Exception:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df.drop_duplicates(subset=["trade_date"], keep="first")
        s = df.set_index("trade_date")["stkPx"].astype(float)
        closes[ticker] = s
        if i % 50 == 0 or i == len(files):
            log.info("  loaded %d/%d", i, len(files))
    panel = pd.DataFrame(closes)
    panel.index = pd.to_datetime(panel.index)
    panel = panel.sort_index()
    return panel


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("h2s")
    panel = build_close_panel()
    log.info("Panel shape: %s", panel.shape)

    spy = panel["SPY"].dropna()
    spy_200dma = spy.rolling(200, min_periods=100).mean()
    spy_ext = spy / spy_200dma - 1.0
    bull_extended = spy_ext >= BULL_EXTENSION_THRESHOLD

    # Compute per-ticker metrics
    ret_6m = panel.pct_change(LOOKBACK_6M, fill_method=None)
    ret_252 = panel.pct_change(LOOKBACK_252, fill_method=None)
    ranks_252 = ret_252.rank(axis=1, pct=True)
    ma_200 = panel.rolling(200, min_periods=100).mean()
    ma_30w = panel.rolling(LOOKBACK_30W, min_periods=80).mean()
    ma_30w_slope = ma_30w - ma_30w.shift(30)  # 30-day change in 30wk MA
    rolling_52w_high = panel.rolling(LOOKBACK_52W, min_periods=120).max()
    rolling_52w_low = panel.rolling(LOOKBACK_52W, min_periods=120).min()
    dist_from_52w_high = panel / rolling_52w_high - 1.0
    new_52w_low_today = (panel <= rolling_52w_low + 1e-9)
    # New 52w low within last 20 days = rolling-or on the new_52w_low_today flag
    new_low_recent = new_52w_low_today.rolling(20, min_periods=1).max().fillna(0).astype(bool)

    fwd_ret = panel.pct_change(FORWARD_DAYS, fill_method=None).shift(-FORWARD_DAYS)
    spy_fwd = spy.pct_change(FORWARD_DAYS, fill_method=None).shift(-FORWARD_DAYS)

    bull_dates = bull_extended[bull_extended].index
    log.info("Bull-extended date count: %d", len(bull_dates))

    def evaluate(variant_name, mask_fn):
        """Run a weakness variant. mask_fn(date) returns a Series of booleans per ticker."""
        rows = []
        for dt in bull_dates:
            if dt not in panel.index:
                continue
            mask = mask_fn(dt)
            if mask is None:
                continue
            mask = mask.fillna(False)
            n_weak = int(mask.sum())
            if n_weak == 0:
                continue
            spy_f = spy_fwd.get(dt)
            if pd.isna(spy_f):
                continue
            if dt not in fwd_ret.index:
                continue
            date_fwd = fwd_ret.loc[dt]
            for ticker in mask[mask].index:
                fwd = date_fwd.get(ticker)
                if pd.isna(fwd):
                    continue
                rows.append({
                    "date": dt,
                    "ticker": ticker,
                    "fwd": fwd,
                    "excess_vs_spy": fwd - spy_f,
                    "year": dt.year,
                })
        if not rows:
            print(f"\n=== {variant_name}: NO observations matched ===\n")
            return
        odf = pd.DataFrame(rows)
        print(f"\n=== {variant_name} (bull-extended sample) ===")
        print(f"  N={len(odf):,}  unique dates={odf['date'].nunique():,}")
        print(f"  fwd 75d:      mean={odf['fwd'].mean():+.3%}  median={odf['fwd'].median():+.3%}  win={(odf['fwd']>0).mean():.1%}")
        print(f"  vs SPY:       mean={odf['excess_vs_spy'].mean():+.3%}  median={odf['excess_vs_spy'].median():+.3%}  underperformed={(odf['excess_vs_spy']<0).mean():.1%}")
        # Crash rate
        crash20 = (odf["fwd"] < -0.20).mean()
        crash30 = (odf["fwd"] < -0.30).mean()
        print(f"  crash rate:   <-20%: {crash20:.1%}   <-30%: {crash30:.1%}")
        # Per-year breakdown
        print(f"  per-year (weak underperforms SPY):")
        yrs = sorted(odf["year"].unique())
        underperform_count = 0
        valid_yrs = 0
        for yr in yrs:
            y = odf[odf["year"] == yr]
            if len(y) < 3:
                continue
            valid_yrs += 1
            ex_mean = y["excess_vs_spy"].mean()
            ex_med = y["excess_vs_spy"].median()
            marker = "✓" if ex_mean < 0 else "✗"
            if ex_mean < 0:
                underperform_count += 1
            print(f"    {yr}  N={len(y):>4d}  excess mean={ex_mean:>+7.3%}  median={ex_med:>+7.3%}  {marker}")
        print(f"  Years weak < SPY (mean basis): {underperform_count}/{valid_yrs}")

    # ── W1: absolute negative 6m return ──
    def mask_w1(dt):
        if dt not in ret_6m.index: return None
        return ret_6m.loc[dt] < 0

    # ── W2: Stage 4 Weinstein (below 30wk MA AND MA sloping down) ──
    def mask_w2(dt):
        if dt not in panel.index: return None
        if dt not in ma_30w.index: return None
        price = panel.loc[dt]
        ma = ma_30w.loc[dt]
        slope = ma_30w_slope.loc[dt]
        return (price < ma) & (slope < 0)

    # ── W3: multi-filter (RS bot decile + below 200dma + 52w-high dist < -30%) ──
    def mask_w3(dt):
        if dt not in ranks_252.index: return None
        if dt not in ma_200.index: return None
        if dt not in dist_from_52w_high.index: return None
        rs = ranks_252.loc[dt] <= 0.10
        price = panel.loc[dt]
        ma200 = ma_200.loc[dt]
        below = price < ma200
        far = dist_from_52w_high.loc[dt] < -0.30
        return rs & below & far

    # ── W4: making new 52w low within last 20 trading days ──
    def mask_w4(dt):
        if dt not in new_low_recent.index: return None
        return new_low_recent.loc[dt]

    print("=" * 70)
    print(f"H2 strict-weakness backtest — SPY ≥ {1+BULL_EXTENSION_THRESHOLD:.2f}× 200dma "
          f"({len(bull_dates):,} dates, 326 universe names, fwd horizon {FORWARD_DAYS}d)")
    print(f"SPY baseline on bull-extended dates: mean fwd {FORWARD_DAYS}d = "
          f"{spy_fwd.loc[bull_dates].dropna().mean():+.3%}")
    print("=" * 70)

    evaluate("W1: absolute negative 6m return (Burry-literal)", mask_w1)
    evaluate("W2: Stage 4 Weinstein (price < 30wk MA AND MA sloping down)", mask_w2)
    evaluate("W3: multi-filter (RS<10pct + below 200dma + 52w-high < -30%)", mask_w3)
    evaluate("W4: new 52w low within last 20 trading days", mask_w4)


if __name__ == "__main__":
    main()
