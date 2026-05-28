"""H2 Phase 2 — five candidate weakness definitions (sealed in
docs/H2_PHASE2_PREREG.md §2).

Each definition is pure: takes pre-computed price panel + auxiliary maps,
returns a boolean DataFrame (dates × tickers) of matches.

The orchestrator in scripts/backtest/h2_phase2_validation.py:
  1. builds the close panel + sector ETF panel,
  2. precomputes rolling metrics (60d return, 200dma, etc.),
  3. calls each definition to get its mask DataFrame,
  4. evaluates each mask against the sealed Gates A-D.

Five definitions:
  R1 — rotation 60d (cross-sectional rank ≤ 0.20 AND own vs SPY ≤ -10pp)
  R2 — sector-relative 60d (own vs sector ETF ≤ -8pp)
  R3 — stage-2 break (above 200dma 30d ago, below 200dma today)
  R4 — compound W3 ∪ R3
  R5 — sector-load cohort gate (≥40% of sector's tracked names match R1/R2/R3)

R5 returns a date × sector matrix, not date × ticker. Its evaluation is
cohort-level, not per-name; the validation harness handles it differently.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

# ── Sealed thresholds (from H2_PHASE2_PREREG.md §2) ──────────────────────────
R1_RANK_THRESHOLD = 0.20       # bottom 20% by 60d return
R1_RELATIVE_GAP_PP = -10.0      # own 60d ret - SPY 60d ret ≤ -10pp
R2_SECTOR_GAP_PP = -8.0         # own 60d ret - sector ETF 60d ret ≤ -8pp
R3_LOOKBACK_DAYS = 30           # was above 200dma N days ago
LOOKBACK_60 = 60                # rotation window
LOOKBACK_200 = 200              # MA200 window

# W3 thresholds (carried forward from Phase 1, used in R4)
W3_RS_DECILE_THRESHOLD = 0.10
W3_DIST_52W_HIGH_THRESHOLD = -0.30
W3_LOOKBACK_252 = 252
W3_LOOKBACK_52W = 252

# R5 sector-load
R5_FRACTION_THRESHOLD = 0.40    # ≥40% of sector tracked names match weakness
R5_MIN_TRACKED_PER_SECTOR = 8   # need at least 8 names per sector to evaluate

# Sector ETF map (sealed in pre-reg §3)
SECTOR_ETF_MAP = {
    "information_technology": "XLK",
    "financials": "XLF",
    "health_care": "XLV",
    "communication_services": "XLC",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "energy": "XLE",
    "industrials": "XLI",
    "utilities": "XLU",
    "materials": "XLB",
    "real_estate": "XLRE",
}


# ─── R1: rotation 60d ───────────────────────────────────────────────────────

def compute_r1(panel: pd.DataFrame, spy: pd.Series) -> pd.DataFrame:
    """Boolean dates × tickers DataFrame: True where R1 fires.

    R1 = (60d return rank ≤ 0.20) AND (own 60d return - SPY 60d return ≤ -10pp)
    """
    ret_60 = panel.pct_change(LOOKBACK_60, fill_method=None)
    ranks_60 = ret_60.rank(axis=1, pct=True)
    spy_ret_60 = spy.pct_change(LOOKBACK_60, fill_method=None)
    # Subtract SPY's 60d return from each column (broadcast on index)
    relative_gap_pp = ret_60.subtract(spy_ret_60, axis=0) * 100.0
    mask = (ranks_60 <= R1_RANK_THRESHOLD) & (relative_gap_pp <= R1_RELATIVE_GAP_PP)
    return mask.fillna(False)


# ─── R2: sector-relative 60d ───────────────────────────────────────────────

def compute_r2(panel: pd.DataFrame, sector_etf_returns: pd.DataFrame,
                sector_of: dict[str, str]) -> pd.DataFrame:
    """Boolean dates × tickers DataFrame: True where R2 fires.

    R2 = own 60d return - own sector ETF 60d return ≤ -8pp.
    Names with sector in (_ETF, _UNKNOWN, sector w/o ETF data) → always False.

    sector_etf_returns: dates × ETF_symbol DataFrame of 60d returns.
    sector_of: ticker → sector_string (from lib.sector_map.get_sector).
    """
    ret_60 = panel.pct_change(LOOKBACK_60, fill_method=None)
    mask = pd.DataFrame(False, index=ret_60.index, columns=ret_60.columns)
    for ticker in ret_60.columns:
        sector = sector_of.get(ticker)
        if sector is None or sector in ("_ETF", "_UNKNOWN"):
            continue
        etf = SECTOR_ETF_MAP.get(sector)
        if etf is None or etf not in sector_etf_returns.columns:
            continue
        etf_ret = sector_etf_returns[etf]
        ticker_ret = ret_60[ticker]
        gap_pp = (ticker_ret - etf_ret) * 100.0
        mask[ticker] = (gap_pp <= R2_SECTOR_GAP_PP).fillna(False)
    return mask


# ─── R3: stage-2 break ─────────────────────────────────────────────────────

def compute_r3(panel: pd.DataFrame) -> pd.DataFrame:
    """Boolean dates × tickers DataFrame: True where R3 fires.

    R3 = price was above MA200 at (date - 30 trading days)
         AND price is below MA200 at date.
    """
    ma_200 = panel.rolling(LOOKBACK_200, min_periods=100).mean()
    above_now = panel > ma_200
    above_30d_ago = above_now.shift(R3_LOOKBACK_DAYS)
    mask = above_30d_ago & (~above_now)
    return mask.fillna(False)


# ─── W3 (used by R4) ───────────────────────────────────────────────────────

def compute_w3(panel: pd.DataFrame) -> pd.DataFrame:
    """W3 from Phase 1: RS bottom 10% + below 200dma + ≥30% off 52w high."""
    ret_252 = panel.pct_change(W3_LOOKBACK_252, fill_method=None)
    ranks_252 = ret_252.rank(axis=1, pct=True)
    ma_200 = panel.rolling(LOOKBACK_200, min_periods=100).mean()
    rolling_52w_high = panel.rolling(W3_LOOKBACK_52W, min_periods=120).max()
    dist_52w_high = panel / rolling_52w_high - 1.0
    below_ma_200 = panel < ma_200
    mask = (
        (ranks_252 <= W3_RS_DECILE_THRESHOLD) &
        below_ma_200 &
        (dist_52w_high <= W3_DIST_52W_HIGH_THRESHOLD)
    )
    return mask.fillna(False)


# ─── R4: compound W3 ∪ R3 ──────────────────────────────────────────────────

def compute_r4(panel: pd.DataFrame) -> pd.DataFrame:
    """Boolean dates × tickers DataFrame: True where W3 OR R3 fires."""
    w3 = compute_w3(panel)
    r3 = compute_r3(panel)
    # Align indices; reindex r3 to w3 (W3 has stricter min_periods so starts later)
    common_idx = w3.index.intersection(r3.index)
    common_cols = w3.columns.intersection(r3.columns)
    return (w3.loc[common_idx, common_cols] | r3.loc[common_idx, common_cols]).fillna(False)


# ─── R5: sector-load cohort gate ──────────────────────────────────────────

def compute_r5(per_name_masks: dict[str, pd.DataFrame],
                sector_of: dict[str, str]) -> pd.DataFrame:
    """Boolean dates × sector DataFrame: True where R5 fires (block sector).

    R5 = (≥40% of sector's tracked names match R1 OR R2 OR R3)
         AND (sector has ≥8 tracked names total).

    Inputs:
      per_name_masks: dict with 'r1', 'r2', 'r3' → dates × tickers DataFrames.
      sector_of: ticker → sector_string.
    """
    # Union of R1, R2, R3 per name
    r1 = per_name_masks["r1"]
    r2 = per_name_masks["r2"]
    r3 = per_name_masks["r3"]
    common_idx = r1.index.intersection(r2.index).intersection(r3.index)
    common_cols = r1.columns.intersection(r2.columns).intersection(r3.columns)
    weak_union = (
        r1.loc[common_idx, common_cols] |
        r2.loc[common_idx, common_cols] |
        r3.loc[common_idx, common_cols]
    )

    # Group columns by sector
    sectors = sorted({s for s in (sector_of.get(t) for t in common_cols)
                     if s and s not in ("_ETF", "_UNKNOWN")})
    sector_tickers = {s: [t for t in common_cols if sector_of.get(t) == s]
                      for s in sectors}

    result = pd.DataFrame(False, index=common_idx, columns=sectors)
    for s in sectors:
        tickers = sector_tickers[s]
        if len(tickers) < R5_MIN_TRACKED_PER_SECTOR:
            continue  # insufficient sample → always False
        sub = weak_union[tickers]
        fraction = sub.sum(axis=1) / float(len(tickers))
        result[s] = fraction >= R5_FRACTION_THRESHOLD
    return result


# ─── Single-name evaluators (for live/qualifier use after promotion) ──────

def evaluate_definition_at(definition_name: str, symbol: str, asof_date,
                            panel: pd.DataFrame, spy: pd.Series,
                            sector_etf_returns: pd.DataFrame,
                            sector_of: dict[str, str]) -> bool:
    """One-shot evaluator for a single (ticker, date) pair.

    Used by the qualifier post-promotion. Each call recomputes from the
    panel — fine for once-a-day verdict generation, would need caching for
    high-frequency use.
    """
    asof_ts = pd.Timestamp(asof_date)
    if definition_name == "R1":
        mask = compute_r1(panel, spy)
    elif definition_name == "R2":
        mask = compute_r2(panel, sector_etf_returns, sector_of)
    elif definition_name == "R3":
        mask = compute_r3(panel)
    elif definition_name == "R4":
        mask = compute_r4(panel)
    else:
        raise ValueError(f"Unknown definition: {definition_name}")
    if asof_ts not in mask.index or symbol not in mask.columns:
        return False
    return bool(mask.loc[asof_ts, symbol])
