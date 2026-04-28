"""Run inverted_fly wide-wings backtest on new tickers (universe expansion).

Canonical specs per TRADING_PLAN.rtf v1.3 inverted_fly section:
  - dte_45 entry
  - 10% wings (validated sweet spot)
  - slip=0.25 (realistic-retail friction)
  - also run 15% wings for tail characterization

Target tickers extracted by extract_new_tickers.py:
  Mag 7 gaps: AMZN, GOOGL, NVDA
  Gold miners: GOLD, AU, KGC, PAAS
  Oil services: SLB, BKR
  Copper: SCCO, TECK
  Steel: NUE, STLD, CLF
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
sys.path.insert(0, str(ROOT / "scripts/backtest"))

import config as C
import run as R
import structures as S


NEW_TICKERS = [
    "AMZN", "GOOGL", "NVDA",
    "GOLD", "AU", "KGC", "PAAS",
    "SLB", "BKR",
    "SCCO", "TECK",
    "NUE", "STLD", "CLF",
]
WING_PCTS = [0.10, 0.15]
OUT_PATH = ROOT / "data/backtest/results_if_new_tickers_slip025.parquet"


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("if_new")


def run_one_wing(tickers: list[str], wing_pct: float) -> pd.DataFrame:
    C.activate_slip(0.25)
    C.BFLY_WING_PCT_SPOT = wing_pct
    # Restrict to inverted_fly
    original = dict(S.STRUCTURES)
    S.STRUCTURES.clear()
    S.STRUCTURES["inverted_fly"] = original["inverted_fly"]
    try:
        rows = []
        for i, t in enumerate(tickers, 1):
            by_tkr = ROOT / f"data/orats/by_ticker/{t}.parquet"
            if not by_tkr.exists():
                log.warning("  wing=%.2f [%d/%d] %s: MISSING by_ticker file, skipping",
                            wing_pct, i, len(tickers), t)
                continue
            df = R.simulate_ticker(t)
            if not df.empty:
                df["wing_pct"] = wing_pct
                rows.append(df)
                log.info("  wing=%.2f [%d/%d] %s: %d rows",
                         wing_pct, i, len(tickers), t, len(df))
            else:
                log.info("  wing=%.2f [%d/%d] %s: no rows",
                         wing_pct, i, len(tickers), t)
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    finally:
        S.STRUCTURES.clear()
        S.STRUCTURES.update(original)


def main() -> None:
    log.info("Sweep: %d new tickers x %d wing widths", len(NEW_TICKERS), len(WING_PCTS))
    pieces = []
    for wp in WING_PCTS:
        log.info("--- Wing %.2f%% of spot ---", wp * 100)
        df = run_one_wing(NEW_TICKERS, wp)
        if not df.empty:
            pieces.append(df)
    if not pieces:
        log.warning("No results")
        return
    out = pd.concat(pieces, ignore_index=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT_PATH, engine="pyarrow", compression="snappy", index=False)
    log.info("Wrote %d rows to %s", len(out), OUT_PATH)


if __name__ == "__main__":
    main()
