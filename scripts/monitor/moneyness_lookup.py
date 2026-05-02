"""
Per-ticker moneyness recommendation lookup.

Reads walk-forward-validated recommendation parquets for bull_put and bear_call
and exposes a single `recommended_short_delta(ticker, structure, exit_rule)`
helper used by trade_construction.py.

Default behavior: if no walk-forward-validated recommendation exists for the
(ticker, exit_rule) pair, falls back to OTM 0.30 (current TRADING_PLAN spec).
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"

BULL_PUT_REC = ROOT / "data/profile/bull_put_moneyness_recommendation.parquet"
BEAR_CALL_REC = ROOT / "data/profile/bear_call_moneyness_recommendation.parquet"
IF_WING_REC = ROOT / "data/profile/inverted_fly_wing_recommendation.parquet"

# Maps from "moneyness label" → C.VERTICAL_SHORT_DELTA value
MONEYNESS_TO_DELTA = {"OTM": 0.30, "ATM": 0.50, "ITM": 0.70}

# Maps from IF wing variant label → C.BFLY_WING_PCT_SPOT value
IF_WING_PCT = {
    "narrow_2pct": 0.02,
    "medium_5pct": 0.05,
    "wide_10pct": 0.10,
    "vwide_15pct": 0.15,
}

# Default fallback (current TRADING_PLAN spec)
DEFAULT_LABEL = "OTM"
# Default IF wing — universe-wide best at slip=0.50 managed; safe choice
# for tickers without per-ticker walk-forward evidence.
DEFAULT_IF_WING = "medium_5pct"


class Recommendation(NamedTuple):
    label: str          # OTM / ATM / ITM
    short_delta: float  # 0.30 / 0.50 / 0.70 — value for C.VERTICAL_SHORT_DELTA
    is_default: bool    # True if no walk-forward evidence; False if validated
    evidence_pair: str | None
    train_p: float | None
    val_p: float | None
    train_n: int | None
    val_n: int | None


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


_BP_LOOKUP = _load(BULL_PUT_REC)
_BC_LOOKUP = _load(BEAR_CALL_REC)
_IF_LOOKUP = _load(IF_WING_REC)


def recommended_short_delta(
    ticker: str, structure: str, exit_rule: str = "mgd50",
) -> Recommendation:
    """For a (ticker, structure) and the assumed exit rule, return the
    walk-forward-validated short-delta recommendation, or OTM default
    if none exists.

    structure: "bull_put", "bull_put_earnings", "bear_call", "bear_call_earnings"
    exit_rule: "mgd50" (default — what the framework actually uses) or "held"
    """
    if structure.startswith("bull_put"):
        df = _BP_LOOKUP
    elif structure.startswith("bear_call"):
        df = _BC_LOOKUP
    else:
        return Recommendation(DEFAULT_LABEL, MONEYNESS_TO_DELTA[DEFAULT_LABEL],
                              True, None, None, None, None, None)

    if df.empty:
        return Recommendation(DEFAULT_LABEL, MONEYNESS_TO_DELTA[DEFAULT_LABEL],
                              True, None, None, None, None, None)

    match = df[(df["ticker"] == ticker) & (df["exit_rule"] == exit_rule)]
    if match.empty:
        return Recommendation(DEFAULT_LABEL, MONEYNESS_TO_DELTA[DEFAULT_LABEL],
                              True, None, None, None, None, None)

    r = match.iloc[0]
    label = r["recommended_moneyness"]
    return Recommendation(
        label=label,
        short_delta=MONEYNESS_TO_DELTA[label],
        is_default=False,
        evidence_pair=r["evidence_pair"],
        train_p=float(r["train_p"]),
        val_p=float(r["val_p"]),
        train_n=int(r["train_n"]),
        val_n=int(r["val_n"]),
    )


class WingRecommendation(NamedTuple):
    label: str          # narrow_2pct / medium_5pct / wide_10pct / vwide_15pct
    wing_pct: float     # 0.02 / 0.05 / 0.10 / 0.15 — value for C.BFLY_WING_PCT_SPOT
    is_default: bool
    evidence_pair: str | None
    train_p: float | None
    val_p: float | None
    train_n: int | None
    val_n: int | None


def recommended_if_wing(ticker: str) -> WingRecommendation:
    """For an inverted-fly ticker, return the walk-forward-validated wing
    variant, or the default fallback if none."""
    if _IF_LOOKUP.empty:
        return WingRecommendation(DEFAULT_IF_WING, IF_WING_PCT[DEFAULT_IF_WING],
                                   True, None, None, None, None, None)
    match = _IF_LOOKUP[_IF_LOOKUP["ticker"] == ticker]
    if match.empty:
        return WingRecommendation(DEFAULT_IF_WING, IF_WING_PCT[DEFAULT_IF_WING],
                                   True, None, None, None, None, None)
    r = match.iloc[0]
    label = r["recommended_variant"]
    return WingRecommendation(
        label=label,
        wing_pct=IF_WING_PCT[label],
        is_default=False,
        evidence_pair=r["evidence_pair"],
        train_p=float(r["train_p"]),
        val_p=float(r["val_p"]),
        train_n=int(r["train_n"]),
        val_n=int(r["val_n"]),
    )
