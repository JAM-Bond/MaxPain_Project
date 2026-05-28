"""Phase 1 exploratory — bull_put cycles entered ABOVE the underlying's MA,
did the position survive when spot crossed BELOW the MA during the hold?

Compares two cells per MA window (200-DMA, 50-DMA):
  (A) entered above MA, spot stayed above MA through expiration
  (B) entered above MA, spot crossed below MA at some point during hold

Reports held-to-expiry mean P/L, win rate, N for each. Also reports the
managed-at-50% counterpart so we can see if early-exit discipline rescues
the crossed subset.

NOTE: this study does NOT simulate exit-at-cross P/L (we don't have daily
MTM in the existing cycle parquet). It only tells us whether the
crossed-during-hold cohort is structurally damaged on hold. If yes, then
Phase 2 (re-simulate with cross-exit rule) is worth doing.

Output: data/profile/bull_put_ma_cross_during_hold.parquet (per-cell rollup)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
CYCLES_IN = ROOT / "data/profile/bull_put_moneyness_results.parquet"
OUT_PARQUET = ROOT / "data/profile/bull_put_ma_cross_during_hold.parquet"


def _ticker_daily_series(ticker: str) -> pd.DataFrame | None:
    """Return DataFrame indexed by trade_date with close, ma50, ma200."""
    p = BY_TICKER / f"{ticker}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    daily = (df.dropna(subset=["stkPx"])
                .drop_duplicates("trade_date")
                .sort_values("trade_date")
                .set_index("trade_date"))
    if len(daily) < 200:
        return None
    daily["ma50"] = daily["stkPx"].rolling(50, min_periods=30).mean()
    daily["ma200"] = daily["stkPx"].rolling(200, min_periods=100).mean()
    daily["above_ma50"] = daily["stkPx"] > daily["ma50"]
    daily["above_ma200"] = daily["stkPx"] > daily["ma200"]
    return daily


def _summarize(label: str, df: pd.DataFrame, pnl_col: str) -> dict:
    n = len(df)
    if n == 0:
        return {"cell": label, "n": 0, "mean": np.nan, "median": np.nan,
                "win_rate": np.nan, "total": 0.0}
    pnl = df[pnl_col]
    return {
        "cell": label,
        "n": int(n),
        "mean": round(float(pnl.mean()), 4),
        "median": round(float(pnl.median()), 4),
        "win_rate": round(float((pnl > 0).mean()), 3),
        "total": round(float(pnl.sum()), 2),
    }


def main() -> int:
    cycles = pd.read_parquet(CYCLES_IN)
    cycles["entry_date"] = pd.to_datetime(cycles["entry_date"])
    cycles["expiration"] = pd.to_datetime(cycles["expiration"])
    cycles["mgd50_exit_date"] = pd.to_datetime(cycles["mgd50_exit_date"])
    print(f"Loaded {len(cycles):,} bull_put cycles across "
          f"{cycles['ticker'].nunique()} tickers")

    # Focus on OTM (the standard deployment), held to expiry as headline
    otm = cycles[cycles["moneyness"] == "OTM"].copy()
    print(f"OTM cycles: {len(otm):,}\n")

    tagged_rows = []
    n_tickers_with_data = 0
    n_tickers_skipped = 0
    for ticker, sub in otm.groupby("ticker"):
        daily = _ticker_daily_series(ticker)
        if daily is None:
            n_tickers_skipped += 1
            continue
        n_tickers_with_data += 1

        for _, row in sub.iterrows():
            entry = row["entry_date"]
            expiry = row["expiration"]
            if entry not in daily.index:
                # Find nearest trading day on or before
                idx = daily.index.searchsorted(entry)
                if idx == 0:
                    continue
                entry_row = daily.iloc[idx - 1]
                entry_dt = daily.index[idx - 1]
            else:
                entry_row = daily.loc[entry]
                entry_dt = entry

            if pd.isna(entry_row.get("ma50")) or pd.isna(entry_row.get("ma200")):
                continue

            entry_above_ma50 = bool(entry_row["above_ma50"])
            entry_above_ma200 = bool(entry_row["above_ma200"])

            # Forward window: entry+1 to expiration
            mask = (daily.index > entry_dt) & (daily.index <= expiry)
            window = daily[mask]
            if window.empty:
                continue

            # Did spot cross BELOW each MA during the hold?
            cross_below_ma50 = bool((~window["above_ma50"].fillna(True)).any())
            cross_below_ma200 = bool((~window["above_ma200"].fillna(True)).any())

            tagged_rows.append({
                **row.to_dict(),
                "entry_above_ma50": entry_above_ma50,
                "entry_above_ma200": entry_above_ma200,
                "cross_below_ma50_during_hold": cross_below_ma50,
                "cross_below_ma200_during_hold": cross_below_ma200,
            })

    print(f"Tickers with sufficient MA history: {n_tickers_with_data}, "
          f"skipped: {n_tickers_skipped}")
    df = pd.DataFrame(tagged_rows)
    print(f"Tagged cycles: {len(df):,}\n")

    # ─── Headline cells ──
    rows_summary = []

    for ma_label, entry_col, cross_col in [
        ("200-DMA", "entry_above_ma200", "cross_below_ma200_during_hold"),
        ("50-DMA",  "entry_above_ma50",  "cross_below_ma50_during_hold"),
    ]:
        print("=" * 86)
        print(f"  {ma_label}: bull_put cycles entered ABOVE the {ma_label}")
        print("=" * 86)
        above = df[df[entry_col] == True]
        n_above = len(above)
        if n_above == 0:
            print("  (no cycles)")
            continue
        n_crossed = int(above[cross_col].sum())
        n_not_crossed = n_above - n_crossed
        print(f"  Cycles entered above {ma_label}: {n_above:,}")
        print(f"    of which crossed below during hold: {n_crossed:,} "
              f"({n_crossed/n_above*100:.1f}%)")
        print(f"    of which stayed above through expiration: {n_not_crossed:,} "
              f"({n_not_crossed/n_above*100:.1f}%)")
        print()

        # Cell A: stayed above (no cross), held to expiry
        cell_a = above[above[cross_col] == False]
        # Cell B: crossed below, held to expiry
        cell_b = above[above[cross_col] == True]

        a_held = _summarize(f"A) stayed above {ma_label} — HELD", cell_a, "held_pnl")
        b_held = _summarize(f"B) crossed below {ma_label} — HELD", cell_b, "held_pnl")
        a_mgd = _summarize(f"A) stayed above {ma_label} — MGD50", cell_a, "mgd50_pnl")
        b_mgd = _summarize(f"B) crossed below {ma_label} — MGD50", cell_b, "mgd50_pnl")

        print(f"  {'Cell':50s}  {'N':>6s}  {'mean':>10s}  {'win':>6s}  {'total':>12s}")
        for c in [a_held, b_held, a_mgd, b_mgd]:
            print(f"  {c['cell']:50s}  {c['n']:>6d}  {c['mean']:>+10.4f}  "
                  f"{c['win_rate']:>6.3f}  {c['total']:>+12.2f}")
        print()

        # Delta cells: managed_50 vs held within crossed subset
        if cell_b is not None and len(cell_b) > 0:
            delta_held_to_mgd = b_mgd["mean"] - b_held["mean"]
            mgd_save_pct = ((b_mgd["mean"] - b_held["mean"]) / abs(b_held["mean"])
                             if b_held["mean"] != 0 else float("nan")) * 100
            print(f"  → If you got 'crossed during hold', mgd50 vs held = "
                  f"${delta_held_to_mgd:+.4f}/sh "
                  f"({'better' if delta_held_to_mgd > 0 else 'worse'} "
                  f"by {abs(mgd_save_pct):.1f}%)")
        print()

        rows_summary.extend([
            {"ma": ma_label, **a_held, "cross": False, "exit": "HELD"},
            {"ma": ma_label, **b_held, "cross": True,  "exit": "HELD"},
            {"ma": ma_label, **a_mgd,  "cross": False, "exit": "MGD50"},
            {"ma": ma_label, **b_mgd,  "cross": True,  "exit": "MGD50"},
        ])

    summary_df = pd.DataFrame(rows_summary)
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_parquet(OUT_PARQUET, index=False)
    print(f"\nWrote {OUT_PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
