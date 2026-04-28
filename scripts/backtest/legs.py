"""Leg selection and pricing primitives for options structures.

ORATS schema: one row per (ticker, expiration, strike) with a single `delta` column
that is the CALL delta (range ~0 to 1). Put delta = call_delta - 1.
"""
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config as C


@dataclass
class Leg:
    side: str            # "long" or "short"
    option_type: str     # "call" or "put"
    strike: float
    price: float         # entry price (bid for short-sell, ask for long-buy)
    delta: float
    mid_iv: float


@dataclass
class Position:
    structure: str
    legs: list[Leg]
    entry_date: pd.Timestamp
    entry_credit: float  # positive = credit received; negative = debit paid
    expiration: pd.Timestamp
    underlying_entry: float
    notes: dict          # captures selected strikes and other diagnostics


def select_by_delta(chain: pd.DataFrame, target_delta: float,
                    tolerance: float = C.DELTA_TOLERANCE) -> Optional[pd.Series]:
    """Return the single row with delta closest to target, if within tolerance."""
    candidates = chain.dropna(subset=["delta", "cMidIv", "pMidIv"])
    if candidates.empty:
        return None
    idx = (candidates["delta"] - target_delta).abs().idxmin()
    row = candidates.loc[idx]
    if abs(row["delta"] - target_delta) > tolerance:
        return None
    if row["cMidIv"] < C.MIN_IV_FOR_PRICING or row["pMidIv"] < C.MIN_IV_FOR_PRICING:
        return None
    return row


def strike_by_offset(chain: pd.DataFrame, reference_strike: float, offset: float,
                     tolerance_mult: float = 3.0) -> Optional[pd.Series]:
    """Return the strike closest to (reference + offset), strictly in the offset direction.

    Directional requirement fixes a degenerate case on coarse strike grids: with a small
    offset (e.g. 0.25% of a low-priced stock), the "wing" strike could snap to the
    reference strike itself, producing a zero-width spread. We require strikes > reference
    when offset > 0 and < reference when offset < 0.
    """
    if offset > 0:
        candidates = chain[chain["strike"] > reference_strike]
    elif offset < 0:
        candidates = chain[chain["strike"] < reference_strike]
    else:
        return None
    if candidates.empty:
        return None
    target = reference_strike + offset
    idx = (candidates["strike"] - target).abs().idxmin()
    row = candidates.loc[idx]
    if abs(row["strike"] - target) > abs(offset) * tolerance_mult:
        return None
    return row


def _mid(bid, ask) -> Optional[float]:
    if pd.isna(bid) or pd.isna(ask):
        return None
    b, a = float(bid), float(ask)
    if b <= 0 or a <= 0 or a < b:
        return None
    return (b + a) / 2.0


def _bid(row: pd.Series, col: str) -> Optional[float]:
    p = row.get(col)
    return float(p) if pd.notna(p) and p > 0 else None


def _slip_sell(bid, ask, frac: float) -> Optional[float]:
    """Seller's realized fill: mid − frac·(spread/2). frac=0→mid, frac=1→bid."""
    if pd.isna(bid) or pd.isna(ask):
        return None
    b, a = float(bid), float(ask)
    if b <= 0 or a <= 0 or a < b:
        return None
    return (b + a) / 2.0 - frac * (a - b) / 2.0


def _slip_buy(bid, ask, frac: float) -> Optional[float]:
    """Buyer's realized fill: mid + frac·(spread/2). frac=0→mid, frac=1→ask."""
    if pd.isna(bid) or pd.isna(ask):
        return None
    b, a = float(bid), float(ask)
    if b <= 0 or a <= 0 or a < b:
        return None
    return (b + a) / 2.0 + frac * (a - b) / 2.0


def price_short_call(row: pd.Series) -> Optional[float]:
    if C.PRICING_MODE == "slip":
        return _slip_sell(row.get("cBidPx"), row.get("cAskPx"), C.PRICING_SLIP_FRAC)
    if C.PRICING_MODE == "mid":
        return _mid(row.get("cBidPx"), row.get("cAskPx"))
    return _bid(row, "cBidPx")


def price_long_call(row: pd.Series) -> Optional[float]:
    if C.PRICING_MODE == "slip":
        return _slip_buy(row.get("cBidPx"), row.get("cAskPx"), C.PRICING_SLIP_FRAC)
    if C.PRICING_MODE == "mid":
        return _mid(row.get("cBidPx"), row.get("cAskPx"))
    return _bid(row, "cAskPx")


def price_short_put(row: pd.Series) -> Optional[float]:
    if C.PRICING_MODE == "slip":
        return _slip_sell(row.get("pBidPx"), row.get("pAskPx"), C.PRICING_SLIP_FRAC)
    if C.PRICING_MODE == "mid":
        return _mid(row.get("pBidPx"), row.get("pAskPx"))
    return _bid(row, "pBidPx")


def price_long_put(row: pd.Series) -> Optional[float]:
    if C.PRICING_MODE == "slip":
        return _slip_buy(row.get("pBidPx"), row.get("pAskPx"), C.PRICING_SLIP_FRAC)
    if C.PRICING_MODE == "mid":
        return _mid(row.get("pBidPx"), row.get("pAskPx"))
    return _bid(row, "pAskPx")


def close_cost_call(row: pd.Series, side: str) -> Optional[float]:
    """Cost to exit at current snapshot. Closing a short → buy; closing a long → sell."""
    if C.PRICING_MODE == "slip":
        if side == "short":
            return _slip_buy(row.get("cBidPx"), row.get("cAskPx"), C.PRICING_SLIP_FRAC)
        return _slip_sell(row.get("cBidPx"), row.get("cAskPx"), C.PRICING_SLIP_FRAC)
    if C.PRICING_MODE == "mid":
        return _mid(row.get("cBidPx"), row.get("cAskPx"))
    col = "cAskPx" if side == "short" else "cBidPx"
    return _bid(row, col)


def close_cost_put(row: pd.Series, side: str) -> Optional[float]:
    if C.PRICING_MODE == "slip":
        if side == "short":
            return _slip_buy(row.get("pBidPx"), row.get("pAskPx"), C.PRICING_SLIP_FRAC)
        return _slip_sell(row.get("pBidPx"), row.get("pAskPx"), C.PRICING_SLIP_FRAC)
    if C.PRICING_MODE == "mid":
        return _mid(row.get("pBidPx"), row.get("pAskPx"))
    col = "pAskPx" if side == "short" else "pBidPx"
    return _bid(row, col)
