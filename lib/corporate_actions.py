"""Corporate-actions (stock-split) feed via yfinance.

An *independent* source of split ex-dates and ratios, used to confirm and augment
the price-discontinuity heuristic in `lib.adjusted_close`. yfinance is rejected
for option *chains* (bid/ask quality), but its split history is a clean
corporate-action record — official ex-dates + exact ratios — which is precisely
what the heuristic cannot give us (it infers from price jumps and is blind to
fractional / earnings-adjacent splits).

Normalized output per split (matches the `lib.adjusted_close` convention):
    {
      "date":   pd.Timestamp,     # split ex-date
      "ratio":  float,            # yfinance convention: new/old (7:1 fwd -> 7.0, 1:10 rev -> 0.1)
      "factor": float,            # multiplier on PRE-split prices = 1/ratio
      "label":  str,              # "7:1" / "1:10"
      "source": "yfinance",
    }

Disk-cached per ticker (JSON) so bulk reconciliation and the daily refresh don't
re-hit the network; a cache entry is refreshed once it is older than
``CACHE_TTL_DAYS``. Network/parse failures return None (callers fail open and keep
the heuristic) — never raise into a pricing path.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

CACHE_DIR = Path.home() / "MaxPain_Project/data/profile/corp_actions_cache"
CACHE_TTL_DAYS = 7
SAMPLE_START = "2013-01-01"   # the ORATS archive begins 2013


# yfinance `.splits` mixes true share splits with small STOCK DIVIDENDS (e.g.
# SCCO ~1.01) and SPINOFFS (e.g. GE 1.281 = GE HealthCare). Only clean share-split
# ratios are corporate-action *splits* for our purpose; the rest must never enter
# the adjustment ledger. We accept a ratio only if it snaps tightly to a known
# split ratio AND is at least DIVIDEND_FLOOR away from 1.0.
DIVIDEND_FLOOR = 0.15        # |ratio−1| below this ⇒ stock dividend, not a split
_SNAP_TOL = 0.02             # ratio must be within 2% of a clean split ratio
# Clean split ratios (new/old): integer k:1 and 1:k, plus the handful of real
# fractional splits. Deliberately tight — 9:7-type "ratios" are spinoffs, not splits.
_FRACTIONALS = [3 / 2, 5 / 4, 4 / 3, 5 / 3, 5 / 2, 7 / 5, 8 / 5,
                2 / 3, 4 / 5, 3 / 4, 3 / 5, 2 / 5, 5 / 8]
_INTEGERS = [float(k) for k in range(2, 101)] + [1.0 / k for k in range(2, 101)]
_SPLIT_RATIOS = sorted(_INTEGERS + _FRACTIONALS)


def _label(ratio: float) -> str:
    if ratio >= 1:
        return f"{int(round(ratio))}:1" if abs(ratio - round(ratio)) < 0.02 else f"{ratio:.3g}:1"
    inv = 1.0 / ratio
    return f"1:{int(round(inv))}" if abs(inv - round(inv)) < 0.02 else f"1:{inv:.3g}"


def _snap_split(ratio: float) -> tuple[float, bool] | None:
    """Return (snapped_ratio, is_integer) if `ratio` is a clean share-split ratio,
    else None (stock dividend / spinoff / noise)."""
    if ratio <= 0 or abs(ratio - 1.0) < DIVIDEND_FLOOR:
        return None
    best = min(_SPLIT_RATIOS, key=lambda c: abs(ratio - c))
    if abs(ratio - best) > _SNAP_TOL * best:
        return None
    is_int = abs(best - round(best)) < 1e-6 or abs(1.0 / best - round(1.0 / best)) < 1e-6
    return best, is_int


def _normalize(raw: list[tuple[str, float]]) -> list[dict]:
    out = []
    for d, ratio in raw:
        snap = _snap_split(float(ratio))
        if snap is None:
            continue
        snapped, is_int = snap
        out.append({
            "date": pd.Timestamp(d),
            "ratio": snapped,
            "factor": 1.0 / snapped,
            "label": _label(snapped),
            "integer": is_int,
            "source": "yfinance",
        })
    return sorted(out, key=lambda x: x["date"])


def _cache_path(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}.json"


def _cache_fresh(p: Path, ttl_days: int) -> bool:
    if not p.exists():
        return False
    try:
        rec = json.loads(p.read_text())
        fetched = datetime.fromisoformat(rec["fetched"]).date()
        return (date.today() - fetched).days < ttl_days
    except Exception:
        return False


def fetch_splits(ticker: str, *, use_cache: bool = True,
                 ttl_days: int = CACHE_TTL_DAYS) -> list[dict] | None:
    """Return normalized splits for `ticker` (≥ SAMPLE_START), or None on failure.

    Reads a fresh on-disk cache if present; otherwise hits yfinance and rewrites
    the cache. None means "feed unavailable" (network/parse error) — distinct
    from [] which means "feed succeeded, no splits".
    """
    p = _cache_path(ticker)
    if use_cache and _cache_fresh(p, ttl_days):
        try:
            rec = json.loads(p.read_text())
            return _normalize([(s["date"], s["ratio"]) for s in rec["splits"]])
        except Exception:
            pass  # fall through to a live fetch

    try:
        import yfinance as yf
        s = yf.Ticker(ticker).splits  # Series indexed by date, value = new/old ratio
        if s is None or len(s) == 0:
            raw = []
        else:
            s = s[s.index >= SAMPLE_START]
            raw = [(str(pd.Timestamp(d).date()), float(r)) for d, r in s.items()]
    except Exception:
        # On failure, fall back to any (stale) cache rather than nothing.
        if p.exists():
            try:
                rec = json.loads(p.read_text())
                return _normalize([(s["date"], s["ratio"]) for s in rec["splits"]])
            except Exception:
                return None
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "fetched": date.today().isoformat(),
        "ticker": ticker.upper(),
        "splits": [{"date": d, "ratio": r} for d, r in raw],
    }, indent=0))
    return _normalize(raw)


if __name__ == "__main__":
    import sys
    for t in (sys.argv[1:] or ["NVDA", "NFLX", "XLU", "WMT", "SPY"]):
        sp = fetch_splits(t)
        if sp is None:
            print(f"{t:6} FEED UNAVAILABLE")
        else:
            print(f"{t:6} {[s['label'] + '@' + str(s['date'].date()) for s in sp]}")
