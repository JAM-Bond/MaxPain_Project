#!/usr/bin/env python3.11
"""
ORATS by_ticker incremental update.

Reads the daily ORATS parquets at data/orats/parquet/year=YYYY/month=MM/
and appends any rows newer than each ticker's current max trade_date in
data/orats/by_ticker/{TICKER}.parquet.

Idempotent — safe to run daily via cron right after `ingest.py all`.
Designed to be cheap on no-op days: if no new daily parquets exist beyond
the universe-max-date, exits in seconds without touching any files.

Usage:
    python3.11 daily_extract.py            # update all tracked tickers
    python3.11 daily_extract.py --dry-run  # report only, no writes
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
DAILY_PARQUET = ROOT / "data/orats/parquet"

# Must match the by_ticker schema (verified 2026-05-02)
KEEP_COLS = [
    "ticker", "trade_date", "expirDate", "yte", "strike", "stkPx",
    "delta", "cBidPx", "cAskPx", "cMidIv", "cOi", "cVolu",
    "pBidPx", "pAskPx", "pMidIv", "pOi", "pVolu",
]
# Dedup key — protects against double-running on the same day
DEDUP_KEY = ["ticker", "trade_date", "expirDate", "strike"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daily_extract")


def find_new_daily_files(after_date: pd.Timestamp) -> list[Path]:
    """All daily parquet files with date > after_date, sorted ascending."""
    files = []
    for year_dir in sorted(DAILY_PARQUET.glob("year=*")):
        for month_dir in sorted(year_dir.glob("month=*")):
            for pq in sorted(month_dir.glob("*.parquet")):
                try:
                    date = pd.Timestamp(pq.stem)
                except Exception:
                    continue
                if date > after_date:
                    files.append(pq)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Report only — no parquet writes")
    args = ap.parse_args()

    if not BY_TICKER.exists():
        log.error("No by_ticker directory at %s — run extract.py first", BY_TICKER)
        sys.exit(1)

    tickers = sorted(p.stem for p in BY_TICKER.glob("*.parquet"))
    if not tickers:
        log.error("by_ticker is empty — nothing to update")
        sys.exit(1)
    log.info("Tracked tickers: %d", len(tickers))

    # Find the most-recent date currently in any by_ticker file. New daily
    # parquets ABOVE this date are candidates for incremental ingest.
    overall_max = pd.Timestamp("1900-01-01")
    for t in tickers:
        df = pd.read_parquet(BY_TICKER / f"{t}.parquet", columns=["trade_date"])
        if not df.empty:
            tmax = df["trade_date"].max()
            if tmax > overall_max:
                overall_max = tmax
    log.info("Universe-max date currently in by_ticker: %s", overall_max.date())

    new_files = find_new_daily_files(overall_max)
    if not new_files:
        log.info("No new daily parquets — by_ticker already current.")
        return

    log.info("Found %d new daily parquet(s) to merge in: %s … %s",
             len(new_files), new_files[0].stem, new_files[-1].stem)

    # Stream new rows, group by ticker, accumulate new frames per ticker
    ticker_set = set(tickers)
    accumulated: dict[str, list[pd.DataFrame]] = {t: [] for t in tickers}
    for pq in new_files:
        try:
            df = pd.read_parquet(pq, columns=KEEP_COLS)
        except Exception as e:
            log.warning("Skipping %s: %s", pq.name, e)
            continue
        df = df[df["ticker"].isin(ticker_set)]
        for t, grp in df.groupby("ticker"):
            accumulated[t].append(grp)

    # Append per-ticker
    updated = 0
    rows_total = 0
    for t in tickers:
        grps = accumulated[t]
        if not grps:
            continue
        new_df = pd.concat(grps, ignore_index=True)
        if new_df.empty:
            continue
        path = BY_TICKER / f"{t}.parquet"
        if args.dry_run:
            log.info("  [dry-run] %s: would append %d rows", t, len(new_df))
            updated += 1
            rows_total += len(new_df)
            continue
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        before = len(combined)
        combined = combined.drop_duplicates(subset=DEDUP_KEY)
        combined = combined.sort_values(["trade_date", "expirDate", "strike"]).reset_index(drop=True)
        dedup_dropped = before - len(combined)
        combined.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
        updated += 1
        rows_total += len(new_df) - dedup_dropped

    if args.dry_run:
        log.info("[dry-run] Would update %d ticker files (~%d new rows total)", updated, rows_total)
    else:
        log.info("Updated %d ticker files; appended %d new rows total", updated, rows_total)


if __name__ == "__main__":
    main()
