#!/usr/bin/env python3.11
"""Migration 011 — create `ticker_stop_profile` + load the existing scan.

Promotes the per-ticker breach-recovery / stop profile from a local research
parquet to an operational DB table, so the daily alert and the promotion path are
a simple lookup (project_per_ticker_stop_findings). Creates the table and loads
data/profile/per_ticker_stop_profile.parquet (today's full-cohort scan) if present.

Idempotent: INSERT OR REPLACE keyed on (ticker, structure). Safe to re-run.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402
from lib.ticker_stop_profile import ensure_table, upsert_profiles, PROFILE_PARQUET, TABLE  # noqa: E402


def main(apply: bool) -> None:
    import pandas as pd
    c = sqlite3.connect(DB_PATH)
    ensure_table(c)
    exists = PROFILE_PARQUET.exists()
    n_parquet = len(pd.read_parquet(PROFILE_PARQUET)) if exists else 0
    have = c.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    print(f"table {TABLE}: {have} rows | profile parquet: {'present' if exists else 'MISSING'} ({n_parquet} rows)")
    if not apply:
        print("(dry-run — pass --apply to create+load)")
        c.close()
        return
    if exists:
        n = upsert_profiles(c, pd.read_parquet(PROFILE_PARQUET))
        print(f"APPLIED: loaded {n} profiles into {TABLE}")
    else:
        print("APPLIED: table created (no parquet to load — run per_ticker_stop_study.py)")
    total = c.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    by = c.execute(f"SELECT structure, classification, COUNT(*) FROM {TABLE} GROUP BY structure, classification").fetchall()
    print(f"  total rows: {total} | breakdown: {by}")
    c.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
