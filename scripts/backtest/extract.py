#!/usr/bin/env python3.11
"""Extract per-ticker parquet files from the partitioned ORATS archive.

Single-pass streaming: reads each daily parquet once, splits into per-ticker frames,
flushes year-by-year to per-year-per-ticker temp files, then coalesces to one
final parquet per ticker at end.

Output: data/orats/by_ticker/{TICKER}.parquet (one file per universe ticker)
Intermediate: data/orats/by_ticker/_tmp/{TICKER}_{YEAR}.parquet (cleaned up at end)

Usage:
    python3.11 extract.py              # extract all universe tickers
    python3.11 extract.py --force      # re-extract even if output exists
"""
import argparse
import logging
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C


C.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(C.LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("extract")


KEEP_COLS = [
    "ticker", "trade_date", "expirDate", "yte", "strike",
    "stkPx", "delta",
    "cBidPx", "cAskPx", "cMidIv", "cOi", "cVolu",
    "pBidPx", "pAskPx", "pMidIv", "pOi", "pVolu",
]


def process_year(year_dir: Path, tickers: set[str], tmp_root: Path) -> dict[str, int]:
    """Read all daily parquets for a year, split per-ticker, write temp files."""
    row_counts = defaultdict(int)
    per_ticker: dict[str, list[pd.DataFrame]] = defaultdict(list)

    daily_files = []
    for month_dir in sorted(year_dir.iterdir()):
        if month_dir.is_dir():
            daily_files.extend(sorted(month_dir.glob("*.parquet")))

    year_label = year_dir.name.replace("year=", "")
    log.info("  year %s: %d daily files", year_label, len(daily_files))

    for i, pq in enumerate(daily_files, 1):
        df = pd.read_parquet(pq, columns=KEEP_COLS)
        df = df[df["ticker"].isin(tickers)]
        for ticker, sub in df.groupby("ticker", sort=False):
            per_ticker[ticker].append(sub)
        if i % 50 == 0 or i == len(daily_files):
            log.info("    [%d/%d]", i, len(daily_files))

    tmp_root.mkdir(parents=True, exist_ok=True)
    for ticker, frames in per_ticker.items():
        if not frames:
            continue
        year_df = pd.concat(frames, ignore_index=True)
        year_df["trade_date"] = pd.to_datetime(year_df["trade_date"])
        out = tmp_root / f"{ticker}_{year_label}.parquet"
        year_df.to_parquet(out, engine="pyarrow", compression="snappy", index=False)
        row_counts[ticker] = len(year_df)
    return row_counts


def coalesce_ticker(ticker: str, tmp_root: Path) -> int:
    parts = sorted(tmp_root.glob(f"{ticker}_*.parquet"))
    if not parts:
        return 0
    frames = [pd.read_parquet(p) for p in parts]
    full = pd.concat(frames, ignore_index=True)
    full["trade_date"] = pd.to_datetime(full["trade_date"])
    full = full.sort_values(["trade_date", "expirDate", "strike"]).reset_index(drop=True)
    out_path = C.BY_TICKER_ROOT / f"{ticker}.parquet"
    full.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)
    for p in parts:
        p.unlink()
    return len(full)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    universe = pd.read_parquet(C.UNIVERSE_PATH)
    tickers = set(universe["ticker"].tolist())
    log.info("Universe: %d tickers", len(tickers))

    if not args.force:
        existing = {p.stem for p in C.BY_TICKER_ROOT.glob("*.parquet")}
        tickers = tickers - existing
        if not tickers:
            log.info("All tickers already extracted — nothing to do (use --force to re-extract)")
            return
        log.info("Extracting %d new tickers (skipping %d existing)", len(tickers), len(existing))

    C.BY_TICKER_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_root = C.BY_TICKER_ROOT / "_tmp"

    year_dirs = sorted([d for d in C.PARQUET_ROOT.iterdir() if d.is_dir() and d.name.startswith("year=")])
    log.info("Streaming %d year directories", len(year_dirs))

    for year_dir in year_dirs:
        process_year(year_dir, tickers, tmp_root)

    log.info("Coalescing per-year temp files into per-ticker outputs...")
    total_rows = 0
    for i, t in enumerate(sorted(tickers), 1):
        n = coalesce_ticker(t, tmp_root)
        total_rows += n
        if i % 10 == 0 or i == len(tickers):
            log.info("  [%d/%d] %s: %d rows", i, len(tickers), t, n)

    if tmp_root.exists() and not any(tmp_root.iterdir()):
        shutil.rmtree(tmp_root)

    log.info("Done. Total rows across %d tickers: %d", len(tickers), total_rows)


if __name__ == "__main__":
    main()
