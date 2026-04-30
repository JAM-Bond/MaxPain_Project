#!/usr/bin/env python3.11
"""
52-week-extremes forward-return scan over the ORATS universe.

For every ticker in data/orats/by_ticker/, identify first-touch 52w highs and
52w lows on daily close (252 trading-day window, 10-day cooldown). For each
event, compute forward 5d/10d/20d/60d returns, realized vol, max upside,
max drawdown. Build a same-ticker non-event baseline for comparison.

Outputs:
  - data/profile/scan_52w_extremes_events.parquet  (one row per event)
  - data/profile/scan_52w_extremes_summary.parquet (aggregated by event type)
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
TICKER_DIR = ROOT / "data/orats/by_ticker"
OUT_EVENTS = ROOT / "data/profile/scan_52w_extremes_events.parquet"
OUT_SUMMARY = ROOT / "data/profile/scan_52w_extremes_summary.parquet"

LOOKBACK = 252
COOLDOWN = 10
HORIZONS = [5, 10, 20, 60]
BASELINE_PER_TICKER = 200  # random non-event days per ticker
RNG = np.random.default_rng(seed=20260429)


def daily_prices(parquet_path: Path) -> pd.DataFrame:
    """Load ticker parquet → one row per (trade_date, stkPx). Sorted ascending."""
    df = pd.read_parquet(parquet_path, columns=["trade_date", "stkPx"])
    df = (df.dropna(subset=["stkPx"])
            .drop_duplicates("trade_date")
            .sort_values("trade_date")
            .reset_index(drop=True))
    return df


def flag_extremes(prices: pd.DataFrame) -> pd.DataFrame:
    """Add is_52w_high / is_52w_low boolean columns with cooldown applied."""
    p = prices.copy()
    p["roll_max"] = p["stkPx"].rolling(LOOKBACK, min_periods=LOOKBACK).max()
    p["roll_min"] = p["stkPx"].rolling(LOOKBACK, min_periods=LOOKBACK).min()
    raw_high = (p["stkPx"] >= p["roll_max"] - 1e-9) & p["roll_max"].notna()
    raw_low = (p["stkPx"] <= p["roll_min"] + 1e-9) & p["roll_min"].notna()

    # Cooldown: suppress if a high/low fired in prior COOLDOWN days
    p["is_52w_high"] = False
    p["is_52w_low"] = False
    last_high = -COOLDOWN - 1
    last_low = -COOLDOWN - 1
    for i in range(len(p)):
        if raw_high.iloc[i] and (i - last_high) > COOLDOWN:
            p.at[i, "is_52w_high"] = True
            last_high = i
        if raw_low.iloc[i] and (i - last_low) > COOLDOWN:
            p.at[i, "is_52w_low"] = True
            last_low = i
    return p


def forward_metrics(prices: pd.DataFrame, idx: int, horizon: int) -> dict | None:
    """For event at row `idx`, compute forward-window metrics over `horizon` days."""
    end = idx + horizon
    if end >= len(prices):
        return None
    p0 = prices["stkPx"].iat[idx]
    window = prices["stkPx"].iloc[idx + 1:end + 1]
    if len(window) < horizon or p0 <= 0:
        return None
    end_price = window.iat[-1]
    ret = (end_price - p0) / p0
    log_returns = np.log(window.values / np.r_[p0, window.values[:-1]])
    vol = float(np.std(log_returns, ddof=1) * np.sqrt(252)) if len(log_returns) > 1 else np.nan
    max_up = float((window.max() - p0) / p0)
    max_dd = float((window.min() - p0) / p0)
    return {
        f"fwd_{horizon}d_ret": float(ret),
        f"fwd_{horizon}d_vol": vol,
        f"fwd_{horizon}d_max": max_up,
        f"fwd_{horizon}d_dd": max_dd,
    }


def build_event_rows(ticker: str, prices: pd.DataFrame) -> list[dict]:
    """Walk the price series, emit one row per event with forward metrics."""
    flagged = flag_extremes(prices)
    rows = []

    event_idxs = []
    for i in range(len(flagged)):
        if flagged["is_52w_high"].iat[i]:
            event_idxs.append((i, "52w_high"))
        elif flagged["is_52w_low"].iat[i]:
            event_idxs.append((i, "52w_low"))

    event_set = {i for i, _ in event_idxs}

    for i, etype in event_idxs:
        row = {
            "ticker": ticker,
            "event_date": flagged["trade_date"].iat[i],
            "event_type": etype,
            "price": float(flagged["stkPx"].iat[i]),
        }
        for h in HORIZONS:
            metrics = forward_metrics(flagged, i, h)
            if metrics is None:
                row.update({k: np.nan for k in (
                    f"fwd_{h}d_ret", f"fwd_{h}d_vol", f"fwd_{h}d_max", f"fwd_{h}d_dd")})
            else:
                row.update(metrics)
        rows.append(row)

    # Baseline: random non-event days, with cooldown to keep them clean
    eligible = [i for i in range(LOOKBACK, len(flagged) - max(HORIZONS))
                if i not in event_set
                and not any(j in event_set for j in range(max(0, i - COOLDOWN),
                                                         min(len(flagged), i + COOLDOWN)))]
    n_sample = min(BASELINE_PER_TICKER, len(eligible))
    if n_sample > 0:
        sampled = RNG.choice(eligible, size=n_sample, replace=False)
        for i in sampled:
            row = {
                "ticker": ticker,
                "event_date": flagged["trade_date"].iat[i],
                "event_type": "baseline",
                "price": float(flagged["stkPx"].iat[i]),
            }
            for h in HORIZONS:
                metrics = forward_metrics(flagged, i, h)
                if metrics is None:
                    continue
                row.update(metrics)
            rows.append(row)

    return rows


def aggregate(events: pd.DataFrame) -> pd.DataFrame:
    """Per-event-type summary across all tickers."""
    agg_rows = []
    for etype, grp in events.groupby("event_type"):
        for h in HORIZONS:
            ret = grp[f"fwd_{h}d_ret"].dropna()
            vol = grp[f"fwd_{h}d_vol"].dropna()
            max_up = grp[f"fwd_{h}d_max"].dropna()
            max_dd = grp[f"fwd_{h}d_dd"].dropna()
            if len(ret) == 0:
                continue
            agg_rows.append({
                "event_type": etype,
                "horizon_d": h,
                "n": int(len(ret)),
                "mean_ret": float(ret.mean()),
                "median_ret": float(ret.median()),
                "win_rate": float((ret > 0).mean()),
                "std_ret": float(ret.std()),
                "p_move_5pct": float((ret.abs() > 0.05).mean()),
                "p_move_10pct": float((ret.abs() > 0.10).mean()),
                "mean_vol": float(vol.mean()),
                "mean_max_up": float(max_up.mean()),
                "mean_max_dd": float(max_dd.mean()),
                "p_dd_lt_neg5pct": float((max_dd < -0.05).mean()),
                "p_dd_lt_neg10pct": float((max_dd < -0.10).mean()),
                "p_up_gt_5pct": float((max_up > 0.05).mean()),
                "p_up_gt_10pct": float((max_up > 0.10).mean()),
            })
    return pd.DataFrame(agg_rows).sort_values(["event_type", "horizon_d"])


def main():
    ticker_files = sorted(TICKER_DIR.glob("*.parquet"))
    print(f"Scanning {len(ticker_files)} tickers...")

    all_rows = []
    for i, f in enumerate(ticker_files, 1):
        ticker = f.stem
        try:
            prices = daily_prices(f)
            if len(prices) < LOOKBACK + max(HORIZONS) + 10:
                continue
            rows = build_event_rows(ticker, prices)
            all_rows.extend(rows)
            if i % 25 == 0:
                print(f"  {i}/{len(ticker_files)}: {ticker} ({len(rows)} rows)")
        except Exception as e:
            print(f"  skip {ticker}: {e}")
            continue

    events = pd.DataFrame(all_rows)
    events.to_parquet(OUT_EVENTS, index=False)
    print(f"\nEvents: {len(events)} rows → {OUT_EVENTS}")
    print(events["event_type"].value_counts())

    summary = aggregate(events)
    summary.to_parquet(OUT_SUMMARY, index=False)
    print(f"\nSummary → {OUT_SUMMARY}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
