"""Empirically calibrate per-name move thresholds for the daily alert.

For every ticker in the live trading universe — all gate_config cohorts (bull_put,
bear_call, inverted_fly, zebra tiers, earnings carve-outs, …), the v1.5 research
cohort, SPY/QQQ/VIX, and any names with open positions — compute percentiles of
daily absolute return from ORATS history. Store results in the alert_thresholds
table. Names with no ORATS history are skipped and fall back to the alert's flat
default (the alert labels those honestly). Refresh quarterly or on cohort change.

Output table schema (alert_thresholds in maxpain.db):
  ticker, n_days, p75, p90, p95, p99, refreshed_at

Usage:
  python3.11 compute_alert_thresholds.py
  python3.11 compute_alert_thresholds.py --ticker SPY QQQ
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
COHORT_PATH = ROOT / "data/profile/research_cohort_v15.parquet"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alert_thresh")


def gate_config_symbols() -> set[str]:
    """Union of every COHORT_* list in the qualifier's gate_config — the full live
    trading universe across all structures. Future-proof: picks up any cohort added
    later. Soft-fails to an empty set so calibration never breaks on an import error."""
    try:
        from scripts.qualifier import gate_config as gc
    except Exception as e:  # noqa: BLE001
        log.warning("gate_config import failed (%s); cohort universe limited to research set", e)
        return set()
    syms: set[str] = set()
    for name in dir(gc):
        if name.startswith("COHORT_"):
            val = getattr(gc, name)
            if isinstance(val, list):
                syms.update(s for s in val if isinstance(s, str) and s)
    return syms


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


def recalibrate(only: list[str] | None = None) -> list[dict]:
    """Compute + persist move thresholds for the full universe (v1.5 research
    cohort + every gate_config cohort + SPY/QQQ/VIX + open positions). Returns the
    rows written. `only` restricts to a subset. No printing — safe to call from
    other pipelines (e.g. the nightly auto-promotion hook)."""
    cohort = pd.read_parquet(COHORT_PATH)["ticker"].tolist()
    # Always include SPY/VIX for regime context
    base = set(cohort + ["SPY", "QQQ", "VIX"])
    # Add the full live trading universe — every COHORT_* list in gate_config — so
    # all cohort names get calibrated thresholds, not just the v1.5 research set.
    gc_syms = gate_config_symbols()
    if gc_syms:
        log.info("Adding %d gate_config cohort symbols (%d new vs research set)",
                 len(gc_syms), len(gc_syms - base))
        base |= gc_syms
    # Pull in any tickers with currently-open positions in either trade_log
    # or spread_score_trades (so the alert has thresholds for the live paper book
    # even when those names aren't in any cohort).
    conn = sqlite3.connect(DB_PATH)
    open_syms = set()
    for tbl, where in [("trade_log", "exit_date IS NULL OR exit_price IS NULL"),
                        ("spread_score_trades", "exit_date IS NULL")]:
        try:
            rows_ = conn.execute(f"SELECT DISTINCT symbol FROM {tbl} WHERE {where}").fetchall()
            open_syms.update(r[0] for r in rows_ if r[0])
        except Exception:
            pass
    conn.close()
    if open_syms - base:
        log.info("Adding %d open-position symbols outside cohort: %s",
                 len(open_syms - base), sorted(open_syms - base))
    base |= open_syms
    targets = sorted(base)
    if only:
        targets = [t for t in targets if t in set(only)]

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
        return []
    write_table(rows)
    log.info("Wrote %d rows to alert_thresholds", len(rows))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", nargs="+", help="Subset of cohort to recompute")
    args = parser.parse_args()

    rows = recalibrate(only=args.ticker)
    if not rows:
        return

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
