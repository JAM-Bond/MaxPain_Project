"""Exploratory — does a "bull-to-bear transition" signal fire bear_call
entries at the right moment, BEFORE the mean-revert snapback?

Hypothesis: the per-sector H1 gate (sector < own 200-DMA + own IVR > 0.5)
catches sectors AFTER the move down — which historically is when sectors
bounce. A bear_call entered at that point is fighting mean reversion.
A better gate would fire on the rollover itself, while the sector is
still elevated, but momentum has clearly turned.

Five candidate transition signals, tested on each sector ETF's existing
OTM/mgd50 bear_call cycle parquet:

  S1 STAGE2_BREAK     — was above 200-DMA 30 days ago, below 200-DMA today
                        (Weinstein-style classical stage-2 break)
  S2 EARLY_ROLLOVER   — above 200-DMA today (still cushion), but below
                        50-DMA AND 20-day return < -3%
                        (catches the rollover before it breaks 200-DMA)
  S3 DEATH_CROSS      — 50-DMA crossed below 100-DMA in the last 5 days
                        (faster than the 50/200 death cross)
  S4 MOMENTUM_FLIP    — 60-day return still positive, 20-day return < 0,
                        gap to 200-DMA still positive but shrinking ≥ 50%
                        from its 60-day peak (trend losing altitude)
  S5 IV_EXPAND_HIGH   — spot > 200-DMA, IVR_252 > 0.4, IVR rising over
                        the trailing 20 days (vol hedging before price breaks)

For each signal we report (per sector + pooled):
  - cycles where signal active at entry
  - mean per-share P/L
  - win rate
  - lift vs ungated baseline

A signal "works" if it lifts mean P/L meaningfully above the ungated
baseline AND fires enough times to be statistically credible (target
N ≥ 12 cycles per sector). This is a screen, not a promotion — anything
promising would require a formal pre-reg + walk-forward.

Output: data/profile/sector_etf_bearcall_transition_signals.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_IN = ROOT / "data/profile/bear_call_moneyness_results.parquet"
OUT_PARQUET = ROOT / "data/profile/sector_etf_bearcall_transition_signals.parquet"

SECTOR_ETFS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                "XLP", "XLU", "XLV", "XLY", "IYR", "SMH"]


def per_ticker_daily(ticker: str) -> pd.DataFrame | None:
    """Daily close + 30-DTE ATM IV + computed signals."""
    p = BY_TICKER / f"{ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["trade_date", "expirDate", "strike",
                                       "stkPx", "delta", "cMidIv"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["exp_dt"] = pd.to_datetime(df["expirDate"], format="%m/%d/%Y", errors="coerce")
    df["dte"] = (df["exp_dt"] - df["trade_date"]).dt.days
    df["delta_dist"] = (df["delta"] - 0.50).abs()
    front = (df[(df["dte"] >= 25) & (df["dte"] <= 35)]
               .sort_values(["trade_date", "delta_dist"])
               .drop_duplicates("trade_date"))
    if front.empty:
        return None
    daily = front.set_index("trade_date")[["stkPx", "cMidIv"]].copy()
    daily.columns = ["close", "atm_iv30"]
    daily = daily.sort_index()
    daily["ma50"] = daily["close"].rolling(50, min_periods=30).mean()
    daily["ma100"] = daily["close"].rolling(100, min_periods=60).mean()
    daily["ma200"] = daily["close"].rolling(200, min_periods=100).mean()
    daily["ret_20"] = daily["close"].pct_change(20)
    daily["ret_60"] = daily["close"].pct_change(60)
    daily["ma200_30d_ago"] = daily["ma200"].shift(30)
    daily["close_30d_ago"] = daily["close"].shift(30)
    daily["ma50_5d_ago"] = daily["ma50"].shift(5)
    daily["ma100_5d_ago"] = daily["ma100"].shift(5)
    daily["pct_to_ma200"] = daily["close"] / daily["ma200"] - 1.0
    daily["pct_to_ma200_60d_peak"] = daily["pct_to_ma200"].rolling(60).max()

    rmin = daily["atm_iv30"].rolling(252, min_periods=120).min()
    rmax = daily["atm_iv30"].rolling(252, min_periods=120).max()
    daily["ivr_252"] = (daily["atm_iv30"] - rmin) / (rmax - rmin).replace(0, np.nan)
    daily["ivr_20d_ago"] = daily["ivr_252"].shift(20)
    daily["ivr_rising_20d"] = (daily["ivr_252"] - daily["ivr_20d_ago"]).fillna(0)

    # ── Signals ──
    # S1: Stage 2 break — above MA200 30 days ago, below MA200 today
    daily["S1_STAGE2_BREAK"] = (
        (daily["close_30d_ago"] > daily["ma200_30d_ago"])
        & (daily["close"] < daily["ma200"])
    ).astype(int)

    # S2: Early rollover — still above MA200 (cushion intact), but below
    # MA50 AND 20-day return ≤ -3% (momentum decisively turned)
    daily["S2_EARLY_ROLLOVER"] = (
        (daily["close"] > daily["ma200"])
        & (daily["close"] < daily["ma50"])
        & (daily["ret_20"] <= -0.03)
    ).astype(int)

    # S3: Faster death cross — MA50 crossed below MA100 in the last 5 days
    cross_today = daily["ma50"] < daily["ma100"]
    cross_5d_ago = daily["ma50_5d_ago"] > daily["ma100_5d_ago"]
    daily["S3_DEATH_CROSS"] = (cross_today & cross_5d_ago).astype(int)

    # S4: Momentum flip — 60-day return still positive, 20-day flipped
    # negative, AND gap to MA200 has compressed ≥ 50% from its 60-day peak
    gap_compress = (daily["pct_to_ma200_60d_peak"] > 0) & (
        daily["pct_to_ma200"] <= 0.5 * daily["pct_to_ma200_60d_peak"]
    )
    daily["S4_MOMENTUM_FLIP"] = (
        (daily["ret_60"] > 0)
        & (daily["ret_20"] < 0)
        & gap_compress
    ).astype(int)

    # S5: IV expansion while elevated — close > MA200, IVR > 0.40, IVR
    # rising over trailing 20 days
    daily["S5_IV_EXPAND_HIGH"] = (
        (daily["close"] > daily["ma200"])
        & (daily["ivr_252"] > 0.40)
        & (daily["ivr_rising_20d"] > 0.10)
    ).astype(int)

    return daily


SIGNALS = ["S1_STAGE2_BREAK", "S2_EARLY_ROLLOVER", "S3_DEATH_CROSS",
           "S4_MOMENTUM_FLIP", "S5_IV_EXPAND_HIGH"]


def summarize(label: str, pnl: pd.Series) -> dict:
    n = len(pnl)
    if n == 0:
        return {"label": label, "n": 0, "mean": np.nan, "median": np.nan,
                "win_rate": np.nan, "total": 0.0}
    return {
        "label": label,
        "n": int(n),
        "mean": round(float(pnl.mean()), 4),
        "median": round(float(pnl.median()), 4),
        "win_rate": round(float((pnl > 0).mean()), 3),
        "total": round(float(pnl.sum()), 2),
    }


def main() -> int:
    if not RESULTS_IN.exists():
        print(f"ERROR: input parquet missing: {RESULTS_IN}")
        return 1

    cycles = pd.read_parquet(RESULTS_IN)
    cycles["entry_date"] = pd.to_datetime(cycles["entry_date"])
    sec_cycles = cycles[cycles["ticker"].isin(SECTOR_ETFS)
                          & (cycles["moneyness"] == "OTM")].copy()
    print(f"Sector ETF OTM cycles: {len(sec_cycles):,} across "
          f"{sec_cycles['ticker'].nunique()} ETFs")

    summary_rows = []
    detail_frames = []
    for etf in sorted(sec_cycles["ticker"].unique()):
        daily = per_ticker_daily(etf)
        if daily is None:
            continue
        sig_cols = SIGNALS
        flag = daily[sig_cols]
        sub = sec_cycles[sec_cycles["ticker"] == etf].copy()
        sub = sub.merge(flag, left_on="entry_date", right_index=True, how="left")
        for c in sig_cols:
            sub[c] = sub[c].fillna(0).astype(int)
        detail_frames.append(sub)

        # Per-signal summary for this ETF
        baseline_pnl = sub["mgd50_pnl"]
        baseline = summarize("ALL", baseline_pnl)
        summary_rows.append({"ticker": etf, **baseline})
        for sig in sig_cols:
            on_pnl = sub.loc[sub[sig] == 1, "mgd50_pnl"]
            summary_rows.append({"ticker": etf, **summarize(sig, on_pnl)})

    df = pd.DataFrame(summary_rows)
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)

    # Print per-sector table
    print()
    print("=" * 100)
    print("Per-sector: each signal's bear_call mean P/L vs ungated baseline (OTM, mgd50, slip=0.50)")
    print("=" * 100)
    for etf in sorted(df["ticker"].unique()):
        sub = df[df["ticker"] == etf]
        base = sub[sub["label"] == "ALL"]["mean"].iloc[0]
        base_n = int(sub[sub["label"] == "ALL"]["n"].iloc[0])
        print(f"\n{etf}  (ungated N={base_n}, mean=${base:+.4f}/sh)")
        for sig in SIGNALS:
            r = sub[sub["label"] == sig]
            if r.empty:
                continue
            r = r.iloc[0]
            if r["n"] == 0:
                print(f"  {sig:18s}  N=  0  —  (never fired)")
                continue
            lift = r["mean"] - base if not np.isnan(r["mean"]) else np.nan
            star = "  ★" if (r["n"] >= 8 and not np.isnan(r["mean"]) and r["mean"] > 0) else ""
            print(f"  {sig:18s}  N={r['n']:>3d}  mean=${r['mean']:>+.4f}/sh  "
                  f"win={r['win_rate']:>.3f}  lift={lift:>+.4f}{star}")

    # Pooled across all sector ETFs
    detail = pd.concat(detail_frames, ignore_index=True)
    pnl_all = detail["mgd50_pnl"]
    print()
    print("=" * 100)
    print("POOLED across all 8 sector ETFs (signal fires anywhere → cycle counted)")
    print("=" * 100)
    base = summarize("ALL", pnl_all)
    print(f"  Ungated baseline:   N={base['n']:>4d}  mean=${base['mean']:+.4f}/sh  "
          f"win={base['win_rate']:.3f}  total=${base['total']:+.2f}")
    for sig in SIGNALS:
        on_pnl = detail.loc[detail[sig] == 1, "mgd50_pnl"]
        off_pnl = detail.loc[detail[sig] == 0, "mgd50_pnl"]
        on_s = summarize(f"{sig}=1", on_pnl)
        off_s = summarize(f"{sig}=0", off_pnl)
        lift = on_s["mean"] - base["mean"] if not np.isnan(on_s["mean"]) else np.nan
        flag = "  ★ POSITIVE" if (on_s["mean"] is not None and not np.isnan(on_s["mean"]) and on_s["mean"] > 0) else ""
        print(f"  {sig}: ON  N={on_s['n']:>4d}  mean=${on_s['mean']:+.4f}/sh  "
              f"win={on_s['win_rate']:.3f}  total=${on_s['total']:+.2f}  "
              f"lift={lift:+.4f}{flag}")

    # Signal overlap diagnostic
    print()
    print("=" * 100)
    print("Signal fire-rates and pairwise overlap (sector ETFs only)")
    print("=" * 100)
    fire = {s: int(detail[s].sum()) for s in SIGNALS}
    n_total = len(detail)
    for s in SIGNALS:
        print(f"  {s}: fires on {fire[s]:>4d}/{n_total:>4d} cycles  ({fire[s]/n_total*100:5.1f}%)")
    # Pairwise
    print("\n  Pairwise joint fires:")
    for i, s1 in enumerate(SIGNALS):
        for s2 in SIGNALS[i+1:]:
            joint = int(((detail[s1] == 1) & (detail[s2] == 1)).sum())
            if joint > 0:
                print(f"    {s1} & {s2}: {joint}")

    print(f"\nWrote {OUT_PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
