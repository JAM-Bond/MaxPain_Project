#!/usr/bin/env python3.11
"""Inverted_fly wide-wings sweep across the full 150-symbol universe.

The v1/v2 runs use 0.25% of spot as the BFLY wing, which `project_scorecard_and_tail_findings.md`
flagged as too narrow to actually test the long-vol thesis. This sweep runs the inverted_fly
structure at 3%, 5%, 10%, and 15% of spot, preserving per-cycle rows (entry_date, pnl, etc.)
so a regime-window cut can be applied afterward.

Friction: slip=0.25 (the canonical realistic-friction setting — see project_backtest_slippage_sensitivity.md).

Output: data/backtest/results_wide_wings_universe_slip025.parquet

Usage:
    python3.11 wide_wings_sweep.py                      # full universe, 4 wing widths
    python3.11 wide_wings_sweep.py --ticker SPY         # single-ticker smoke test
    python3.11 wide_wings_sweep.py --wings 0.15         # one wing width
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

import config as C
import run as R
import structures as S


WING_PCTS = [0.03, 0.05, 0.10, 0.15]
OUT_PATH = C.BACKTEST_ROOT / "results_wide_wings_universe_slip025.parquet"


logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("wide_wings")


def run_one_wing(tickers: list[str], wing_pct: float) -> pd.DataFrame:
    C.activate_slip(0.25)
    C.BFLY_WING_PCT_SPOT = wing_pct
    # Restrict STRUCTURES dict to inverted_fly only for speed.
    original = dict(S.STRUCTURES)
    S.STRUCTURES.clear()
    S.STRUCTURES["inverted_fly"] = original["inverted_fly"]
    try:
        all_rows: list[pd.DataFrame] = []
        for i, t in enumerate(tickers, 1):
            df = R.simulate_ticker(t)
            if not df.empty:
                df["wing_pct"] = wing_pct
                all_rows.append(df)
                log.info("  wing=%.2f [%d/%d] %s: %d rows", wing_pct, i, len(tickers), t, len(df))
            else:
                log.info("  wing=%.2f [%d/%d] %s: no rows", wing_pct, i, len(tickers), t)
        if not all_rows:
            return pd.DataFrame()
        return pd.concat(all_rows, ignore_index=True)
    finally:
        S.STRUCTURES.clear()
        S.STRUCTURES.update(original)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, help="Single-ticker smoke test")
    parser.add_argument("--wings", type=str, default=None,
                        help=f"Comma-separated wing pcts, default {WING_PCTS}")
    parser.add_argument("--out", type=str, default=str(OUT_PATH))
    args = parser.parse_args()

    wings = [float(w) for w in args.wings.split(",")] if args.wings else WING_PCTS

    if args.ticker:
        tickers = [args.ticker]
    else:
        universe = pd.read_parquet(C.UNIVERSE_PATH)
        tickers = universe["ticker"].tolist()
    log.info("Sweep: %d tickers × %d wing widths = %d cells",
             len(tickers), len(wings), len(tickers) * len(wings))

    pieces: list[pd.DataFrame] = []
    for wp in wings:
        log.info("─── Wing %.2f%% of spot ───", wp * 100)
        df = run_one_wing(tickers, wp)
        if not df.empty:
            pieces.append(df)

    if not pieces:
        log.warning("No results")
        return
    out = pd.concat(pieces, ignore_index=True)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)
    log.info("Wrote %d rows × %d cols to %s", len(out), len(out.columns), out_path)


if __name__ == "__main__":
    main()
