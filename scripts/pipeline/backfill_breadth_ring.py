#!/usr/bin/env python3.11
"""Backfill breadth_ring_daily with the full reconstructable history (2013→present).

The ring is fully reconstructable from data we already have:
  • rs / broadening / run_days / spy_pct_200  ← SPY+RSP daily (yfinance, to 2013)
  • breadth leg (% S&P > 50dma)                ← breadth_spx500_v2.parquet (research file)

This gives a ~13-year historical record immediately rather than accruing one row/day.
Rows are stamped source='backfill_v1' and written with INSERT OR IGNORE, so the live
cron's rows (source='live', 503-name breadth feed) are never clobbered. The breadth
basis differs (research file = 448 names vs live = 503) — hence the explicit source stamp.

Idempotent. Run once; re-running only fills genuinely missing dates.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.breadth_ring import RATIO_MA, BREADTH_EXTENDED, _classify  # noqa: E402
from lib.db import DB_PATH  # noqa: E402

BREADTH_RESEARCH = ROOT / "data/profile/breadth_spx500_v2.parquet"


def _closes(t: str) -> pd.Series:
    import yfinance as yf
    df = yf.download(t, start="2011-06-01", end=None, auto_adjust=True, progress=False)
    return pd.Series(np.asarray(df["Close"]).ravel(), index=pd.to_datetime(df.index), name=t)


def build_history() -> pd.DataFrame:
    spy, rsp = _closes("SPY"), _closes("RSP")
    d = pd.DataFrame({"SPY": spy, "RSP": rsp}).dropna()
    d["ratio"] = d["RSP"] / d["SPY"]
    d["ratio_ma"] = d["ratio"].rolling(RATIO_MA).mean()
    d["spy200"] = d["SPY"].rolling(200).mean()
    d = d.dropna(subset=["ratio_ma", "spy200"]).copy()
    d["rs"] = d["ratio"] / d["ratio_ma"] - 1.0
    d["broadening"] = d["rs"] > 0.0
    d["spy_pct_200"] = d["SPY"] / d["spy200"] - 1.0

    # run-length of the current broadening/narrowing state (vectorized)
    grp = (d["broadening"] != d["broadening"].shift()).cumsum()
    d["run_days"] = d.groupby(grp).cumcount() + 1

    # breadth leg from the research file (join by date; NaN where unavailable)
    br = pd.read_parquet(BREADTH_RESEARCH)[["date", "pct_above_50dma"]]
    br["date"] = pd.to_datetime(br["date"])
    d = d.join(br.set_index("date")["pct_above_50dma"].rename("breadth"))

    d["breadth_extended"] = d["breadth"] >= BREADTH_EXTENDED
    d["top_warning"] = (~d["broadening"]) & d["breadth_extended"].fillna(False)
    return d


def main() -> int:
    d = build_history()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS breadth_ring_daily (
               asof TEXT PRIMARY KEY, status TEXT, rs REAL, broadening INTEGER,
               run_days INTEGER, spy_pct_200 REAL, breadth REAL,
               breadth_extended INTEGER, top_warning INTEGER, source TEXT)"""
    )
    try:
        conn.execute("ALTER TABLE breadth_ring_daily ADD COLUMN source TEXT")
    except Exception:
        pass

    written = 0
    for ts, r in d.iterrows():
        broadening = bool(r["broadening"])
        top = bool(r["top_warning"])
        status, _, _ = _classify(broadening, top)
        breadth = None if pd.isna(r["breadth"]) else float(r["breadth"])
        cur = conn.execute(
            """INSERT OR IGNORE INTO breadth_ring_daily
               (asof, status, rs, broadening, run_days, spy_pct_200, breadth,
                breadth_extended, top_warning, source)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (ts.date().isoformat(), status, float(r["rs"]), int(broadening),
             int(r["run_days"]), float(r["spy_pct_200"]), breadth,
             int(bool(r["breadth_extended"]) if pd.notna(r["breadth"]) else 0),
             int(top), "backfill_v1"),
        )
        written += cur.rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM breadth_ring_daily").fetchone()[0]
    span = conn.execute("SELECT MIN(asof), MAX(asof) FROM breadth_ring_daily").fetchone()
    by_src = conn.execute("SELECT source, COUNT(*) FROM breadth_ring_daily GROUP BY source").fetchall()
    conn.close()
    print(f"backfill: inserted {written} new rows; table now {total} rows, span {span[0]}→{span[1]}")
    print(f"  by source: {dict(by_src)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
