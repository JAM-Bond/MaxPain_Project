"""Cache yfinance earnings event dates for the earnings-strategies cohort tickers.

Output: data/profile/earnings_events.parquet
        cols = [ticker, earnings_date]

The bias scan aggregates per-ticker statistics; this caches the actual events
so the structure backtest can iterate them without refetching yfinance.
"""
from pathlib import Path
import logging
import time

import pandas as pd
import yfinance as yf

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
OUT = ROOT / "data/profile/earnings_events.parquet"

# Union of T1 (bias-up), T2 (bias-down), T4 (high-vol bias-ambiguous)
COHORT = sorted(set([
    # T1 bias-up (>=60% positive, N>=20)
    "SCCO", "CNQ", "KO", "NUE", "KGC", "GOOGL", "NRG", "RRC", "META", "WFC",
    "CX", "ITUB",
    # T2 bias-down (<=40% positive, N>=20)
    "INTC", "JBLU", "NEM", "GLNG", "FCX", "VST", "CAR",
    # T4 high-vol bias-ambiguous
    "RIG", "ENPH", "PLTR", "SNAP", "TME", "TEVA", "CFLT",
]))


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("earnings_events")


def fetch(ticker: str) -> list[pd.Timestamp]:
    try:
        ed = yf.Ticker(ticker).earnings_dates
    except Exception as e:
        log.warning("%s fetch err: %s", ticker, e)
        return []
    if ed is None or ed.empty:
        return []
    past = ed[ed["Reported EPS"].notna()]
    return [pd.Timestamp(d).tz_localize(None).normalize() for d in past.index]


def main() -> None:
    rows = []
    for i, tkr in enumerate(COHORT, 1):
        chain = BY_TICKER / f"{tkr}.parquet"
        if not chain.exists():
            log.warning("[%d/%d] %s: no ORATS chain, skip", i, len(COHORT), tkr)
            continue
        events = fetch(tkr)
        if not events:
            log.info("[%d/%d] %s: no events", i, len(COHORT), tkr)
            continue
        log.info("[%d/%d] %s: %d events", i, len(COHORT), tkr, len(events))
        for d in events:
            rows.append({"ticker": tkr, "earnings_date": d})
        time.sleep(0.1)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(["ticker", "earnings_date"]).sort_values(["ticker", "earnings_date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    log.info("Wrote %d events for %d tickers to %s",
             len(df), df["ticker"].nunique(), OUT)
    print(df.groupby("ticker").size().to_string())


if __name__ == "__main__":
    main()
