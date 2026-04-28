#!/usr/bin/env python3.11
"""Symbol profile builder — two stages.

Stage 1 (daily):   Per-day × per-ticker feature rows written to daily_summary/YYYY-MM-DD.parquet.
                   Reads partitioned ORATS parquet; output is small (one row per ticker per day).

Stage 2 (profile): Aggregate daily summaries into per-ticker profile (one row per ticker).
                   Outputs profile_v1.parquet with medians, ranges, realized vol, coverage.

Usage:
    python3.11 build.py daily --year 2020
    python3.11 build.py daily --year 2020 --month 3
    python3.11 build.py profile
    python3.11 build.py all --year 2020
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
from features import FEATURES

C.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(C.LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("profile")


def daily_summary_for_file(parquet_path: Path) -> pd.DataFrame:
    """Compute one row per ticker for a single day's chain parquet."""
    df = pd.read_parquet(parquet_path)
    if df.empty:
        return pd.DataFrame()

    trade_date = pd.to_datetime(df["trade_date"].iloc[0]).date()
    out_rows = []
    for ticker, rows in df.groupby("ticker", sort=False):
        rec = {"ticker": ticker, "trade_date": trade_date}
        for name, fn in FEATURES.items():
            try:
                rec[name] = fn(rows)
            except Exception:
                rec[name] = np.nan
        out_rows.append(rec)
    return pd.DataFrame(out_rows)


def stage_daily(year: int, month: int | None = None) -> None:
    C.DAILY_DIR.mkdir(parents=True, exist_ok=True)
    year_dir = C.PARQUET_ROOT / f"year={year}"
    if not year_dir.exists():
        log.error("No parquet for year=%d at %s", year, year_dir)
        return

    months = [f"month={month:02d}"] if month else sorted(p.name for p in year_dir.iterdir() if p.is_dir())
    for mdir in months:
        month_path = year_dir / mdir
        if not month_path.exists():
            continue
        files = sorted(month_path.glob("*.parquet"))
        log.info("Year %d %s: %d daily files", year, mdir, len(files))
        for i, f in enumerate(files, 1):
            date_str = f.stem
            out_path = C.DAILY_DIR / f"{date_str}.parquet"
            if out_path.exists():
                continue
            summary = daily_summary_for_file(f)
            if summary.empty:
                log.warning("  empty summary for %s", f.name)
                continue
            summary.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)
            if i % 20 == 0 or i == len(files):
                log.info("  [%d/%d] %s — %d tickers", i, len(files), f.name, len(summary))


def stage_profile() -> None:
    """Aggregate all daily summaries into per-ticker profile."""
    files = sorted(C.DAILY_DIR.glob("*.parquet"))
    if not files:
        log.error("No daily summaries at %s — run `daily` stage first", C.DAILY_DIR)
        return
    log.info("Loading %d daily summary files", len(files))
    frames = [pd.read_parquet(f) for f in files]
    daily = pd.concat(frames, ignore_index=True)
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    log.info("Daily rows: %d  |  unique tickers: %d", len(daily), daily["ticker"].nunique())

    agg = (
        daily.groupby("ticker")
        .agg(
            history_days=("trade_date", "nunique"),
            first_date=("trade_date", "min"),
            last_date=("trade_date", "max"),
            median_total_oi=("total_oi", "median"),
            median_total_volume=("total_volume", "median"),
            median_n_expirations=("n_expirations", "median"),
            median_n_contracts=("n_contracts", "median"),
            median_stk_px=("stk_px", "median"),
            median_atm_iv=("atm_iv", "median"),
            p10_atm_iv=("atm_iv", lambda s: s.quantile(0.10)),
            p90_atm_iv=("atm_iv", lambda s: s.quantile(0.90)),
            median_iv_skew_10d=("iv_skew_10d", "median"),
            has_weekly_frac=("has_weekly", "mean"),
        )
        .reset_index()
    )
    agg["iv_regime_range"] = agg["p90_atm_iv"] - agg["p10_atm_iv"]

    realized = []
    for ticker, g in daily.sort_values("trade_date").groupby("ticker"):
        px = g["stk_px"].dropna().values
        if len(px) < 30:
            realized.append((ticker, np.nan))
            continue
        rets = np.diff(np.log(px))
        realized.append((ticker, float(np.std(rets) * np.sqrt(252))))
    rv = pd.DataFrame(realized, columns=["ticker", "realized_vol_annualized"])
    agg = agg.merge(rv, on="ticker", how="left")

    C.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    agg.to_parquet(C.PROFILE_PATH, engine="pyarrow", compression="snappy", index=False)
    log.info("Wrote profile: %s  |  %d tickers × %d features", C.PROFILE_PATH, len(agg), len(agg.columns) - 1)
    log.info("Sample (top 5 by median_total_oi):")
    log.info("\n%s", agg.nlargest(5, "median_total_oi").to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["daily", "profile", "all"])
    parser.add_argument("--year", type=int)
    parser.add_argument("--month", type=int)
    args = parser.parse_args()

    if args.stage in ("daily", "all"):
        if not args.year:
            parser.error("--year required for daily stage")
        stage_daily(args.year, args.month)
    if args.stage in ("profile", "all"):
        stage_profile()


if __name__ == "__main__":
    main()
