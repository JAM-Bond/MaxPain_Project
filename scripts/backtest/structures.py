"""Six options structures for Track A v1 backtest.

Each structure exposes:
    open_<name>(chain, entry_date, expiration) -> Position | None
    max_profit(pos) -> float      # dollars, per 1-contract position
    max_loss(pos) -> float        # dollars, per 1-contract position

The simulator walks forward from entry day through the option chain history,
computing close_cost() to mark the position to market and trigger exits.
"""
from typing import Optional

import pandas as pd

import config as C
from legs import (
    Leg, Position, select_by_delta, strike_by_offset,
    price_short_call, price_long_call, price_short_put, price_long_put,
    close_cost_call, close_cost_put,
)


def _ic_wing(spot: float) -> float:
    return max(C.IC_WING_WIDTH, C.IC_WING_PCT_SPOT * spot)


def _vertical_wing(spot: float) -> float:
    return max(C.VERTICAL_WING_WIDTH, C.VERTICAL_WING_PCT_SPOT * spot)


def _bfly_wing(spot: float) -> float:
    return max(C.BFLY_WING_WIDTH, C.BFLY_WING_PCT_SPOT * spot)


# ─────────────────────────────────────────────────────────────
# ENTRY — build positions from a front-chain snapshot
# ─────────────────────────────────────────────────────────────

def open_iron_condor(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """Short IC: short 30Δ call + put, long wings further out (scaled to spot in v2)."""
    short_call_row = select_by_delta(chain, C.IC_SHORT_DELTA)
    short_put_row = select_by_delta(chain, 1.0 - C.IC_SHORT_DELTA)
    if short_call_row is None or short_put_row is None:
        return None
    spot = float(short_call_row["stkPx"])
    wing = _ic_wing(spot)
    long_call_row = strike_by_offset(chain, short_call_row["strike"], +wing)
    long_put_row = strike_by_offset(chain, short_put_row["strike"], -wing)
    if long_call_row is None or long_put_row is None:
        return None

    sc = price_short_call(short_call_row); lc = price_long_call(long_call_row)
    sp = price_short_put(short_put_row);   lp = price_long_put(long_put_row)
    if None in (sc, lc, sp, lp):
        return None

    credit = sc + sp - lc - lp
    if credit <= 0:
        return None

    legs = [
        Leg("short", "call", float(short_call_row["strike"]), sc, float(short_call_row["delta"]), float(short_call_row["cMidIv"])),
        Leg("long",  "call", float(long_call_row["strike"]),  lc, float(long_call_row["delta"]),  float(long_call_row["cMidIv"])),
        Leg("short", "put",  float(short_put_row["strike"]),  sp, float(short_put_row["delta"]),  float(short_put_row["pMidIv"])),
        Leg("long",  "put",  float(long_put_row["strike"]),   lp, float(long_put_row["delta"]),   float(long_put_row["pMidIv"])),
    ]
    actual_wing = abs(legs[1].strike - legs[0].strike)  # snapped to real strikes
    return Position(
        structure="iron_condor",
        legs=legs, entry_date=entry_date, entry_credit=credit,
        expiration=expiration,
        underlying_entry=spot,
        notes={"short_call_k": legs[0].strike, "short_put_k": legs[2].strike,
               "wing_width": actual_wing},
    )


def open_short_strangle(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """Short 15Δ call + put. Undefined risk."""
    sc_row = select_by_delta(chain, C.STRANGLE_SHORT_DELTA)
    sp_row = select_by_delta(chain, 1.0 - C.STRANGLE_SHORT_DELTA)
    if sc_row is None or sp_row is None:
        return None
    sc = price_short_call(sc_row); sp = price_short_put(sp_row)
    if None in (sc, sp):
        return None
    credit = sc + sp
    legs = [
        Leg("short", "call", float(sc_row["strike"]), sc, float(sc_row["delta"]), float(sc_row["cMidIv"])),
        Leg("short", "put",  float(sp_row["strike"]), sp, float(sp_row["delta"]), float(sp_row["pMidIv"])),
    ]
    return Position(structure="short_strangle", legs=legs, entry_date=entry_date,
                    entry_credit=credit, expiration=expiration,
                    underlying_entry=float(sc_row["stkPx"]),
                    notes={"short_call_k": legs[0].strike, "short_put_k": legs[1].strike})


def open_bull_put(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """Short 30Δ put + long put wing below (scaled to spot in v2)."""
    sp_row = select_by_delta(chain, 1.0 - C.VERTICAL_SHORT_DELTA)
    if sp_row is None:
        return None
    spot = float(sp_row["stkPx"])
    wing = _vertical_wing(spot)
    lp_row = strike_by_offset(chain, sp_row["strike"], -wing)
    if lp_row is None:
        return None
    sp = price_short_put(sp_row); lp = price_long_put(lp_row)
    if None in (sp, lp):
        return None
    credit = sp - lp
    if credit <= 0:
        return None
    legs = [
        Leg("short", "put", float(sp_row["strike"]), sp, float(sp_row["delta"]), float(sp_row["pMidIv"])),
        Leg("long",  "put", float(lp_row["strike"]), lp, float(lp_row["delta"]), float(lp_row["pMidIv"])),
    ]
    actual_wing = abs(legs[0].strike - legs[1].strike)
    return Position(structure="bull_put", legs=legs, entry_date=entry_date,
                    entry_credit=credit, expiration=expiration,
                    underlying_entry=spot,
                    notes={"short_put_k": legs[0].strike, "wing_width": actual_wing})


def open_bull_put_mp(
    chain: pd.DataFrame, entry_date, expiration,
    max_pain: Optional[float] = None,
) -> Optional[Position]:
    """T-5 bull put credit spread anchored to max pain.

    Short put = strike nearest max_pain. Long put = next strike below.
    Skip when spot < max_pain (Phase 2c entry rule — MP-anchor only helps
    when spot sits above MP, which is the bull-regime case the lift was
    measured under). Phase 2c +$0.072/cycle vs 30Δ; Phase 2f signal-gated
    variant (contango+VRP>0) lifts to +$0.019/cycle mean, 87% win.
    """
    if max_pain is None:
        return None
    if chain.empty or "stkPx" not in chain.columns:
        return None
    spot = float(chain["stkPx"].iloc[0])
    if spot < max_pain:
        return None

    puts = chain.dropna(subset=["pBidPx", "pAskPx", "delta", "pMidIv"])
    puts = puts[(puts["pBidPx"] > 0) & (puts["pAskPx"] > 0)]
    if puts.empty:
        return None

    sp_idx = (puts["strike"] - max_pain).abs().idxmin()
    sp_row = puts.loc[sp_idx]
    short_K = float(sp_row["strike"])

    below = puts[puts["strike"] < short_K]
    if below.empty:
        return None
    lp_row = below.loc[below["strike"].idxmax()]

    sp = price_short_put(sp_row); lp = price_long_put(lp_row)
    if None in (sp, lp):
        return None
    credit = sp - lp
    if credit <= 0:
        return None
    legs = [
        Leg("short", "put", float(sp_row["strike"]), sp, float(sp_row["delta"]), float(sp_row["pMidIv"])),
        Leg("long",  "put", float(lp_row["strike"]), lp, float(lp_row["delta"]), float(lp_row["pMidIv"])),
    ]
    actual_wing = abs(legs[0].strike - legs[1].strike)
    return Position(structure="bull_put_mp", legs=legs, entry_date=entry_date,
                    entry_credit=credit, expiration=expiration,
                    underlying_entry=spot,
                    notes={"short_put_k": legs[0].strike,
                           "wing_width": actual_wing,
                           "max_pain": float(max_pain)})


def open_bear_call(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """Short 30Δ call + long call wing above (scaled to spot in v2)."""
    sc_row = select_by_delta(chain, C.VERTICAL_SHORT_DELTA)
    if sc_row is None:
        return None
    spot = float(sc_row["stkPx"])
    wing = _vertical_wing(spot)
    lc_row = strike_by_offset(chain, sc_row["strike"], +wing)
    if lc_row is None:
        return None
    sc = price_short_call(sc_row); lc = price_long_call(lc_row)
    if None in (sc, lc):
        return None
    credit = sc - lc
    if credit <= 0:
        return None
    legs = [
        Leg("short", "call", float(sc_row["strike"]), sc, float(sc_row["delta"]), float(sc_row["cMidIv"])),
        Leg("long",  "call", float(lc_row["strike"]), lc, float(lc_row["delta"]), float(lc_row["cMidIv"])),
    ]
    actual_wing = abs(legs[1].strike - legs[0].strike)
    return Position(structure="bear_call", legs=legs, entry_date=entry_date,
                    entry_credit=credit, expiration=expiration,
                    underlying_entry=spot,
                    notes={"short_call_k": legs[0].strike, "wing_width": actual_wing})


def _atm_row(chain: pd.DataFrame) -> Optional[pd.Series]:
    """Pick the strike closest to spot with valid IVs."""
    candidates = chain.dropna(subset=["strike", "stkPx", "cMidIv", "pMidIv"])
    if candidates.empty:
        return None
    spot = candidates["stkPx"].iloc[0]
    idx = (candidates["strike"] - spot).abs().idxmin()
    return candidates.loc[idx]


def open_iron_fly(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """Short iron fly: short ATM call+put, long wings above/below (scaled to spot in v2)."""
    atm = _atm_row(chain)
    if atm is None:
        return None
    K = atm["strike"]
    spot = float(atm["stkPx"])
    wing = _bfly_wing(spot)
    long_put_row = strike_by_offset(chain, K, -wing)
    long_call_row = strike_by_offset(chain, K, +wing)
    if long_put_row is None or long_call_row is None:
        return None
    sc = price_short_call(atm);       lc = price_long_call(long_call_row)
    sp = price_short_put(atm);        lp = price_long_put(long_put_row)
    if None in (sc, lc, sp, lp):
        return None
    credit = sc + sp - lc - lp
    if credit <= 0:
        return None
    legs = [
        Leg("short", "call", float(K),                       sc, float(atm["delta"]),           float(atm["cMidIv"])),
        Leg("long",  "call", float(long_call_row["strike"]), lc, float(long_call_row["delta"]), float(long_call_row["cMidIv"])),
        Leg("short", "put",  float(K),                       sp, float(atm["delta"]),           float(atm["pMidIv"])),
        Leg("long",  "put",  float(long_put_row["strike"]),  lp, float(long_put_row["delta"]),  float(long_put_row["pMidIv"])),
    ]
    actual_wing = min(abs(legs[1].strike - float(K)), abs(float(K) - legs[3].strike))
    return Position(structure="iron_fly", legs=legs, entry_date=entry_date,
                    entry_credit=credit, expiration=expiration,
                    underlying_entry=spot,
                    notes={"center_k": float(K), "wing_width": actual_wing})


def open_inverted_fly(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """Inverted iron fly: short wings (scaled to spot in v2), long ATM. Pay debit; profit on big move."""
    atm = _atm_row(chain)
    if atm is None:
        return None
    K = atm["strike"]
    spot = float(atm["stkPx"])
    wing = _bfly_wing(spot)
    short_put_row = strike_by_offset(chain, K, -wing)
    short_call_row = strike_by_offset(chain, K, +wing)
    if short_put_row is None or short_call_row is None:
        return None
    # long ATM call + put, short wing call + put
    lc = price_long_call(atm);        sc = price_short_call(short_call_row)
    lp = price_long_put(atm);         sp = price_short_put(short_put_row)
    if None in (lc, sc, lp, sp):
        return None
    debit = lc + lp - sc - sp
    if debit <= 0:
        return None
    legs = [
        Leg("long",  "call", float(K),                        lc, float(atm["delta"]),             float(atm["cMidIv"])),
        Leg("short", "call", float(short_call_row["strike"]), sc, float(short_call_row["delta"]),  float(short_call_row["cMidIv"])),
        Leg("long",  "put",  float(K),                        lp, float(atm["delta"]),             float(atm["pMidIv"])),
        Leg("short", "put",  float(short_put_row["strike"]),  sp, float(short_put_row["delta"]),   float(short_put_row["pMidIv"])),
    ]
    actual_wing = min(abs(legs[1].strike - float(K)), abs(float(K) - legs[3].strike))
    return Position(structure="inverted_fly", legs=legs, entry_date=entry_date,
                    entry_credit=-debit,  # convention: negative = debit paid
                    expiration=expiration,
                    underlying_entry=spot,
                    notes={"center_k": float(K), "wing_width": actual_wing})


def open_jade_lizard(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """Jade Lizard — short 30Δ put + short 30Δ call + long call (TastyTrade
    practitioner variant).

    The TastyTrade rule: total credit > call-wing width. The 15Δ long call
    is a heuristic; the binding rule is the credit-rule. We search candidate
    long calls (all strikes above the short call), choose the one with the
    SMALLEST wing where credit > wing AND long-call delta >= 0.05 (don't go
    so far OTM that the long call is degenerate).

    When the rule holds, upside risk is structurally zero: at any spot above
    the long call, P&L = credit − wing > 0. Put-side risk remains undefined
    (assignable max loss = short_put_strike − credit if spot expires at 0).

    Returns None if no qualifying long-call strike exists in the chain."""
    # Short put: target 30Δ short delta → call delta 0.70
    sp_row = select_by_delta(chain, 1.0 - C.VERTICAL_SHORT_DELTA)
    if sp_row is None:
        return None
    # Short call: target 30Δ
    sc_row = select_by_delta(chain, C.VERTICAL_SHORT_DELTA)
    if sc_row is None:
        return None

    spot = float(sp_row["stkPx"])
    sp = price_short_put(sp_row); sc = price_short_call(sc_row)
    if None in (sp, sc):
        return None

    short_call_k = float(sc_row["strike"])

    # Search candidate long calls: all strikes above the short call.
    # Pick the one with smallest wing where credit > wing AND long call delta >= 0.05.
    candidates = chain.dropna(subset=["delta", "cMidIv", "cBidPx", "cAskPx"])
    candidates = candidates[candidates["strike"] > short_call_k]
    if candidates.empty:
        return None
    candidates = candidates.sort_values("strike")  # closest-OTM first → smallest wing first

    best = None  # (wing, lc_row, lc_price, credit)
    for _, lc_row in candidates.iterrows():
        if lc_row["delta"] < 0.05:
            continue
        if lc_row["cMidIv"] < C.MIN_IV_FOR_PRICING:
            continue
        lc_px = price_long_call(lc_row)
        if lc_px is None:
            continue
        credit_try = sp + sc - lc_px
        if credit_try <= 0:
            continue
        wing_try = float(lc_row["strike"]) - short_call_k
        if credit_try > wing_try:
            best = (wing_try, lc_row, lc_px, credit_try)
            break  # first match by ascending strike is the smallest-wing match

    if best is None:
        return None

    call_wing, lc_row, lc, credit = best

    legs = [
        Leg("short", "put",  float(sp_row["strike"]), sp,
            float(sp_row["delta"]), float(sp_row["pMidIv"])),
        Leg("short", "call", float(sc_row["strike"]), sc,
            float(sc_row["delta"]), float(sc_row["cMidIv"])),
        Leg("long",  "call", float(lc_row["strike"]), lc,
            float(lc_row["delta"]), float(lc_row["cMidIv"])),
    ]
    return Position(
        structure="jade_lizard",
        legs=legs, entry_date=entry_date, entry_credit=credit,
        expiration=expiration, underlying_entry=spot,
        notes={
            "short_put_k": legs[0].strike,
            "short_call_k": legs[1].strike,
            "long_call_k": legs[2].strike,
            "call_wing": call_wing,
            "credit_minus_wing": credit - call_wing,  # the structural "no risk above" payoff
        },
    )


def open_zebra(chain: pd.DataFrame, entry_date, expiration) -> Optional[Position]:
    """ZEBRA — Zero Extrinsic Back Ratio.

    Buy 2× ITM call at ~70Δ (call delta 0.70).
    Sell 1× ATM call at ~50Δ (call delta 0.50).

    Defining rule: extrinsic_short >= total_extrinsic_long, so net theta
    is zero or positive. Practitioner variant: if 70/50 doesn't satisfy
    the rule, search the long-call delta upward (deeper ITM = less
    extrinsic per contract) until it does.

    Defined risk: max loss = total debit (realized only if spot expires
    below the long-call strike). All three legs same expiration.
    """
    # Short call: target 50Δ (ATM)
    sc_row = select_by_delta(chain, C.ZEBRA_SHORT_DELTA, tolerance=C.ZEBRA_SHORT_TOL)
    if sc_row is None:
        return None
    spot = float(sc_row["stkPx"])
    sc_px = price_short_call(sc_row)
    if sc_px is None:
        return None
    K_short = float(sc_row["strike"])
    # Short extrinsic = price - max(spot - K, 0)
    sc_extrinsic = sc_px - max(0.0, spot - K_short)

    # Search candidate long calls: deltas in [0.65, 0.85], strike strictly < K_short
    candidates = chain.dropna(subset=["delta", "cMidIv", "cBidPx", "cAskPx"])
    candidates = candidates[(candidates["strike"] < K_short)]
    if candidates.empty:
        return None
    candidates = candidates[(candidates["delta"] >= 0.55) & (candidates["delta"] <= 0.90)]
    if candidates.empty:
        return None
    # Sort by closeness to target 0.70 (preferred), but fall back to deeper ITM
    # if extrinsic rule isn't satisfied at 70Δ.
    candidates = candidates.sort_values("delta", ascending=False)  # deepest ITM first

    best = None  # (lc_row, lc_px, debit, capture_pref)
    for _, lc_row in candidates.iterrows():
        if lc_row["cMidIv"] < C.MIN_IV_FOR_PRICING:
            continue
        lc_px = price_long_call(lc_row)
        if lc_px is None:
            continue
        K_long = float(lc_row["strike"])
        lc_extrinsic = lc_px - max(0.0, spot - K_long)
        # Rule: short_extrinsic >= 2 * long_extrinsic (per contract) for net theta >= 0
        if sc_extrinsic >= 2.0 * lc_extrinsic:
            # Prefer the candidate closest to 0.70Δ that satisfies the rule
            delta_score = abs(float(lc_row["delta"]) - C.ZEBRA_LONG_DELTA)
            if best is None or delta_score < best[3]:
                best = (lc_row, lc_px, 2.0 * lc_px - sc_px, delta_score)

    if best is None:
        return None
    lc_row, lc, debit, _ = best

    if debit <= 0:
        return None  # if extrinsic + intrinsic relationship makes this a credit, structure is degenerate

    K_long = float(lc_row["strike"])
    lc_extrinsic = lc - max(0.0, spot - K_long)

    legs = [
        Leg("long",  "call", K_long,  lc, float(lc_row["delta"]),  float(lc_row["cMidIv"])),
        Leg("long",  "call", K_long,  lc, float(lc_row["delta"]),  float(lc_row["cMidIv"])),  # second long
        Leg("short", "call", K_short, sc_px, float(sc_row["delta"]), float(sc_row["cMidIv"])),
    ]
    # Net entry "credit": ZEBRA pays a debit, so this is negative
    return Position(
        structure="zebra",
        legs=legs, entry_date=entry_date,
        entry_credit=-debit,  # negative = debit paid, matching inverted_fly convention
        expiration=expiration, underlying_entry=spot,
        notes={
            "long_strike": K_long,
            "short_strike": K_short,
            "debit": debit,
            "long_extrinsic_each": lc_extrinsic,
            "long_extrinsic_total": 2.0 * lc_extrinsic,
            "short_extrinsic": sc_extrinsic,
            "extrinsic_cushion": sc_extrinsic - 2.0 * lc_extrinsic,
            "entry_delta": 2.0 * float(lc_row["delta"]) - float(sc_row["delta"]),
            "capital_outlay": 100.0 * spot,
            "capital_efficiency": debit / spot,  # debit / spot per contract
        },
    )


STRUCTURES = {
    "iron_condor":    open_iron_condor,
    "iron_fly":       open_iron_fly,
    "inverted_fly":   open_inverted_fly,
    "short_strangle": open_short_strangle,
    "bull_put":       open_bull_put,
    "bear_call":      open_bear_call,
    "jade_lizard":    open_jade_lizard,
    "zebra":          open_zebra,
}


# ─────────────────────────────────────────────────────────────
# MTM — cost to close the position at a later chain snapshot
# ─────────────────────────────────────────────────────────────

def close_cost(pos: Position, chain: pd.DataFrame) -> Optional[float]:
    """Total cost to close all legs. Positive = net cash paid to close.

    P&L at close = entry_credit - close_cost
    (if pos opened at credit; for debit structures entry_credit is negative, and
    `entry_credit - close_cost` remains the correct P&L signing).
    """
    total = 0.0
    for leg in pos.legs:
        row = chain[chain["strike"] == leg.strike]
        if row.empty:
            return None
        row = row.iloc[0]
        if leg.option_type == "call":
            px = close_cost_call(row, leg.side)
        else:
            px = close_cost_put(row, leg.side)
        if px is None:
            return None
        # Closing a short leg costs money (buy back). Closing a long leg generates cash (sell).
        if leg.side == "short":
            total += px
        else:
            total -= px
    return total


def intrinsic_value_at_expiry(pos: Position, underlying_close: float) -> float:
    """P&L contribution from intrinsic value at expiration, per 1-contract position.
    At expiry, long calls pay max(0, S-K), short calls pay -max(0, S-K), etc.
    Return value is SIGNED dollars. Combine with entry_credit for total P&L:
        total_pnl = entry_credit + intrinsic_at_expiry(pos, S)  -- NO
        total_pnl = entry_credit + sum(leg_intrinsic_signed)
    """
    total = 0.0
    for leg in pos.legs:
        if leg.option_type == "call":
            intr = max(0.0, underlying_close - leg.strike)
        else:
            intr = max(0.0, leg.strike - underlying_close)
        if leg.side == "long":
            total += intr
        else:
            total -= intr
    return total


def max_profit(pos: Position) -> float:
    """Max possible profit per 1-contract position (dollars)."""
    if pos.structure in ("iron_condor", "iron_fly", "bull_put", "bear_call"):
        return pos.entry_credit
    if pos.structure == "short_strangle":
        return pos.entry_credit
    if pos.structure == "inverted_fly":
        # Max profit = wing_width + entry_credit (entry_credit is negative = debit)
        return pos.notes["wing_width"] + pos.entry_credit
    if pos.structure == "jade_lizard":
        # Max profit = entry_credit (when spot expires between short put and short call)
        return pos.entry_credit
    if pos.structure == "zebra":
        # Unbounded upside; for sizing, use the practical max as 2x debit
        return float("inf")
    raise ValueError(f"Unknown structure {pos.structure}")


def max_loss(pos: Position) -> float:
    """Max possible loss per 1-contract position (dollars, positive number)."""
    if pos.structure == "iron_condor":
        return pos.notes["wing_width"] - pos.entry_credit
    if pos.structure == "iron_fly":
        return pos.notes["wing_width"] - pos.entry_credit
    if pos.structure in ("bull_put", "bear_call"):
        return pos.notes["wing_width"] - pos.entry_credit
    if pos.structure == "short_strangle":
        return float("inf")  # undefined-risk
    if pos.structure == "inverted_fly":
        return -pos.entry_credit  # debit paid is the max loss
    if pos.structure == "jade_lizard":
        # Put-side undefined risk → assignable max loss = short_put_strike - credit
        return pos.notes["short_put_k"] - pos.entry_credit
    if pos.structure == "zebra":
        # Defined risk = the debit paid (entry_credit is negative for ZEBRA)
        return -pos.entry_credit
    raise ValueError(f"Unknown structure {pos.structure}")
