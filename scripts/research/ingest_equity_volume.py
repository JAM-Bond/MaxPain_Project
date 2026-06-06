"""Ingest daily EQUITY (share) volume for the IF universe — Study B equity arm.

Per LONGDATED_IF_VOLUME_SIGNAL_PREREG.md. Burry's literal volume tell is about
SHARE volume ("volume has remained low"), which ORATS does not carry (it has
option volume only). Pull daily Volume via yfinance for the IF universe and
cache to parquet; the equity-arm signal study reads this cache.

Note: SPX is an index (no share volume) → skipped in the equity arm. ETFs
(QQQ/GLD/EFA/XLK) do trade share volume → kept. yfinance Volume is split-
adjusted across the series (consistent for a 20d/60d ratio except at split
boundaries — immaterial for this screen).
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
OUT = ROOT / "data" / "profile" / "equity_volume.parquet"

UNIVERSE = sorted(set([
    "SPY", "QQQ", "GLD", "EFA", "WMT", "NEM", "XOM", "PG", "WFC", "GE",
    "INTC", "BABA", "TSLA", "AMD", "NVDA", "CAR", "AMZN", "GOOGL", "SCCO",
    "GOLD", "CLF", "ISRG", "XLK", "PEP", "STX", "LRCX", "MCD", "JNJ", "PDD",
    "AG", "DELL", "AFRM", "PLTR", "AVGO",
]))  # SPX dropped (index, no share volume)

START = "2012-01-01"


def run():
    import yfinance as yf
    print(f"  ingesting daily volume for {len(UNIVERSE)} names from {START}...", flush=True)
    raw = yf.download(UNIVERSE, start=START, auto_adjust=False,
                      group_by="ticker", progress=False, threads=True)
    rows = []
    got = 0
    for sym in UNIVERSE:
        try:
            sub = raw[sym][["Close", "Volume"]].dropna()
        except (KeyError, Exception):
            print(f"    {sym}: no data", flush=True)
            continue
        if sub.empty:
            print(f"    {sym}: empty", flush=True)
            continue
        sub = sub.reset_index().rename(columns={"Date": "date", "Close": "close",
                                                "Volume": "eq_volume"})
        sub["ticker"] = sym
        rows.append(sub[["ticker", "date", "close", "eq_volume"]])
        got += 1
    out = pd.concat(rows, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.tz_localize(None)
    out.to_parquet(OUT, index=False)
    print(f"\n  {got}/{len(UNIVERSE)} names; {len(out):,} rows "
          f"({out['date'].min().date()}..{out['date'].max().date()}) -> {OUT}", flush=True)


if __name__ == "__main__":
    run()
