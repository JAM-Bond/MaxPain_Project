"""Extract per-ticker parquets for inverted_fly universe expansion.

Reads the full ORATS parquet dump, filters to target tickers, writes to by_ticker/{TICKER}.parquet
in the same schema as the existing 150 universe files.

Target tickers: Mag 7 gaps + commodity-cyclical niche.
"""
from pathlib import Path
import logging
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path("/Users/josephmorris/MaxPain_Project")
PARQUET_ROOT = ROOT / "data/orats/parquet"
BY_TICKER = ROOT / "data/orats/by_ticker"

NEW_TICKERS = [
    # Mag 7 gaps
    "AMZN", "GOOGL", "NVDA",
    # Gold miners
    "GOLD", "AU", "KGC", "PAAS",
    # Oil services
    "SLB", "BKR",
    # Copper
    "SCCO", "TECK",
    # Steel
    "NUE", "STLD", "CLF",
]

KEEP_COLS = [
    "ticker", "expirDate", "yte", "strike",
    "stkPx", "delta",
    "cBidPx", "cAskPx", "cMidIv", "cOi", "cVolu",
    "pBidPx", "pAskPx", "pMidIv", "pOi", "pVolu",
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("extract_new")


def extract_date_from_filename(path: Path) -> str:
    """Parse YYYY-MM-DD from filename like 2024-03-01.parquet"""
    return path.stem


def main() -> None:
    target = set(NEW_TICKERS)
    log.info("Target tickers: %s", sorted(target))

    per_ticker_frames = {t: [] for t in target}
    year_dirs = sorted([d for d in PARQUET_ROOT.iterdir()
                         if d.is_dir() and d.name.startswith("year=")])
    log.info("Scanning %d year directories", len(year_dirs))

    total_files = 0
    for yd in year_dirs:
        for md in sorted(yd.iterdir()):
            if not md.is_dir():
                continue
            for pf in sorted(md.glob("*.parquet")):
                total_files += 1
                try:
                    df = pd.read_parquet(
                        pf, columns=KEEP_COLS,
                        filters=[("ticker", "in", list(target))],
                    )
                except Exception as e:
                    log.warning("skip %s: %s", pf, e)
                    continue
                if df.empty:
                    continue
                trade_date = pd.to_datetime(extract_date_from_filename(pf))
                df["trade_date"] = trade_date
                for t in target:
                    sub = df[df["ticker"] == t]
                    if not sub.empty:
                        per_ticker_frames[t].append(sub)
        log.info("  %s: done (%d files total so far)", yd.name, total_files)

    BY_TICKER.mkdir(parents=True, exist_ok=True)
    for t, frames in per_ticker_frames.items():
        if not frames:
            log.warning("%s: no rows found across archive", t)
            continue
        full = pd.concat(frames, ignore_index=True)
        full["trade_date"] = pd.to_datetime(full["trade_date"])
        full = full.sort_values(["trade_date", "expirDate", "strike"]).reset_index(drop=True)
        # Reorder columns to match existing by_ticker schema
        col_order = ["ticker", "trade_date", "expirDate", "yte", "strike",
                     "stkPx", "delta",
                     "cBidPx", "cAskPx", "cMidIv", "cOi", "cVolu",
                     "pBidPx", "pAskPx", "pMidIv", "pOi", "pVolu"]
        full = full[col_order]
        out = BY_TICKER / f"{t}.parquet"
        full.to_parquet(out, engine="pyarrow", compression="snappy", index=False)
        log.info("  %s: %d rows saved", t, len(full))

    log.info("Done. Scanned %d files.", total_files)


if __name__ == "__main__":
    main()
