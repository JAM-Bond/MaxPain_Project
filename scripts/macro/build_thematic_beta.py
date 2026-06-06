#!/usr/bin/env python3.11
"""
Thematic-beta overlay — the SECOND concentration axis (theme, not macro).

The macro fingerprint (lib/macro_profile) captures rate/risk/dollar/etc. exposure
but is BLIND to thematic clustering — two names can share a macro archetype yet be
opposite theme bets (e.g. AAPL and NVDA are both PC1- long-duration growth, but on
an "AI gets repriced" day AAPL is green while NVDA bleeds). This overlay measures
each name's exposure to two tradeable theme proxies, controlling for the market so
it isolates the THEME and not just market beta:

  b_soxx = coefficient on SOXX in  ret_i ~ a + SPY + SOXX   (AI/semiconductor)
  b_qqq  = coefficient on QQQ  in  ret_i ~ a + SPY + QQQ    (megacap / AI-software growth)

Writes data/macro/thematic_beta.parquet (ticker, as_of_date, b_soxx, soxx_tier,
b_qqq, qqq_tier, n_obs). Tiers are cross-sectional quartiles (HIGH ≥ p75 and > 0).

Usage: python3.11 scripts/macro/build_thematic_beta.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PRICES = ROOT / "data/macro/prices_daily_13y.parquet"
OUT = ROOT / "data/macro/thematic_beta.parquet"
WIN = 252           # trailing trading days (matches the macro fingerprint window)
PROXIES = {"b_soxx": "SOXX", "b_qqq": "QQQ"}


def _beta_controlling_market(y: pd.Series, spy: pd.Series, factor: pd.Series) -> float:
    """Coefficient on `factor` in y ~ const + spy + factor (market-orthogonalized)."""
    d = pd.concat([y, spy, factor], axis=1).dropna()
    d.columns = ["y", "spy", "f"]
    if len(d) < 60:
        return np.nan
    X = np.column_stack([np.ones(len(d)), d["spy"].values, d["f"].values])
    beta, *_ = np.linalg.lstsq(X, d["y"].values, rcond=None)
    return float(beta[2])


def _tier(b: float, p25: float, p50: float, p75: float) -> str:
    if b != b:
        return "NA"
    if b >= p75 and b > 0:
        return "HIGH"
    if b >= p50:
        return "MED"
    if b <= p25 and b < 0:
        return "NEG"
    return "LOW"


def main() -> int:
    p = pd.read_parquet(PRICES)
    w = p.pivot_table(index="date", columns="ticker", values="log_ret_1d").sort_index()
    recent = w.tail(WIN)
    as_of = recent.index.max().date()

    if "SPY" not in recent.columns:
        print("FATAL: SPY not in price spine — cannot market-control.")
        return 1
    spy = recent["SPY"]
    missing = [px for px in PROXIES.values() if px not in recent.columns]
    if missing:
        print(f"WARNING: proxy/proxies absent from price spine, skipped: {missing}")

    rows = []
    for t in w.columns:
        if t == "SPY":
            continue
        row = {"ticker": t, "as_of_date": as_of, "n_obs": int(recent[t].notna().sum())}
        for col, proxy in PROXIES.items():
            row[col] = (_beta_controlling_market(recent[t], spy, recent[proxy])
                        if proxy in recent.columns else np.nan)
        rows.append(row)
    df = pd.DataFrame(rows)

    # cross-sectional quartile tiers per factor
    for col in PROXIES:
        p25, p50, p75 = df[col].quantile([0.25, 0.50, 0.75])
        df[col.replace("b_", "") + "_tier"] = df[col].apply(lambda b: _tier(b, p25, p50, p75))

    df = df[["ticker", "as_of_date", "b_soxx", "soxx_tier", "b_qqq", "qqq_tier", "n_obs"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)

    print(f"Wrote {OUT.relative_to(ROOT)} — {len(df)} names, as_of {as_of}")
    print("\nSOXX-tier counts:", df["soxx_tier"].value_counts().to_dict())
    print("QQQ-tier  counts:", df["qqq_tier"].value_counts().to_dict())
    print("\nTop AI/semi exposure (SOXX-β, market-controlled):")
    print(df.nlargest(8, "b_soxx")[["ticker", "b_soxx", "soxx_tier", "b_qqq", "qqq_tier"]]
          .round(2).to_string(index=False))
    spot = df[df.ticker.isin(["AAPL", "NVDA", "AMD", "AMZN", "AVGO", "PLTR", "AMGN"])]
    print("\ntoday's actors:")
    print(spot[["ticker", "b_soxx", "soxx_tier", "b_qqq", "qqq_tier"]].round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
