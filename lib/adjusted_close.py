"""Split-adjusted close prices from the ORATS by_ticker archive.

The ORATS `stkPx` field is **unadjusted for splits** — every split in the
archive (2013–2026) is an uncorrected discontinuity (e.g. NVDA 4:1 2021,
GOOGL/AMZN 20:1 2022, five SPDR sector ETFs 2:1 on 2025-12-05). That is fine
for per-cycle option backtests (each date is internally consistent) but
**corrupts any multi-month stock-series computation** that spans a split:
rolling betas, 200-DMA, relative strength.

This module produces a back-adjusted close so those consumers see a continuous
series. It does NOT mutate the option archive.

Source of truth: the feed-reconciled ledger at config/splits_ledger.csv (built by
scripts/maintenance/build_splits_ledger.py, which cross-checks the price-jump
heuristic below against the yfinance corporate-actions feed and price-confirms
every split). `load_adjusted_close` reads that ledger for any reconciled ticker;
the heuristic here is the FALLBACK for tickers not yet in the ledger.

The fallback detection is data-driven and conservative:
  - a single-day move > ~40% whose ratio snaps to a clean split factor
    (k:1 or 1:k for k in 2..30, plus 3:2 / 5:2), AND
  - smooth neighbor days (|return| < 15% on either side)
so a real crash (COVID, the 2025-04-09 tariff day) is never adjusted as a split.

A manual override file (data/profile/splits_manual.csv, columns
ticker,date,ratio) lets the user add any split the detector misses or remove a
false positive — manual entries win.

Usage:
    from lib.adjusted_close import load_adjusted_close, detect_splits
    s = load_adjusted_close("NVDA")     # continuous, split-adjusted close
"""
from __future__ import annotations

import csv
import functools
import json
from pathlib import Path

import pandas as pd

BY_TICKER = Path.home() / "MaxPain_Project/data/orats/by_ticker"
# Manual overrides: prefer the tracked config/ copy, fall back to legacy data/.
_MANUAL_CONFIG = Path.home() / "MaxPain_Project/config/splits_manual.csv"
_MANUAL_LEGACY = Path.home() / "MaxPain_Project/data/profile/splits_manual.csv"
MANUAL_OVERRIDE = _MANUAL_CONFIG if _MANUAL_CONFIG.exists() else _MANUAL_LEGACY

# Authoritative, feed-reconciled split ledger (built by
# scripts/maintenance/build_splits_ledger.py). When a ticker has been reconciled
# (listed in the manifest), this ledger is the SOLE source of truth — including
# the case of zero rows, which means "reconciled, no real split" and correctly
# SUPPRESSES detector artifacts (e.g. META's corrupt-data 14:1). Tickers absent
# from the manifest fall back to live price-discontinuity detection + manual.
LEDGER_PATH = Path.home() / "MaxPain_Project/config/splits_ledger.csv"
LEDGER_META = Path.home() / "MaxPain_Project/config/splits_ledger.meta.json"

_BIG_MOVE = 0.40        # min single-day move to consider a split
_FACTOR_TOL = 0.04      # ratio must be within 4% of a clean factor
_NEIGHBOR_MAX = 0.15    # neighbor-day moves must be calmer than this
# Integer ratios only (2:1 .. 100:1 and reverse). A real ≥50% single-day move
# with calm neighbors essentially never happens, so integer-split false
# positives are negligible. Range goes to 100 to catch large splits (CMG 50:1).
# Fractional splits (3:2 etc.) land near common ~33% earnings moves and would be
# false-positive-prone, so they are NOT auto-detected — add genuine ones (and
# any earnings-adjacent split the neighbor guard rejects, e.g. NFLX 7:1 2015) via
# data/profile/splits_manual.csv.
_CANDIDATES = list(range(2, 101))


def _snap_factor(ratio: float):
    """Return (clean_price_factor, label) if ratio matches a split, else None.

    clean_price_factor is the multiplier applied to PRE-split prices to make the
    series continuous (0.5 for a 2:1 forward split, 4.0 for a 1:4 reverse)."""
    fwd = 1.0 / ratio   # >1 for a forward split (price dropped)
    # Pick the NEAREST clean factor (within tolerance), not the first match —
    # an observed 19.6 (real-day return blurs the exact ratio) must snap to 20:1.
    fwd_k = min(_CANDIDATES, key=lambda k: abs(fwd - k))
    if abs(fwd - fwd_k) <= _FACTOR_TOL * fwd_k:
        return 1.0 / fwd_k, f"{fwd_k:g}:1"
    rev_k = min(_CANDIDATES, key=lambda k: abs(ratio - k))
    if abs(ratio - rev_k) <= _FACTOR_TOL * rev_k:
        return float(rev_k), f"1:{rev_k:g}"
    return None


def _load_raw(ticker: str, by_ticker_dir: Path = BY_TICKER) -> pd.Series:
    path = by_ticker_dir / f"{ticker}.parquet"
    df = pd.read_parquet(path, columns=["trade_date", "stkPx"])
    s = df.drop_duplicates("trade_date").set_index("trade_date")["stkPx"].sort_index()
    s.index = pd.to_datetime(s.index)
    return s.astype(float)


def detect_splits(s: pd.Series) -> list[dict]:
    """Detect splits in a close series. Returns [{date, ratio, factor, label}]."""
    out = []
    if len(s) < 4:
        return out
    vals = s.values
    idx = s.index
    for i in range(2, len(s) - 1):
        ratio = vals[i] / vals[i - 1]
        if 0.70 < ratio < 1.40:
            continue
        snap = _snap_factor(ratio)
        if snap is None:
            continue
        prev_mv = abs(vals[i - 1] / vals[i - 2] - 1)
        next_mv = abs(vals[i + 1] / vals[i] - 1)
        if prev_mv < _NEIGHBOR_MAX and next_mv < _NEIGHBOR_MAX:
            factor, label = snap
            out.append({"date": idx[i], "ratio": round(float(ratio), 4),
                        "factor": factor, "label": label})
    return out


def _manual_for(ticker: str) -> list[dict]:
    if not MANUAL_OVERRIDE.exists():
        return []
    rows = []
    with open(MANUAL_OVERRIDE) as f:
        for r in csv.DictReader(f):
            if r.get("ticker", "").strip().upper() == ticker.upper():
                ratio = float(r["ratio"])
                snap = _snap_factor(ratio)
                factor = snap[0] if snap else ratio
                label = snap[1] if snap else "manual"
                rows.append({"date": pd.to_datetime(r["date"]),
                             "ratio": ratio, "factor": factor, "label": label})
    return rows


def back_adjust(s: pd.Series, splits: list[dict]) -> pd.Series:
    """Multiply all prices strictly before each split date by its clean factor
    (cumulative), leaving the most-recent segment unchanged."""
    factor = pd.Series(1.0, index=s.index)
    for sp in splits:
        factor.loc[s.index < sp["date"]] *= sp["factor"]
    return s * factor


@functools.lru_cache(maxsize=1)
def _load_ledger() -> tuple[dict[str, list[dict]], frozenset[str]]:
    """Load the authoritative split ledger + the set of reconciled tickers.
    Cached for the process; the daily cron readers are short-lived so they always
    pick up the latest ledger."""
    reconciled: frozenset[str] = frozenset()
    if LEDGER_META.exists():
        try:
            reconciled = frozenset(json.loads(LEDGER_META.read_text())
                                   .get("reconciled_tickers", []))
        except Exception:
            reconciled = frozenset()
    by_ticker: dict[str, list[dict]] = {}
    if LEDGER_PATH.exists():
        with open(LEDGER_PATH) as f:
            for r in csv.DictReader(f):
                t = r["ticker"].strip().upper()
                by_ticker.setdefault(t, []).append({
                    "date": pd.Timestamp(r["date"]),
                    "factor": float(r["factor"]),
                    "label": r.get("label", ""),
                })
    return by_ticker, reconciled


def _splits_for(ticker: str, s: pd.Series) -> list[dict]:
    """Authoritative splits for a ticker: the feed-reconciled ledger when the
    ticker has been reconciled (zero rows ⇒ no split, artifacts suppressed),
    else live detection + manual overrides as a fallback."""
    ledger, reconciled = _load_ledger()
    if ticker.upper() in reconciled:
        return sorted(ledger.get(ticker.upper(), []), key=lambda x: x["date"])
    # Fallback: ticker not yet in the reconciled ledger (e.g. brand-new name
    # before the nightly refresh). Use the heuristic + manual overrides.
    splits = detect_splits(s)
    manual = _manual_for(ticker)
    by_date = {sp["date"].normalize(): sp for sp in splits}
    for m in manual:
        by_date[m["date"].normalize()] = m   # manual wins
    return sorted(by_date.values(), key=lambda x: x["date"])


def load_adjusted_close(ticker: str, by_ticker_dir: Path = BY_TICKER) -> pd.Series:
    """Continuous split-adjusted close for a ticker, driven by the authoritative
    feed-reconciled ledger (falling back to live detection for unreconciled
    tickers)."""
    s = _load_raw(ticker, by_ticker_dir)
    return back_adjust(s, _splits_for(ticker, s))


if __name__ == "__main__":
    # Quick self-check on known splitters.
    for t in ["NVDA", "GOOGL", "AMZN", "XLK", "SPY"]:
        raw = _load_raw(t)
        adj = load_adjusted_close(t)
        sp = detect_splits(raw)
        max_raw = raw.pct_change().abs().max()
        max_adj = adj.pct_change().abs().max()
        print(f"{t:6} splits={len(sp):2d}  max|ret| raw={max_raw:6.1%} -> adj={max_adj:6.1%}  "
              f"{[s['label']+'@'+str(s['date'].date()) for s in sp]}")
