"""Exploratory — does the H1 gate work per-sector when each sector ETF uses
its OWN 200-DMA + IVR>0.5 (rather than SPY's)?

Background: H1 (SPY < 200-DMA AND IVR_252 > 0.5) is the broad-market gate
that conditions all bear_call entries today. Hypothesis: sectors that are
weak on their own terms — even when SPY is strong — might support bear_call
entries that wouldn't fire under the SPY-based H1.

Test: for each sector ETF, compute its own (close < own 200-DMA) AND
(own IVR_252 > 0.5) flag per day. Re-bucket the existing per-cycle
bear_call results (already simulated at slip=0.50, managed-50% exit) by
that per-ticker flag at the cycle's entry_date. Compare:
  - "ALL"       = every cycle (ungated baseline)
  - "SELF_H1"   = cycles where own per-ticker H1 was active at entry
  - "OFF"       = cycles where own per-ticker H1 was off at entry

For context, also show what the SPY-based H1 would do for the same
ticker — i.e., gate by SPY's flag, not the ETF's.

This is a one-off exploratory backtest. Not a pre-registered promotion
study. Output written to console + a small parquet for reproducibility.
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
OUT_PARQUET = ROOT / "data/profile/sector_etf_bearcall_self_h1.parquet"

SECTOR_ETFS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                "XLP", "XLU", "XLV", "XLY", "IYR", "SMH"]


def per_ticker_h1_series(ticker: str) -> pd.DataFrame | None:
    """Daily series with close, ma200, atm_iv30, ivr_252, h1_active.

    Mirrors the SPY pipeline in scripts/pipeline/backfill_regime_state.py:
      - 30-DTE ATM call mid-IV from the chain (delta ≈ 0.50)
      - ivr_252 = rolling 252d min-max rank of atm_iv30
      - ma200 = 200-day moving average of close (min_periods=100)
      - h1 = (close < ma200) AND (ivr_252 > 0.50)
    """
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
    daily["ma200"] = daily["close"].rolling(200, min_periods=100).mean()
    rmin = daily["atm_iv30"].rolling(252, min_periods=120).min()
    rmax = daily["atm_iv30"].rolling(252, min_periods=120).max()
    daily["ivr_252"] = (daily["atm_iv30"] - rmin) / (rmax - rmin).replace(0, np.nan)
    daily["below_ma200"] = (daily["close"] < daily["ma200"]).astype(int)
    daily["ivr_high"] = (daily["ivr_252"] > 0.5).astype(int)
    daily["h1_active"] = (daily["below_ma200"] & daily["ivr_high"]).astype(int)
    return daily


def summarize(label: str, pnl: pd.Series) -> dict:
    n = len(pnl)
    if n == 0:
        return {"label": label, "n": 0, "mean": np.nan, "median": np.nan,
                "win_rate": np.nan, "total": 0.0, "worst": np.nan, "best": np.nan}
    return {
        "label": label,
        "n": int(n),
        "mean": round(float(pnl.mean()), 4),
        "median": round(float(pnl.median()), 4),
        "win_rate": round(float((pnl > 0).mean()), 3),
        "total": round(float(pnl.sum()), 2),
        "worst": round(float(pnl.min()), 2),
        "best": round(float(pnl.max()), 2),
    }


def main() -> int:
    if not RESULTS_IN.exists():
        print(f"ERROR: input parquet missing: {RESULTS_IN}")
        return 1

    # Load per-cycle bear_call P&L (slip=0.50 already applied at backtest)
    cycles = pd.read_parquet(RESULTS_IN)
    cycles["entry_date"] = pd.to_datetime(cycles["entry_date"])
    print(f"Loaded {len(cycles):,} bear_call cycles across {cycles['ticker'].nunique()} tickers")

    # Filter to sector ETFs
    sec_cycles = cycles[cycles["ticker"].isin(SECTOR_ETFS)].copy()
    print(f"Sector ETF cycles: {len(sec_cycles):,} across "
          f"{sec_cycles['ticker'].nunique()} ETFs (missing: "
          f"{set(SECTOR_ETFS) - set(sec_cycles['ticker'].unique())})")

    # Compute SPY H1 series (reference)
    print("\nComputing SPY H1 series (reference)...")
    spy_daily = per_ticker_h1_series("SPY")
    if spy_daily is None:
        print("ERROR: SPY series missing")
        return 1
    spy_h1 = spy_daily[["h1_active"]].rename(columns={"h1_active": "spy_h1_active"})

    # Compute per-sector H1 + tag each cycle
    summaries = []
    detail_rows = []
    for etf in sorted(sec_cycles["ticker"].unique()):
        ser = per_ticker_h1_series(etf)
        if ser is None:
            print(f"  {etf}: no series (skipping)")
            continue
        flag = ser[["h1_active", "ivr_252", "below_ma200", "ma200", "close"]].rename(
            columns={"h1_active": "self_h1_active"})
        sub = sec_cycles[sec_cycles["ticker"] == etf].copy()
        sub = sub.merge(flag, left_on="entry_date", right_index=True, how="left")
        sub = sub.merge(spy_h1, left_on="entry_date", right_index=True, how="left")
        sub["self_h1_active"] = sub["self_h1_active"].fillna(0).astype(int)
        sub["spy_h1_active"] = sub["spy_h1_active"].fillna(0).astype(int)
        # collapse moneyness — use the BEST per-cycle (max P/L across OTM/ATM/ITM)?
        # No — per-cycle metric should be moneyness-naive. Use OTM (the default
        # bear_call deployment) so the result reflects what the live framework
        # would actually trade.
        otm = sub[sub["moneyness"] == "OTM"].copy()
        if otm.empty:
            continue
        pnl_all = otm["mgd50_pnl"]
        pnl_self = otm.loc[otm["self_h1_active"] == 1, "mgd50_pnl"]
        pnl_off = otm.loc[otm["self_h1_active"] == 0, "mgd50_pnl"]
        pnl_spy = otm.loc[otm["spy_h1_active"] == 1, "mgd50_pnl"]
        pnl_spy_off = otm.loc[otm["spy_h1_active"] == 0, "mgd50_pnl"]

        rows = [
            {"ticker": etf, **summarize("ALL",      pnl_all)},
            {"ticker": etf, **summarize("SELF_H1",  pnl_self)},
            {"ticker": etf, **summarize("SELF_OFF", pnl_off)},
            {"ticker": etf, **summarize("SPY_H1",   pnl_spy)},
            {"ticker": etf, **summarize("SPY_OFF",  pnl_spy_off)},
        ]
        summaries.extend(rows)
        detail_rows.append(otm[["ticker", "entry_date", "moneyness",
                                 "self_h1_active", "spy_h1_active",
                                 "mgd50_pnl", "held_pnl"]])

    if not summaries:
        print("No summaries produced.")
        return 1

    df = pd.DataFrame(summaries)
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)

    # Per-sector table
    print()
    print("=" * 96)
    print("Per-sector bear_call (OTM, mgd50, slip=0.50, per-share P/L)")
    print("Columns: N cycles · mean ($/share) · win rate · total")
    print("=" * 96)
    print(f"{'Ticker':6s}  {'Cohort':9s}  {'N':>4s}  "
          f"{'mean':>8s}  {'win':>6s}  {'total':>8s}  {'lift_vs_ALL':>12s}")
    for etf in sorted(df["ticker"].unique()):
        sub = df[df["ticker"] == etf]
        base = sub[sub["label"] == "ALL"]["mean"].iloc[0]
        for label in ["ALL", "SELF_H1", "SELF_OFF", "SPY_H1", "SPY_OFF"]:
            r = sub[sub["label"] == label]
            if r.empty:
                continue
            r = r.iloc[0]
            lift = (r["mean"] - base) if (not np.isnan(r["mean"]) and not np.isnan(base)) else np.nan
            lift_s = f"{lift:+.4f}" if not np.isnan(lift) else "  —"
            print(f"{etf:6s}  {label:9s}  {r['n']:>4d}  "
                  f"{r['mean']:>+8.4f}  {r['win_rate']:>6.3f}  "
                  f"{r['total']:>+8.2f}  {lift_s:>12s}")
        print()

    # Pooled summary across all sector ETFs
    detail = pd.concat(detail_rows, ignore_index=True)
    print("=" * 96)
    print("Pooled across all sector ETFs (collapsed)")
    print("=" * 96)
    pnl_all = detail["mgd50_pnl"]
    pnl_self_on = detail.loc[detail["self_h1_active"] == 1, "mgd50_pnl"]
    pnl_self_off = detail.loc[detail["self_h1_active"] == 0, "mgd50_pnl"]
    pnl_spy_on = detail.loc[detail["spy_h1_active"] == 1, "mgd50_pnl"]
    pnl_spy_off = detail.loc[detail["spy_h1_active"] == 0, "mgd50_pnl"]
    for label, ser in [("ALL", pnl_all),
                        ("SELF_H1 active", pnl_self_on),
                        ("SELF_H1 off",    pnl_self_off),
                        ("SPY_H1 active",  pnl_spy_on),
                        ("SPY_H1 off",     pnl_spy_off)]:
        s = summarize(label, ser)
        print(f"  {label:18s}  N={s['n']:>5d}  mean=${s['mean']:+.4f}/sh  "
              f"win={s['win_rate']:.3f}  total=${s['total']:+.2f}")

    # Concordance — when do the two gates agree?
    detail["both_on"] = ((detail["self_h1_active"] == 1)
                          & (detail["spy_h1_active"] == 1)).astype(int)
    detail["self_only"] = ((detail["self_h1_active"] == 1)
                            & (detail["spy_h1_active"] == 0)).astype(int)
    detail["spy_only"] = ((detail["self_h1_active"] == 0)
                           & (detail["spy_h1_active"] == 1)).astype(int)
    print()
    print("=" * 96)
    print("Cycle-level gate concordance (sector ETFs only)")
    print("=" * 96)
    n = len(detail)
    print(f"  Both gates ON:    {int(detail['both_on'].sum()):>5d}  ({detail['both_on'].mean()*100:5.1f}%)")
    print(f"  SELF only ON:     {int(detail['self_only'].sum()):>5d}  ({detail['self_only'].mean()*100:5.1f}%)  ← cycles SPY-gated rule would have missed")
    print(f"  SPY only ON:      {int(detail['spy_only'].sum()):>5d}  ({detail['spy_only'].mean()*100:5.1f}%)")
    print(f"  Both OFF:         {n - int(detail['both_on'].sum() + detail['self_only'].sum() + detail['spy_only'].sum()):>5d}  "
          f"({(1 - detail['both_on'].mean() - detail['self_only'].mean() - detail['spy_only'].mean())*100:5.1f}%)")

    print()
    print(f"Wrote: {OUT_PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
