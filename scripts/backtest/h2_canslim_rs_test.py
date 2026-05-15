"""H2 weakness gate — CANSLIM-style relative-strength research.

Question: when SPX is >=7% above its 200dma (the current "stunning bull"
regime), do CANSLIM-bottom-decile names (poor relative strength) systematically
underperform the index over the next 75 days?

If yes, H2 = "this ticker is in the bottom RS decile" becomes a per-name gate
that fires independently of H1 (broad-market bear). Anti-ZEBRA / bear_call
entries on H2-positive names would be tradeable even in bull broad markets —
which is exactly the "hidden bear inside the headline bull" Burry described.

Method:
1. Build daily close panel for the 327-name ORATS universe (13 yrs)
2. For each trading day, compute trailing 126-day (6-month) and 252-day
   (12-month) total return per ticker
3. Rank into deciles cross-sectionally per date
4. Filter to "bull-extended" dates where SPX >= 200dma * 1.07
5. For bottom-decile names on those dates, compute forward 75-day return
6. Compare bottom decile vs top decile vs SPX baseline
7. Per-decile mean + win rate + worst-case
8. Walk-forward across years to verify stability

Decision rule: H2 is real if bottom-decile names underperform SPX by
≥3% over the 75-day forward window AND the pattern holds in 8+/13 years.
"""
from __future__ import annotations

import sys
from pathlib import Path
import logging

import pandas as pd
import numpy as np

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"

BULL_EXTENSION_THRESHOLD = 0.07   # SPX must be >=7% above 200dma
LOOKBACK_DAYS_RS = 252            # 12-month return for RS ranking
FORWARD_DAYS = 75                 # match ZEBRA cycle horizon
MIN_NAMES_PER_DATE = 50           # require min cross-section for deciling


def build_close_panel():
    """Build wide daily-close panel: index=trade_date, columns=ticker."""
    closes = {}
    log = logging.getLogger("h2")
    files = sorted(BY_TICKER.glob("*.parquet"))
    log.info("Loading %d ticker parquets...", len(files))
    for i, p in enumerate(files, 1):
        ticker = p.stem
        try:
            df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
        except Exception:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        # Take one row per date (stkPx is same across strikes for same date)
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
    log = logging.getLogger("h2")
    panel = build_close_panel()
    log.info("Panel shape: %s, %d unique dates", panel.shape, len(panel))

    if "SPY" not in panel.columns:
        log.error("SPY column missing from panel — needed for SPX baseline")
        return
    spy = panel["SPY"].dropna()
    log.info("SPY history: %s .. %s (%d obs)", spy.index.min().date(), spy.index.max().date(), len(spy))

    # SPY 200-day MA + extension state
    spy_200dma = spy.rolling(200, min_periods=100).mean()
    spy_ext = spy / spy_200dma - 1.0
    bull_extended = spy_ext >= BULL_EXTENSION_THRESHOLD
    log.info("Days SPY >= 7%% above 200dma: %d of %d (%.1f%%)",
             int(bull_extended.sum()), len(spy_ext), bull_extended.mean()*100)

    # Trailing 252-day return per ticker per date
    ret_252 = panel.pct_change(LOOKBACK_DAYS_RS)
    # Cross-sectional rank per date (percentile 0-1) — only score names with valid data
    ranks = ret_252.rank(axis=1, pct=True)

    # Forward 75-day return per ticker per date
    fwd_ret = panel.pct_change(FORWARD_DAYS).shift(-FORWARD_DAYS)

    # Iterate over bull-extended dates with adequate cross-section
    bull_dates = bull_extended[bull_extended].index
    log.info("Bull-extended date count: %d", len(bull_dates))

    # Aggregate forward returns by RS decile across all bull-extended dates
    decile_bins = [-0.01, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
    decile_labels = ["d1_weak", "d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "d10_strong"]

    obs = []  # rows: date, ticker, decile, fwd_75_return, year
    for dt in bull_dates:
        if dt not in ranks.index:
            continue
        date_ranks = ranks.loc[dt].dropna()
        if len(date_ranks) < MIN_NAMES_PER_DATE:
            continue
        # Get forward returns aligned to this date
        if dt not in fwd_ret.index:
            continue
        date_fwd = fwd_ret.loc[dt]
        # Bin into deciles
        dec = pd.cut(date_ranks, decile_bins, labels=decile_labels, include_lowest=True)
        for ticker in date_ranks.index:
            fwd = date_fwd.get(ticker)
            if pd.isna(fwd):
                continue
            obs.append({
                "date": dt,
                "ticker": ticker,
                "decile": str(dec.loc[ticker]),
                "rs_rank": float(date_ranks.loc[ticker]),
                "fwd_75d_return": float(fwd),
                "year": dt.year,
            })
    if not obs:
        log.error("No observations produced — empty result")
        return
    odf = pd.DataFrame(obs)
    log.info("Observations: %d across %d bull-extended dates × names",
             len(odf), odf["date"].nunique())

    # ── Headline: per-decile forward 75d return ──
    print("\n=== Forward 75-day return by RS-decile (bull-extended sample) ===")
    print(f"Bull-extended threshold: SPY >= 200dma * {1+BULL_EXTENSION_THRESHOLD:.2f}")
    print(f"Total observations: {len(odf)}  (unique dates: {odf['date'].nunique()})")
    print()
    print(f"  {'decile':>11s}  {'N':>6s}  {'mean_fwd':>9s}  {'median':>8s}  {'win':>5s}  {'worst':>8s}")
    summary = []
    for d in decile_labels:
        sub = odf[odf["decile"] == d]
        if sub.empty:
            print(f"  {d:>11s}: no observations")
            continue
        m = sub["fwd_75d_return"].mean()
        med = sub["fwd_75d_return"].median()
        w = (sub["fwd_75d_return"] > 0).mean()
        mn = sub["fwd_75d_return"].min()
        summary.append((d, len(sub), m, med, w, mn))
        print(f"  {d:>11s}  {len(sub):>6d}  {m:>+9.3%}  {med:>+8.3%}  {w:>5.1%}  {mn:>+8.3%}")

    # SPY baseline over the same set of dates
    spy_fwd = spy.pct_change(FORWARD_DAYS).shift(-FORWARD_DAYS)
    spy_fwd_at_dates = spy_fwd.loc[odf["date"].unique()].dropna()
    print()
    print(f"  SPY baseline (fwd 75d on same dates): mean={spy_fwd_at_dates.mean():+.3%}, median={spy_fwd_at_dates.median():+.3%}")

    # Bottom-decile relative to SPY (anti-cohort lift)
    weak = odf[odf["decile"] == "d1_weak"]
    print()
    weak_minus_spy = []
    for dt, group in weak.groupby("date"):
        spy_f = spy_fwd.get(dt)
        if pd.isna(spy_f):
            continue
        for fwd in group["fwd_75d_return"]:
            weak_minus_spy.append(fwd - spy_f)
    if weak_minus_spy:
        wms = pd.Series(weak_minus_spy)
        print(f"  d1_weak − SPY (excess return): mean={wms.mean():+.3%}, median={wms.median():+.3%}, "
              f"underperformed={(wms < 0).mean():.1%}")

    # ── Per-year stability ──
    print("\n=== d1_weak vs d10_strong by year (bull-extended dates only) ===")
    print(f"  {'year':>4s}  {'n_dates':>7s}  {'d1_mean':>9s}  {'d10_mean':>9s}  {'gap':>9s}  d1<SPY")
    yrs = sorted(odf["year"].unique())
    pos_years = 0
    for yr in yrs:
        y_obs = odf[odf["year"] == yr]
        d1 = y_obs[y_obs["decile"] == "d1_weak"]
        d10 = y_obs[y_obs["decile"] == "d10_strong"]
        if d1.empty or d10.empty:
            continue
        d1_m = d1["fwd_75d_return"].mean()
        d10_m = d10["fwd_75d_return"].mean()
        gap = d1_m - d10_m
        # d1 vs SPY in this year
        y_dates = y_obs["date"].unique()
        spy_y = spy_fwd.loc[y_dates].dropna()
        spy_y_mean = spy_y.mean() if len(spy_y) else float("nan")
        d1_minus_spy = d1_m - spy_y_mean
        if d1_minus_spy < 0:
            pos_years += 1
        marker = "✓" if d1_minus_spy < 0 else "✗"
        print(f"  {yr}  {y_obs['date'].nunique():>7d}  {d1_m:>+9.3%}  {d10_m:>+9.3%}  "
              f"{gap:>+9.3%}  {marker} ({d1_minus_spy:+.3%})")
    print(f"\n  Years where d1_weak underperformed SPY: {pos_years}/{len(yrs)}")

    # ── Sanity: do weak names actually lose in absolute terms in some years? ──
    print("\n=== d1_weak absolute return distribution (across all bull-extended obs) ===")
    weak_returns = weak["fwd_75d_return"]
    pct_negative = (weak_returns < 0).mean()
    pct_below_10 = (weak_returns < -0.10).mean()
    pct_below_20 = (weak_returns < -0.20).mean()
    print(f"  d1_weak fwd 75d: mean={weak_returns.mean():+.3%}  median={weak_returns.median():+.3%}")
    print(f"  N={len(weak_returns)}  losers (<0): {pct_negative:.1%}  crashers (<-10%): {pct_below_10:.1%}  big crashers (<-20%): {pct_below_20:.1%}")


if __name__ == "__main__":
    main()
