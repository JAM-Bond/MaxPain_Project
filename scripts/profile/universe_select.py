#!/usr/bin/env python3.11
"""Build the 150-symbol universe from clustered profile + yfinance reference enrichment.

Two stages:
1. enrich  — take top N per cluster, query yfinance for sector/market_cap/quoteType, cache to parquet
2. select  — apply allocation + sector/cap balance constraints to produce final 150

Usage:
    python3.11 universe_select.py enrich
    python3.11 universe_select.py select
    python3.11 universe_select.py all
"""
import argparse
import logging
import sys
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
import config as C


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("universe")


LIQUIDITY_FLOOR = dict(min_oi=10_000, min_vol=500, min_weekly=0.25)
EXTENDED_POOL = {2: 120, 1: 100, 0: 50, 4: 40}
FINAL_ALLOCATION = {2: 60, 1: 50, 0: 25, 4: 15}
CLUSTERS_PATH = C.PROFILE_ROOT / "clusters_k8.parquet"
REFERENCE_PATH = C.PROFILE_ROOT / "reference_data.parquet"
UNIVERSE_PATH = C.PROFILE_ROOT / "universe_v1.parquet"


def build_pool() -> pd.DataFrame:
    c = pd.read_parquet(CLUSTERS_PATH)
    floor = c[(c["median_total_oi"] >= LIQUIDITY_FLOOR["min_oi"])
           & (c["median_total_volume"] >= LIQUIDITY_FLOOR["min_vol"])
           & (c["has_weekly_frac"] >= LIQUIDITY_FLOOR["min_weekly"])]
    parts = []
    for cid, n in EXTENDED_POOL.items():
        parts.append(floor[floor["cluster"] == cid].nlargest(n, "median_total_oi"))
    pool = pd.concat(parts, ignore_index=True)
    log.info("Extended pool: %d tickers (target final: %d)", len(pool), sum(FINAL_ALLOCATION.values()))
    return pool


def load_reference_cache() -> pd.DataFrame:
    if REFERENCE_PATH.exists():
        return pd.read_parquet(REFERENCE_PATH)
    return pd.DataFrame(columns=["ticker", "sector", "industry", "market_cap", "quote_type", "long_name"])


def save_reference_cache(df: pd.DataFrame) -> None:
    REFERENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(REFERENCE_PATH, engine="pyarrow", compression="snappy", index=False)


def fetch_one(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info
        return {
            "ticker": ticker,
            "sector": info.get("sector") or info.get("category"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "quote_type": info.get("quoteType"),
            "long_name": info.get("longName") or info.get("shortName"),
        }
    except Exception as e:
        return {"ticker": ticker, "sector": None, "industry": None,
                "market_cap": None, "quote_type": None, "long_name": None,
                "error": str(e)[:100]}


def stage_enrich(force: bool = False) -> pd.DataFrame:
    pool = build_pool()
    cache = load_reference_cache()
    known = set(cache["ticker"].tolist()) if not force else set()
    todo = [t for t in pool["ticker"] if t not in known]
    log.info("Reference cache: %d existing, %d to fetch", len(known), len(todo))

    results = []
    for i, ticker in enumerate(todo, 1):
        rec = fetch_one(ticker)
        results.append(rec)
        if i % 20 == 0 or i == len(todo):
            log.info("  [%d/%d] %s  sector=%s cap=%s",
                     i, len(todo), ticker, rec.get("sector"), rec.get("market_cap"))
            tmp = pd.concat([cache, pd.DataFrame(results)], ignore_index=True)
            save_reference_cache(tmp)
        time.sleep(0.05)

    cache = pd.concat([cache, pd.DataFrame(results)], ignore_index=True).drop_duplicates("ticker", keep="last")
    save_reference_cache(cache)
    log.info("Reference cache now contains %d tickers at %s", len(cache), REFERENCE_PATH)
    return cache


def cap_tier(cap) -> str:
    if pd.isna(cap) or cap is None or cap == 0:
        return "unknown"
    if cap >= 200e9:
        return "mega"
    if cap >= 10e9:
        return "large"
    if cap >= 2e9:
        return "mid"
    return "small"


def stage_select() -> pd.DataFrame:
    pool = build_pool()
    cache = load_reference_cache()
    if cache.empty:
        raise SystemExit("Reference cache empty — run `enrich` first")

    pool = pool.merge(cache, on="ticker", how="left")
    pool["cap_tier"] = pool["market_cap"].apply(cap_tier)
    is_etf = pool["quote_type"].fillna("").str.upper().isin({"ETF", "ETN", "FUND", "MUTUALFUND"})
    pool.loc[is_etf, "cap_tier"] = "etf"
    pool["sector"] = pool["sector"].fillna("Unknown")

    log.info("\nSector distribution in extended pool:")
    log.info("\n%s", pool["sector"].value_counts().to_string())
    log.info("\nCap tier distribution in extended pool:")
    log.info("\n%s", pool["cap_tier"].value_counts().to_string())

    # Greedy round-robin selection: within each cluster, iterate through sectors
    # round-robin picking highest-OI remaining name per (cluster, sector) until target hit.
    selections = []
    for cid, target in FINAL_ALLOCATION.items():
        sub = pool[pool["cluster"] == cid].copy().sort_values("median_total_oi", ascending=False)
        sector_counts = {}
        picked = []
        # Max 3 per (sector, cluster) to force diversity first pass
        for _, row in sub.iterrows():
            if len(picked) >= target:
                break
            s = row["sector"]
            if sector_counts.get(s, 0) >= 3 and len([p for p in picked]) < target:
                continue
            picked.append(row)
            sector_counts[s] = sector_counts.get(s, 0) + 1
        # Fill remaining slots if we didn't hit target (sector cap held us back)
        if len(picked) < target:
            picked_tickers = {r["ticker"] for r in picked}
            leftover = sub[~sub["ticker"].isin(picked_tickers)].head(target - len(picked))
            picked.extend([row for _, row in leftover.iterrows()])
        df_c = pd.DataFrame(picked).head(target)
        selections.append(df_c)
        log.info("Cluster %d: selected %d  |  sectors: %s",
                 cid, len(df_c),
                 df_c["sector"].value_counts().to_dict())

    universe = pd.concat(selections, ignore_index=True)
    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    universe.to_parquet(UNIVERSE_PATH, engine="pyarrow", compression="snappy", index=False)
    log.info("\nWrote universe: %s  |  %d tickers", UNIVERSE_PATH, len(universe))
    log.info("\nFinal sector distribution:")
    log.info("\n%s", universe["sector"].value_counts().to_string())
    log.info("\nFinal cap-tier distribution:")
    log.info("\n%s", universe["cap_tier"].value_counts().to_string())
    log.info("\nFinal cluster distribution:")
    log.info("\n%s", universe["cluster"].value_counts().to_string())
    return universe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["enrich", "select", "all"])
    parser.add_argument("--force", action="store_true", help="Re-fetch even cached tickers")
    args = parser.parse_args()

    if args.stage in ("enrich", "all"):
        stage_enrich(force=args.force)
    if args.stage in ("select", "all"):
        stage_select()


if __name__ == "__main__":
    main()
