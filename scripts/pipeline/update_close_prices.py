#!/usr/bin/env python3.11
"""
MaxPain — Post-close price update for live_snapshots
~/MaxPain_Project/scripts/pipeline/update_close_prices.py

Lightweight cron job: runs at 4:16 PM ET on weekdays to update the
current_price column in today's live_snapshots rows with the actual
closing trade. The 9:20 AM cron captures OI/gamma/max-pain (which need
the 8:30-9:00 AM OI refresh) but the 9:20 price is the prior day's
close. This script fixes the lag so the 4:45 PM daily alert sees
today's actual close.

Cohort source: union of every COHORT_* list in
scripts/qualifier/gate_config.py (auto-cascades on cohort edits). SPX
excluded — Schwab equity-quote endpoint doesn't handle index tickers.

Schwab quotes are primary; yfinance fallback for any misses. Only
updates rows that already exist for today — does NOT create new
snapshots. If the morning capture failed for a symbol, this script
silently skips it.

Usage:
  python3.11 update_close_prices.py            # full cohort, today
  python3.11 update_close_prices.py --dry-run  # show changes, no write
  python3.11 update_close_prices.py --symbol SPY GOOGL  # subset
  python3.11 update_close_prices.py --date 2026-04-24   # specific date
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH, connect  # noqa: E402
from lib.schwab_quotes import fetch_quotes  # noqa: E402

try:
    import yfinance as yf
except ImportError:
    yf = None

ROOT = Path.home() / "MaxPain_Project"

# SPX excluded — Schwab equity-quote endpoint doesn't handle index tickers
SKIP = {"SPX"}


def fetch_yfinance_prices(symbols: list[str]) -> dict[str, float]:
    """Fallback: per-symbol yfinance fetch. Slower but always available."""
    if yf is None or not symbols:
        return {}
    prices: dict[str, float] = {}
    for sym in symbols:
        try:
            t = yf.Ticker(sym)
            price = t.fast_info.get("last_price")
            if not price:
                info = t.info
                price = info.get("regularMarketPrice") or info.get("previousClose")
            if price:
                prices[sym] = round(float(price), 4)
        except Exception as e:
            print(f"  yfinance failed for {sym}: {e}")
    return prices


def update_prices(prices: dict[str, float], date_str: str, dry_run: bool = False) -> int:
    """Update current_price in live_snapshots for the given date.

    Returns count of rows actually updated.
    """
    conn = connect()
    updated = 0
    for sym, price in prices.items():
        row = conn.execute(
            "SELECT current_price FROM live_snapshots "
            "WHERE symbol = ? AND snapshot_date = ?",
            (sym, date_str),
        ).fetchone()
        if row is None:
            print(f"  {sym}: no row for {date_str}, skipping")
            continue
        old = row[0]
        if dry_run:
            print(f"  {sym}: ${old} → ${price} (dry run)")
        else:
            conn.execute(
                "UPDATE live_snapshots "
                "SET current_price = ? WHERE symbol = ? AND snapshot_date = ?",
                (price, sym, date_str),
            )
            print(f"  {sym}: ${old} → ${price}")
        updated += 1
    if not dry_run:
        conn.commit()
    conn.close()
    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", nargs="+", default=None,
                        help="Subset of cohort to update")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", default=None,
                        help="Snapshot date (default: today, YYYY-MM-DD)")
    args = parser.parse_args()

    today_str = args.date or str(date.today())

    if args.symbol:
        symbols = [s.upper() for s in args.symbol if s.upper() not in SKIP]
    else:
        from scripts.qualifier import gate_config as G
        union: set[str] = set()
        for attr in dir(G):
            if attr.startswith("COHORT_"):
                v = getattr(G, attr)
                if isinstance(v, (list, tuple, set)):
                    union.update(v)
        symbols = sorted(s for s in union if s not in SKIP)

    if not symbols:
        print("No symbols to update.")
        return

    print(f"Updating close prices for {today_str} ({len(symbols)} symbols)")

    # Schwab batched quote (one call for all)
    schwab = fetch_quotes(symbols)
    print(f"  Schwab: {len(schwab)}/{len(symbols)} quotes returned")

    # yfinance fallback for any misses
    missing = [s for s in symbols if s not in schwab]
    yf_prices = fetch_yfinance_prices(missing) if missing else {}
    if yf_prices:
        print(f"  yfinance fallback: {len(yf_prices)}/{len(missing)} quotes")

    prices = {**schwab, **yf_prices}
    still_missing = [s for s in symbols if s not in prices]
    if still_missing:
        print(f"  WARNING: no quote for {', '.join(still_missing)}")

    # Trap silent failure: a data outage that drops most quotes would leave the
    # 4:45 alert running on stale prices. Exit non-zero so run_cron alerts.
    if not prices:
        print("✗ No prices fetched — exiting 1 so cron traps it.")
        sys.exit(1)
    if len(still_missing) > len(symbols) // 2:
        print(f"✗ {len(still_missing)}/{len(symbols)} symbols missing quotes "
              f"(>50%) — likely a data outage. Exiting 1 so cron traps it.")
        sys.exit(1)

    n = update_prices(prices, today_str, dry_run=args.dry_run)
    status = "DRY RUN" if args.dry_run else "DONE"
    print(f"{status}: {n}/{len(symbols)} rows updated")


if __name__ == "__main__":
    main()
