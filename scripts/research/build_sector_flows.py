#!/usr/bin/env python3.11
"""
Build the SSGA Select-Sector-SPDR daily flow store (and a monthly resample).

Fetches the free navhist XLSX for all 11 GICS sector SPDRs from sectorspdrs.com,
reconstructs net flow = Δ(shares outstanding) × NAV, and writes:
    data/flows/sector_flows_daily.parquet    (date, ticker, nav, shares_out, aum, flow)
    data/flows/sector_flows_monthly.parquet  (month-end resample; flow summed)

Idempotent — re-run to refresh (cron-able later). Source/depth/URL pattern:
reference_ssga_sector_flow_data memory.

Usage: python3.11 -m scripts.research.build_sector_flows
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from lib.ssga_flows import build_all, resample_flows, SECTOR_SPDRS  # noqa: E402

OUT_DAILY = ROOT / "data/flows/sector_flows_daily.parquet"
OUT_MONTHLY = ROOT / "data/flows/sector_flows_monthly.parquet"


def main() -> int:
    t0 = time.time()
    print(f"Fetching navhist for {len(SECTOR_SPDRS)} sector SPDRs...")
    daily, failed = build_all()
    if daily.empty:
        print(f"FATAL: no data fetched (failed: {failed})")
        return 1
    if failed:
        print(f"  WARNING: failed to fetch {failed}")

    OUT_DAILY.parent.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(OUT_DAILY, index=False)
    monthly = resample_flows(daily, "ME")
    monthly.to_parquet(OUT_MONTHLY, index=False)

    el = time.time() - t0
    print(f"\nWrote {OUT_DAILY.relative_to(ROOT)} "
          f"({len(daily):,} daily rows) + {OUT_MONTHLY.relative_to(ROOT)} "
          f"({len(monthly):,} monthly rows) in {el:.1f}s")

    # Per-ticker coverage
    print("\nPer-ticker coverage:")
    g = daily.groupby("ticker")["date"].agg(["min", "max", "count"])
    for t in SECTOR_SPDRS:
        if t in g.index:
            r = g.loc[t]
            print(f"  {t:5} {str(r['min'].date())} → {str(r['max'].date())}  "
                  f"{int(r['count']):,} days")

    # Latest-month net flow snapshot (the rotation read)
    last_m = monthly["date"].max()
    snap = (monthly[monthly["date"] == last_m]
            .assign(flow_mm=lambda d: d["flow"] / 1e6)
            .sort_values("flow_mm", ascending=False)[["ticker", "flow_mm", "aum"]])
    print(f"\nNet flow for {last_m.date()} ($MM, sorted — money rotating in → out):")
    for _, r in snap.iterrows():
        bar = "+" if r["flow_mm"] >= 0 else "-"
        print(f"  {r['ticker']:5} {r['flow_mm']:>+9.0f}  (AUM ${r['aum']/1e9:>5.1f}B)  {bar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
