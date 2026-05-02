#!/usr/bin/env python3.11
"""
Universe expansion v2 — extract liquidity-passing candidates from the
partitioned ORATS archive into per-ticker parquet files in by_ticker/.

Reads candidate list from data/profile/universe_v2_liquidity_pool.parquet
and streams the partitioned archive year-by-year. After each year, flushes
per-ticker rows to per-ticker per-year temp parquets in a sandboxed dir,
freeing memory before the next year. At the end, concatenates each
ticker's per-year temps into the final by_ticker/<ticker>.parquet.

Memory peak: one year of all candidates' rows in memory at once
(~50-200 MB).

Idempotent: skips tickers that already have a final parquet. Temp
files in data/orats/by_ticker_v2_temp/ are cleaned after successful
promotion.

Usage:
    python3.11 -m scripts.maintenance.universe_v2_extract_candidates
    python3.11 -m scripts.maintenance.universe_v2_extract_candidates --limit 5
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
DAILY_PARQUET = ROOT / "data/orats/parquet"
POOL = ROOT / "data/profile/universe_v2_liquidity_pool.parquet"
TEMP_ROOT = ROOT / "data/orats/by_ticker_v2_temp"

KEEP_COLS = [
    "ticker", "trade_date", "expirDate", "yte", "strike", "stkPx",
    "delta", "cBidPx", "cAskPx", "cMidIv", "cOi", "cVolu",
    "pBidPx", "pAskPx", "pMidIv", "pOi", "pVolu",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("universe_v2_extract")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Only extract top N candidates by sum_oi (testing)")
    ap.add_argument("--keep-temp", action="store_true",
                    help="Keep per-year temp files after promotion (debug)")
    args = ap.parse_args()

    if not POOL.exists():
        log.error("Liquidity pool not found at %s", POOL)
        sys.exit(1)
    pool = pd.read_parquet(POOL).sort_values("sum_oi", ascending=False)
    if args.limit:
        pool = pool.head(args.limit)
    candidates = pool["ticker"].tolist()
    log.info("Candidates to extract: %d", len(candidates))

    BY_TICKER.mkdir(parents=True, exist_ok=True)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    already = {p.stem for p in BY_TICKER.glob("*.parquet")}
    todo = [t for t in candidates if t not in already]
    skipped = len(candidates) - len(todo)
    log.info("Already in by_ticker/: %d  |  To extract: %d", skipped, len(todo))
    if not todo:
        return

    todo_set = set(todo)

    # Year-by-year scan, flush per-year temps at end of each year
    year_dirs = sorted(DAILY_PARQUET.glob("year=*"))
    for year_dir in year_dirs:
        year = year_dir.name
        t0 = time.time()
        accumulated: dict[str, list[pd.DataFrame]] = {}
        rows_in_year = 0

        for month_dir in sorted(year_dir.glob("month=*")):
            for pqf in sorted(month_dir.glob("*.parquet")):
                try:
                    df = pd.read_parquet(pqf, columns=KEEP_COLS)
                except Exception as e:
                    log.warning("skip %s: %s", pqf.name, e)
                    continue
                df = df.dropna(subset=["ticker"])
                df = df[df["ticker"].isin(todo_set)]
                if df.empty:
                    continue
                rows_in_year += len(df)
                for t, grp in df.groupby("ticker", observed=True):
                    accumulated.setdefault(t, []).append(grp)

        # Flush this year's accumulated data to per-ticker per-year temps
        flushed = 0
        for t, grps in accumulated.items():
            year_df = pd.concat(grps, ignore_index=True)
            t_dir = TEMP_ROOT / t
            t_dir.mkdir(exist_ok=True)
            year_df.to_parquet(t_dir / f"{year}.parquet",
                              engine="pyarrow", compression="snappy", index=False)
            flushed += 1
        log.info("  %s — %s rows across %d tickers — %.1fs",
                 year, f"{rows_in_year:,}", flushed, time.time() - t0)
        del accumulated

    # Final: concat per-ticker year files into by_ticker/<ticker>.parquet
    log.info("Promoting per-ticker temp files to final by_ticker/...")
    promoted = 0
    rows_total = 0
    for t in todo:
        t_dir = TEMP_ROOT / t
        year_files = sorted(t_dir.glob("year=*.parquet")) if t_dir.exists() else []
        if not year_files:
            log.warning("  %s: 0 rows ingested — verify ticker symbol", t)
            continue
        df = pd.concat([pd.read_parquet(f) for f in year_files], ignore_index=True)
        df = df.sort_values(["trade_date", "expirDate", "strike"]).reset_index(drop=True)
        final = BY_TICKER / f"{t}.parquet"
        df.to_parquet(final, engine="pyarrow", compression="snappy", index=False)
        promoted += 1
        rows_total += len(df)
        log.info("  ✓ %s: %s rows", t, f"{len(df):,}")
        if not args.keep_temp:
            shutil.rmtree(t_dir)

    if not args.keep_temp:
        # Remove the temp root if empty
        try:
            TEMP_ROOT.rmdir()
        except OSError:
            pass

    log.info("Promoted %d ticker files; %s total rows", promoted, f"{rows_total:,}")


if __name__ == "__main__":
    main()
