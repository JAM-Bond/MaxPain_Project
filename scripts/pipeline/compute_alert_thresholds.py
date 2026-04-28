"""Empirically calibrate per-name move thresholds for the daily alert.

For each ticker in the v1.5 research cohort (+ any extra names with open
positions), compute percentiles of daily absolute return from ORATS history.
Store results in alert_thresholds table. Refresh quarterly or on cohort change.

Output table schema (alert_thresholds in metal_project.db):
  ticker, n_days, p75, p90, p95, p99, refreshed_at

Usage:
  python3.11 compute_alert_thresholds.py
  python3.11 compute_alert_thresholds.py --ticker SPY QQQ
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
COHORT_PATH = ROOT / "data/profile/research_cohort_v15.parquet"
DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alert_thresh")


def compute_one(ticker: str) -> dict | None:
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["trade_date", "stkPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    daily = df.drop_duplicates("trade_date").set_index("trade_date")["stkPx"].sort_index()
    if len(daily) < 100:
        return None
    ret = daily.pct_change().dropna()
    abs_ret = ret.abs()
    return {
        "ticker": ticker,
        "n_days": int(len(abs_ret)),
        "p75": float(abs_ret.quantile(0.75)),
        "p90": float(abs_ret.quantile(0.90)),
        "p95": float(abs_ret.quantile(0.95)),
        "p99": float(abs_ret.quantile(0.99)),
    }


def write_table(rows: list[dict]) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_thresholds (
            ticker TEXT PRIMARY KEY,
            n_days INTEGER,
            p75 REAL, p90 REAL, p95 REAL, p99 REAL,
            refreshed_at TEXT DEFAULT (datetime('now'))
        )
    """)
    cols = ["ticker", "n_days", "p75", "p90", "p95", "p99"]
    placeholders = ", ".join(["?"] * len(cols))
    for r in rows:
        cur.execute(
            f"INSERT OR REPLACE INTO alert_thresholds ({', '.join(cols)}) "
            f"VALUES ({placeholders})",
            [r.get(c) for c in cols],
        )
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", nargs="+", help="Subset of cohort to recompute")
    args = parser.parse_args()

    cohort = pd.read_parquet(COHORT_PATH)["ticker"].tolist()
    # Always include SPY/VIX for regime context
    base = set(cohort + ["SPY", "QQQ", "VIX"])
    # Pull in any tickers with currently-open positions in either Metal_Project
    # trade_log or spread_score_trades (so the alert has thresholds for live
    # paper book even when those names aren't in the v1.5 cohort).
    conn = sqlite3.connect(DB_PATH)
    open_syms = set()
    for tbl, where in [("trade_log", "exit_date IS NULL OR exit_price IS NULL"),
                        ("spread_score_trades", "exit_date IS NULL")]:
        try:
            rows = conn.execute(f"SELECT DISTINCT symbol FROM {tbl} WHERE {where}").fetchall()
            open_syms.update(r[0] for r in rows if r[0])
        except Exception:
            pass
    conn.close()
    if open_syms:
        log.info("Adding %d open-position symbols outside cohort: %s",
                 len(open_syms - base), sorted(open_syms - base))
        base |= open_syms
    targets = sorted(base)
    if args.ticker:
        targets = [t for t in targets if t in args.ticker]

    log.info("Calibrating thresholds for %d tickers", len(targets))
    rows = []
    for t in targets:
        result = compute_one(t)
        if result is None:
            log.warning("  %s: insufficient history, skip", t)
            continue
        rows.append(result)

    if not rows:
        log.error("No tickers calibrated")
        return

    write_table(rows)
    log.info("Wrote %d rows to alert_thresholds", len(rows))

    # Display summary
    df = pd.DataFrame(rows).sort_values("p95", ascending=False)
    print("\n" + "=" * 78)
    print("Calibrated thresholds (sorted by p95 desc)")
    print("=" * 78)
    print(f"{'ticker':>6} {'N':>6} {'p75':>8} {'p90':>8} {'p95':>8} {'p99':>8}")
    for _, r in df.iterrows():
        print(f"{r['ticker']:>6} {int(r['n_days']):>6} "
              f"{r['p75']*100:>7.2f}% {r['p90']*100:>7.2f}% "
              f"{r['p95']*100:>7.2f}% {r['p99']*100:>7.2f}%")


if __name__ == "__main__":
    main()
