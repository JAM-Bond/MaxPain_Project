#!/usr/bin/env python3.11
"""
FRED full-history backfill — Phase 1 of macro-sensitivity profile.

Fetches the full available history of 27 FRED series (2013-01-01 onward by
default) and writes a long-format parquet at data/macro/fred_daily_13y.parquet.

Distinct from Agent_Project/FRED/scraper.py: that scraper writes the LATEST
value per series into ChromaDB (one row per day-of-cron-run). This script
hits FRED's observations endpoint with no `limit` and pulls the full series.

Output schema (long):
    date        date          observation date
    series_id   str           FRED series ID (e.g. 'DGS10')
    series_name str           human label
    value       float64       observation value (NaN where FRED returned '.')
    frequency   str           'd'/'w'/'m'/'q'

Usage:
    python3.11 build_fred_daily.py                  # full backfill 2013-01-01 →
    python3.11 build_fred_daily.py --start 2010-01-01
    python3.11 build_fred_daily.py --series DGS10,VIXCLS  # subset (debug)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path.home() / "MaxPain_Project"
OUT_PATH = ROOT / "data/macro/fred_daily_13y.parquet"
API_KEY_FILE = Path.home() / "Agent_Project/config/api_keys.env"
BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# 20 existing (mirrors Agent_Project/FRED/scraper.py) + 7 new for macro-sensitivity
SERIES = {
    # Rates — daily
    "DFF":          ("Fed Funds Rate",                 "d"),
    "DTB4WK":       ("4-Week T-Bill",                  "d"),
    "DTB3":         ("3-Month T-Bill",                 "d"),
    "DTB6":         ("6-Month T-Bill",                 "d"),
    "DTB1YR":       ("1-Year T-Bill",                  "d"),
    "DGS2":         ("2-Year Treasury",                "d"),
    "DGS5":         ("5-Year Treasury",                "d"),
    "DGS10":        ("10-Year Treasury",               "d"),
    "DGS30":        ("30-Year Treasury",               "d"),
    "T10Y2Y":       ("10Y minus 2Y spread",            "d"),
    "T10YIE":       ("10Y breakeven inflation",        "d"),
    # Inflation — monthly
    "CPIAUCSL":     ("CPI All Items",                  "m"),
    "CPILFESL":     ("Core CPI",                       "m"),
    "PCEPILFE":     ("Core PCE",                       "m"),
    "CPIUFDSL":     ("Food Price Index",               "m"),
    "CPIENGSL":     ("Energy Price Index",             "m"),
    # Commodities + dollar — daily
    "DCOILWTICO":   ("Crude Oil (WTI)",                "d"),
    "GASREGW":      ("Gasoline Price",                 "w"),
    "DTWEXBGS":     ("Trade-Wtd Dollar (Broad)",       "d"),
    # Vol + credit — daily
    "VIXCLS":       ("VIX Close",                      "d"),
    "BAMLC0A0CM":   ("IG Corporate OAS",               "d"),    # 3y only (ICE BofA license)
    "BAMLH0A0HYM2": ("HY Corporate OAS",               "d"),    # 3y only (ICE BofA license)
    "DAAA":         ("Moody's Aaa Corporate Yield",    "d"),    # 13y proxy for IG
    "DBAA":         ("Moody's Baa Corporate Yield",    "d"),    # 13y proxy for IG/HY boundary
    "NFCI":         ("Chicago Fed NFCI",               "w"),
    # Activity — monthly + quarterly
    "UNRATE":       ("Unemployment Rate",              "m"),
    "PAYEMS":       ("Nonfarm Payrolls",               "m"),
    "GDP":          ("GDP",                            "q"),
    "MORTGAGE30US": ("30-Year Mortgage Rate",          "w"),
}


def load_api_key() -> str:
    with open(API_KEY_FILE) as f:
        for line in f:
            if line.startswith("FRED_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError(f"FRED_API_KEY not found in {API_KEY_FILE}")


# FRED's gateway throws transient 504s / read-timeouts under load (see the
# 2026-05-28 outage that cascaded into a build_betas_rolling crash). Retry
# those with backoff; do NOT retry 4xx (bad series id won't fix itself).
FETCH_TIMEOUT = 60          # was 30 — outages manifested as read-timeouts
MAX_RETRIES = 4             # ~ 1 + 2 + 4 + 8 = 15s of backoff worst case
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def fetch_series(api_key: str, series_id: str, start: str) -> pd.DataFrame:
    params = {
        "series_id":              series_id,
        "api_key":                api_key,
        "file_type":              "json",
        "observation_start":      start,
        "sort_order":             "asc",
    }
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(BASE_URL, params=params, timeout=FETCH_TIMEOUT)
            if r.status_code in RETRYABLE_STATUS:
                r.raise_for_status()  # -> HTTPError, handled below
            r.raise_for_status()      # non-retryable 4xx propagates immediately
            obs = r.json().get("observations", [])
            if not obs:
                return pd.DataFrame(columns=["date", "value"])
            df = pd.DataFrame(obs)[["date", "value"]]
            df["date"] = pd.to_datetime(df["date"])
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            return df
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in RETRYABLE_STATUS:
                raise  # 4xx — don't retry
            last_exc = e
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
        if attempt < MAX_RETRIES - 1:
            time.sleep(2 ** attempt)  # 1, 2, 4s backoff
    raise last_exc  # exhausted retries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013-01-01")
    ap.add_argument("--series", default=None, help="comma-separated subset")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    api_key = load_api_key()

    if args.series:
        wanted = {s.strip() for s in args.series.split(",")}
        series = {k: v for k, v in SERIES.items() if k in wanted}
        unknown = wanted - set(SERIES)
        if unknown:
            print(f"WARN: unknown series {unknown}")
    else:
        series = SERIES

    print(f"Fetching {len(series)} series from {args.start}...")
    rows = []
    n_failed = 0
    for i, (sid, (name, freq)) in enumerate(series.items(), 1):
        try:
            df = fetch_series(api_key, sid, args.start)
            df["series_id"] = sid
            df["series_name"] = name
            df["frequency"] = freq
            rows.append(df)
            n_valid = df["value"].notna().sum()
            print(f"  [{i:2d}/{len(series)}] {sid:14s} {name:32s} n={len(df):6d}  valid={n_valid:6d}  "
                  f"{df['date'].min().date() if len(df) else '—'} → {df['date'].max().date() if len(df) else '—'}")
        except Exception as e:
            n_failed += 1
            print(f"  [{i:2d}/{len(series)}] {sid:14s} FAILED: {e}")
        time.sleep(0.05)  # polite spacing; FRED tolerates 120/min

    # Write-guard: a partial fetch (e.g. a FRED outage) must NOT overwrite the
    # last-good parquet with a near-empty one — that silently clobbers history
    # and crashes downstream beta builders. Abort loud, leave the good file.
    MIN_SUCCESS_RATE = 0.90
    n_ok = len(series) - n_failed
    if not rows:
        print("No data fetched — aborting (existing parquet left intact).")
        sys.exit(1)
    if n_ok / len(series) < MIN_SUCCESS_RATE:
        print(f"\nABORT: only {n_ok}/{len(series)} series fetched "
              f"({n_ok / len(series):.0%} < {MIN_SUCCESS_RATE:.0%} floor) — "
              f"likely a transient FRED outage. Existing {Path(args.out).name} "
              f"left intact; re-run when FRED recovers.")
        sys.exit(1)

    out = pd.concat(rows, ignore_index=True)
    out = out[["date", "series_id", "series_name", "value", "frequency"]]
    out = out.sort_values(["series_id", "date"]).reset_index(drop=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False, compression="snappy")

    print(f"\nWrote {len(out):,} rows × {len(out.columns)} cols → {out_path}")
    print(f"Date range: {out['date'].min().date()} → {out['date'].max().date()}")
    print(f"Series: {out['series_id'].nunique()}")


if __name__ == "__main__":
    main()
