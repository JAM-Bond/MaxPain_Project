"""H2 Phase 1 — walk-forward + live-failure validation.

Evaluates the W3 multi-filter definition (RS bottom 10% + below own 200dma
+ ≥30% off 52w high) against the pre-registered decision rule in
docs/H2_PREREG.md.

Three sealed gates:
  A. Pooled crash-rate ≥ 2× SPY baseline crash rate
  B. ≥3 of 4 walk-forward windows show crash-rate-ratio ≥ 1.5×
  C. ≥2 of 3 live-failure names matched W3 at entry date (WFC 5/5, XLU 5/6, KRE 5/5)

If ALL THREE pass → promote to qualifier integration.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"

BULL_EXTENSION_THRESHOLD = 0.07
LOOKBACK_252 = 252
LOOKBACK_52W = 252
LOOKBACK_200 = 200
FORWARD_DAYS = 75
RS_DECILE_THRESHOLD = 0.10
DIST_FROM_52W_HIGH_THRESHOLD = -0.30  # at least 30% below 52w high
MIN_NAMES_PER_DATE = 50
CRASH_THRESHOLD = -0.20

# Pre-reg gate thresholds (sealed)
GATE_A_RATIO = 2.0
GATE_B_RATIO = 1.5
GATE_B_MIN_WINDOWS = 3
GATE_C_MIN_MATCHES = 2

# Live-failure entry dates (sealed)
LIVE_FAILURES = [
    ("WFC", date(2026, 5, 5)),
    ("XLU", date(2026, 5, 6)),
    ("KRE", date(2026, 5, 5)),
]

# Walk-forward validation windows (matches ZEBRA Phase C convention)
WALK_FORWARD_WINDOWS = [
    ("2021-2023", range(2021, 2024)),
    ("2022-2024", range(2022, 2025)),
    ("2023-2025", range(2023, 2026)),
    ("2024-2026", range(2024, 2027)),
]


def build_close_panel():
    closes = {}
    log = logging.getLogger("h2val")
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


def compute_w3_mask(panel, ranks_252, ma_200, dist_52w_high, date_idx):
    """Return boolean Series of W3 matches on date_idx."""
    if date_idx not in panel.index:
        return None
    if date_idx not in ranks_252.index or date_idx not in ma_200.index \
       or date_idx not in dist_52w_high.index:
        return None
    price = panel.loc[date_idx]
    return (
        (ranks_252.loc[date_idx] <= RS_DECILE_THRESHOLD) &
        (price < ma_200.loc[date_idx]) &
        (dist_52w_high.loc[date_idx] <= DIST_FROM_52W_HIGH_THRESHOLD)
    ).fillna(False)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("h2val")

    panel = build_close_panel()
    log.info("Panel shape: %s", panel.shape)

    if "SPY" not in panel.columns:
        log.error("SPY missing — cannot establish baseline")
        return

    spy = panel["SPY"].dropna()
    spy_200dma = spy.rolling(200, min_periods=100).mean()
    spy_ext = spy / spy_200dma - 1.0
    bull_extended = spy_ext >= BULL_EXTENSION_THRESHOLD
    log.info("Bull-extended days: %d / %d (%.1f%%)",
             int(bull_extended.sum()), len(spy_ext), bull_extended.mean()*100)

    # Per-ticker metrics
    ret_252 = panel.pct_change(LOOKBACK_252, fill_method=None)
    ranks_252 = ret_252.rank(axis=1, pct=True)
    ma_200 = panel.rolling(LOOKBACK_200, min_periods=100).mean()
    rolling_52w_high = panel.rolling(LOOKBACK_52W, min_periods=120).max()
    dist_52w_high = panel / rolling_52w_high - 1.0
    fwd_ret = panel.pct_change(FORWARD_DAYS, fill_method=None).shift(-FORWARD_DAYS)
    spy_fwd = spy.pct_change(FORWARD_DAYS, fill_method=None).shift(-FORWARD_DAYS)

    bull_dates = bull_extended[bull_extended].index

    # Collect (date, ticker, fwd_return) for every W3 match on bull-extended dates
    log.info("Collecting W3 observations across bull-extended dates...")
    rows = []
    for dt in bull_dates:
        mask = compute_w3_mask(panel, ranks_252, ma_200, dist_52w_high, dt)
        if mask is None or not mask.any():
            continue
        if dt not in fwd_ret.index:
            continue
        date_fwd = fwd_ret.loc[dt]
        spy_f = spy_fwd.get(dt)
        if pd.isna(spy_f):
            continue
        for ticker in mask[mask].index:
            fwd = date_fwd.get(ticker)
            if pd.isna(fwd):
                continue
            rows.append({
                "date": dt,
                "ticker": ticker,
                "fwd": float(fwd),
                "spy_fwd": float(spy_f),
                "year": dt.year,
            })
    if not rows:
        log.error("No W3 observations collected")
        return
    odf = pd.DataFrame(rows)
    log.info("W3 observations: %d across %d unique bull-extended dates",
             len(odf), odf["date"].nunique())

    # SPY crash-rate baseline on the same set of dates
    spy_dates = pd.Series(odf["spy_fwd"].values, index=odf["date"]).drop_duplicates()
    spy_baseline_crash = (spy_dates < CRASH_THRESHOLD).mean()

    w3_crash = (odf["fwd"] < CRASH_THRESHOLD).mean()
    pooled_ratio = w3_crash / spy_baseline_crash if spy_baseline_crash > 0 else float("inf")

    print("\n" + "=" * 72)
    print("H2 PHASE 1 VALIDATION — Pre-Registered Decision Rule")
    print("=" * 72)

    # ── GATE A ──────────────────────────────────────────────────────────
    print("\n--- GATE A: pooled crash-rate ratio ≥ 2.0× ---")
    print(f"  N W3 observations:        {len(odf):,}")
    print(f"  Unique bull-extended dates: {odf['date'].nunique():,}")
    print(f"  SPY baseline crash rate (fwd 75d < -20%): {spy_baseline_crash:.2%}")
    print(f"  W3 crash rate:                            {w3_crash:.2%}")
    print(f"  Crash-rate ratio:                         {pooled_ratio:.2f}×")
    gate_a_pass = pooled_ratio >= GATE_A_RATIO
    print(f"  Gate A: {'✓ PASS' if gate_a_pass else '✗ FAIL'} "
          f"(threshold ≥ {GATE_A_RATIO:.1f}×)")

    # ── GATE B ──────────────────────────────────────────────────────────
    print(f"\n--- GATE B: walk-forward — crash-rate ratio ≥ 1.5× in ≥3 of 4 windows ---")
    pass_windows = 0
    for label, yrs in WALK_FORWARD_WINDOWS:
        win_obs = odf[odf["year"].isin(list(yrs))]
        if win_obs.empty:
            print(f"  {label}: no observations")
            continue
        win_spy = pd.Series(win_obs["spy_fwd"].values, index=win_obs["date"]).drop_duplicates()
        spy_crash = (win_spy < CRASH_THRESHOLD).mean() if not win_spy.empty else float("nan")
        w3_crash_w = (win_obs["fwd"] < CRASH_THRESHOLD).mean()
        ratio = w3_crash_w / spy_crash if spy_crash > 0 else float("inf")
        passes = ratio >= GATE_B_RATIO
        if passes:
            pass_windows += 1
        marker = "✓" if passes else "✗"
        print(f"  {label}  N={len(win_obs):>5d}  SPY_crash={spy_crash:.2%}  "
              f"W3_crash={w3_crash_w:.2%}  ratio={ratio:.2f}×  {marker}")
    gate_b_pass = pass_windows >= GATE_B_MIN_WINDOWS
    print(f"  Gate B: {'✓ PASS' if gate_b_pass else '✗ FAIL'} "
          f"({pass_windows} of 4 windows passed; need ≥{GATE_B_MIN_WINDOWS})")

    # ── GATE C ──────────────────────────────────────────────────────────
    print(f"\n--- GATE C: live-failure validation — ≥2 of 3 names matched W3 at entry ---")
    matches = 0
    for ticker, entry_date in LIVE_FAILURES:
        entry_ts = pd.Timestamp(entry_date)
        mask = compute_w3_mask(panel, ranks_252, ma_200, dist_52w_high, entry_ts)
        if mask is None:
            print(f"  {ticker} @ {entry_date}: ✗ NO_DATA (date not in panel)")
            continue
        if ticker not in mask.index:
            print(f"  {ticker} @ {entry_date}: ✗ NO_DATA (ticker not in panel)")
            continue
        matched = bool(mask.loc[ticker])
        # Diagnostics
        rs = float(panel.loc[entry_ts].pipe(
            lambda _: ranks_252.loc[entry_ts].get(ticker, float("nan"))
        ))
        price = float(panel.loc[entry_ts].get(ticker, float("nan")))
        ma = float(ma_200.loc[entry_ts].get(ticker, float("nan")))
        dist = float(dist_52w_high.loc[entry_ts].get(ticker, float("nan")))
        if matched:
            matches += 1
        marker = "✓ MATCH" if matched else "✗ NO MATCH"
        print(f"  {ticker} @ {entry_date}: {marker}  "
              f"(RS={rs:.2f}, price=${price:.2f}, 200dma=${ma:.2f}, dist52H={dist:+.1%})")
    gate_c_pass = matches >= GATE_C_MIN_MATCHES
    print(f"  Gate C: {'✓ PASS' if gate_c_pass else '✗ FAIL'} "
          f"({matches} of {len(LIVE_FAILURES)} matched; need ≥{GATE_C_MIN_MATCHES})")

    # ── VERDICT ─────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("FINAL VERDICT")
    print("=" * 72)
    overall = gate_a_pass and gate_b_pass and gate_c_pass
    print(f"  Gate A (pooled crash ≥2×):       {'✓' if gate_a_pass else '✗'}")
    print(f"  Gate B (walk-forward ≥1.5× ×3):  {'✓' if gate_b_pass else '✗'}")
    print(f"  Gate C (live-failure ≥2 of 3):   {'✓' if gate_c_pass else '✗'}")
    print()
    if overall:
        print("  ✅ PROMOTE — all three gates pass. Integrate H2 W3 as bull_put exclusion filter.")
    else:
        failed = []
        if not gate_a_pass: failed.append("A")
        if not gate_b_pass: failed.append("B")
        if not gate_c_pass: failed.append("C")
        print(f"  ❌ REJECT — gate(s) {', '.join(failed)} failed. No integration.")
    print()


if __name__ == "__main__":
    main()
