#!/usr/bin/env python3.11
"""Daily refresh for the breadth ring (cron ~16:30 ET, before the 16:45 alert).

Two jobs, each soft-failing independently so a feed hiccup never blocks the other:
  1. Refresh S&P breadth → data/profile/breadth_live.parquet (% of S&P-500
     constituents above their own 50-DMA), appended idempotently per date. This
     is a dedicated LIVE file; the frozen research artifact breadth_spx500_v2 is
     left untouched. The breadth ring's top-warning leg reads this when fresh.
  2. Compute the RSP/SPY relative-strength ring and persist it to
     breadth_ring_daily, so the daily alert can render it without a network call.

Descriptive only — see lib/breadth_ring (not a gate, not a cascade vote).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.breadth_ring import (  # noqa: E402
    BREADTH_LIVE, compute_breadth_ring, persist, render_text,
)
from lib.db import DB_PATH  # noqa: E402

CONSTITUENTS = ROOT / "data/spx_constituents.json"


def refresh_breadth_live() -> str:
    """Append today's % S&P>50dma to breadth_live.parquet. Returns a status line."""
    try:
        import yfinance as yf
        syms = json.load(open(CONSTITUENTS))["symbols"]
        px = yf.download(syms, period="90d", auto_adjust=True, progress=False)["Close"]
        if px is None or px.empty:
            return "breadth refresh: FAILED (empty download)"
        ma50 = px.rolling(50).mean()
        asof = px.index[-1]
        valid = px.loc[asof].notna() & ma50.loc[asof].notna()
        above = (px.loc[asof] > ma50.loc[asof]) & valid
        n_valid = int(valid.sum())
        if n_valid < 100:
            return f"breadth refresh: FAILED (only {n_valid} valid tickers)"
        pct = round(100.0 * int(above.sum()) / n_valid, 2)
        new = pd.DataFrame([{"date": pd.Timestamp(asof.date()),
                             "pct_above_50dma": pct, "n_tickers": n_valid}])
        if BREADTH_LIVE.exists():
            old = pd.read_parquet(BREADTH_LIVE)
            old = old[old["date"] != new["date"].iloc[0]]  # idempotent on date
            out = pd.concat([old, new], ignore_index=True)
        else:
            out = new
        out = out.sort_values("date").reset_index(drop=True)
        BREADTH_LIVE.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(BREADTH_LIVE, index=False)
        return f"breadth refresh: OK {asof.date()} pct_above_50dma={pct}% (n={n_valid})"
    except Exception as e:  # noqa: BLE001
        return f"breadth refresh: FAILED ({e.__class__.__name__}: {e})"


def refresh_ring() -> str:
    """Compute + persist the RSP/SPY ring. Returns a status line."""
    import sqlite3
    ring = compute_breadth_ring()
    if ring.get("error"):
        return f"ring compute: FAILED ({ring['error']})"
    conn = sqlite3.connect(DB_PATH)
    try:
        persist(ring, conn)
    finally:
        conn.close()
    return "ring: " + " | ".join(render_text(ring))


def main() -> int:
    print(f"[refresh_breadth_ring] {date.today().isoformat()}")
    print("  " + refresh_breadth_live())
    # ring compute reads the freshly-written breadth_live for its top-warning leg
    for line in refresh_ring().split("\n"):
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
