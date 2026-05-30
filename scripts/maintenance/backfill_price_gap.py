#!/usr/bin/env python3.11
"""Backfill ORATS feed GAPS in a ticker's daily close series from yfinance.

Some names vanish from the ORATS feed for a stretch (e.g. Barrick/GOLD during its
2025 ticker change lost Jun–Nov 2025) yet the ticker is correct on both sides.
This fills the interior dates from yfinance so the read-side 200-DMA / RS / 52wk
are computed on a continuous series. It does NOT touch the option archive — it
writes a tracked, auditable backfill file (config/price_backfill.csv) that
lib.adjusted_close merges in for missing dates only (real ORATS data wins).

yfinance close is dividend/split-adjusted, so its absolute scale differs from
ORATS raw stkPx — and the offset DRIFTS across a multi-month gap as dividends are
paid. A single constant scale would leave a step at one boundary. So we
scale-match at BOTH ends and linearly interpolate the scale across the gap,
guaranteeing continuity at both boundaries.

Usage:
  python3.11 -m scripts.maintenance.backfill_price_gap GOLD
  python3.11 -m scripts.maintenance.backfill_price_gap GOLD --min-gap 9 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.adjusted_close import _load_raw, BACKFILL_PATH  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"


def find_gaps(s: pd.Series, min_gap_days: int):
    """Return [(left_date, right_date)] for each interior gap > min_gap_days."""
    diffs = s.index.to_series().diff().dt.days
    return [(s.index[i - 1], s.index[i]) for i in range(1, len(s))
            if diffs.iloc[i] > min_gap_days]


def yf_closes(ticker: str, start, end) -> pd.Series:
    import yfinance as yf
    h = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"),
                                  end=(end + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    s = h["Close"].copy()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s.astype(float)


def build_backfill(ticker: str, min_gap_days: int):
    """Return (rows, diagnostics) where rows = [(ticker,date,close,source,note)]."""
    orats = _load_raw(ticker).dropna().sort_index()
    gaps = find_gaps(orats, min_gap_days)
    rows, diag = [], []
    if not gaps:
        return rows, ["no gaps found"]
    yf = yf_closes(ticker, orats.index.min(), orats.index.max())
    for (L, R) in gaps:
        PL, PR = float(orats.loc[L]), float(orats.loc[R])
        yL = float(yf.reindex([L]).ffill().iloc[0]) if (yf.index <= L).any() else None
        yR = float(yf.reindex([R]).ffill().iloc[0]) if (yf.index <= R).any() else None
        interior = yf[(yf.index > L) & (yf.index < R)]
        if yL is None or yR is None or len(interior) == 0:
            diag.append(f"gap {L.date()}→{R.date()}: yfinance coverage insufficient, SKIPPED")
            continue
        rL, rR = PL / yL, PR / yR    # ORATS/yf scale at each boundary
        span = (R - L).days
        for t, yt in interior.items():
            w = (t - L).days / span
            scale = rL * (1 - w) + rR * w
            close = float(yt) * scale
            rows.append((ticker.upper(), t.strftime("%Y-%m-%d"), round(close, 4),
                         "yfinance", f"gap-bridge {L.date()}..{R.date()} scale={scale:.4f}"))
        diag.append(f"gap {L.date()}→{R.date()}: filled {len(interior)} days; "
                    f"boundary scale {rL:.3f}→{rR:.3f}; "
                    f"ORATS ${PL:.2f}/${PR:.2f}")
    return rows, diag


def merge_write(ticker: str, rows: list):
    """Rewrite config/price_backfill.csv: drop this ticker's old rows, add new."""
    existing = []
    if BACKFILL_PATH.exists():
        with open(BACKFILL_PATH) as f:
            existing = [r for r in csv.reader(f)]
    header = ["ticker", "date", "close", "source", "note"]
    body = [r for r in existing[1:] if r and r[0].upper() != ticker.upper()] if existing else []
    body.extend([list(map(str, r)) for r in rows])
    body.sort(key=lambda r: (r[0], r[1]))
    BACKFILL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BACKFILL_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker")
    ap.add_argument("--min-gap", type=int, default=9, help="min calendar-day gap to fill")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows, diag = build_backfill(args.ticker, args.min_gap)
    print(f"=== backfill {args.ticker.upper()} ===")
    for d in diag:
        print(f"  {d}")
    if not rows:
        print("  nothing to write.")
        return 0

    # Continuity check at the boundaries (post-merge series should have no step).
    from lib.adjusted_close import _load_raw as _lr
    orats = _lr(args.ticker).dropna().sort_index()
    bf = pd.Series({pd.Timestamp(r[1]): r[2] for r in rows}).sort_index()
    merged = orats.combine_first(bf).sort_index()
    import numpy as np
    lr = np.log(merged / merged.shift(1)).abs()
    bf_window = lr[(lr.index >= bf.index.min()) & (lr.index <= bf.index.max() + pd.Timedelta(days=5))]
    print(f"  max |1d log| across the bridged window: {float(bf_window.max()):.3f}  "
          f"(was ~{float(np.log(orats.loc[bf.index.max():].iloc[0]/orats.loc[:bf.index.min()].iloc[-1])):.3f} as a single jump)")

    if args.dry_run:
        print(f"  DRY RUN — would write {len(rows)} rows. First/last:")
        print(f"    {rows[0]}")
        print(f"    {rows[-1]}")
        return 0

    merge_write(args.ticker, rows)
    print(f"  Wrote {len(rows)} rows to {BACKFILL_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
