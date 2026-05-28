"""Per-name macro-sensitivity profile reader + cohort helpers.

Reads the parquet built by scripts/macro/build_macro_profile.py and exposes
a small functional surface for the qualifier, daily alert, and post-mortem.

Source-of-truth: data/macro/macro_profile.parquet. Rebuild with
    python3.11 scripts/macro/build_macro_profile.py
which can run daily after build_betas_rolling.py (which depends on the
macro_join_13y.parquet that build_macro_join.py refreshes).

Key concepts (from Phase 3 stability validation):
  - β_mkt is the one quantitatively reliable input (94% of cohort STABLE)
  - β_dgs10 / β_credit / β_t10yie are regime-dependent for 35-50% of names;
    use only when the `beta_*_use` flag is True
  - dollar/oil/vol exposure are reported as tiers only (magnitudes too small
    for quantitative sizing, but directionally stable for diversification)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PROFILE_PATH = ROOT / "data/macro/macro_profile.parquet"


@lru_cache(maxsize=1)
def load_profile() -> pd.DataFrame:
    """Full macro profile table (cached for process lifetime)."""
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"{PROFILE_PATH} not found. Build it with:\n"
            f"  python3.11 scripts/macro/build_macro_profile.py"
        )
    return pd.read_parquet(PROFILE_PATH)


def get(ticker: str) -> dict | None:
    """One ticker's profile as a dict, or None if not in cohort."""
    df = load_profile()
    row = df[df["ticker"] == ticker]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def cohort_macro_concentration(tickers: list[str]) -> dict:
    """Count tier duplicates across a candidate list.

    Use this to surface macro-band concentration (analogous to sector cap):
    if 3 of 4 candidate trades are POS_HIGH on β_dgs10, they're all the same
    rate bet under one ticker symbol diversification.

    Returns:
        {tier_dimension: {tier_label: [ticker, ...]}}
        e.g., {'beta_dgs10_tier': {'POS_HIGH': ['BAC','JPM','WFC']},
               'beta_mkt_tier': {'MED_HIGH': ['MSFT','NVDA']}}
        Only tiers with ≥2 names are returned.
    """
    df = load_profile()
    sub = df[df["ticker"].isin(tickers)]
    out: dict = {}
    for col in ["beta_mkt_tier", "beta_dgs10_tier", "beta_credit_tier",
                "beta_t10yie_tier", "dollar_tier", "oil_tier", "vol_tier"]:
        groups = sub.groupby(col)["ticker"].apply(list).to_dict()
        dupes = {t: tk for t, tk in groups.items() if len(tk) >= 2 and t not in ("NEUTRAL", "NA")}
        if dupes:
            out[col] = dupes
    return out


def rate_stress_warning(tickers: list[str], direction: str) -> list[dict]:
    """Names whose current rate β faces drag from a directional rate move.

    Parameters
    ----------
    tickers : open positions or candidates
    direction : 'UP' (yields rising) or 'DOWN' (yields falling)

    Returns
    -------
    List of {ticker, beta_dgs10, tier, drag_severity} for names with material
    rate exposure pointing the wrong way for `direction`. Names with
    beta_dgs10_use=False are skipped (Phase 3 said don't trust the β).
    """
    df = load_profile()
    sub = df[df["ticker"].isin(tickers) & (df["beta_dgs10_use"] == True)].copy()
    if direction == "UP":
        sub = sub[sub["beta_dgs10"] < -0.02]  # falls when yields rise
    elif direction == "DOWN":
        sub = sub[sub["beta_dgs10"] > 0.02]   # falls when yields fall
    else:
        raise ValueError(f"direction must be 'UP' or 'DOWN', got {direction!r}")

    sub = sub.copy()
    sub["drag_severity"] = sub["beta_dgs10"].abs()
    sub = sub.sort_values("drag_severity", ascending=False)
    return sub[["ticker", "beta_dgs10", "beta_dgs10_tier", "drag_severity"]].to_dict("records")


def cohort_by_tier(col: str, tier_value: str) -> list[str]:
    """All tickers matching a particular tier (e.g., 'POS_HIGH' rate β).

    Useful for cohort-construction queries:
        cohort_by_tier('beta_dgs10_tier', 'NEG_HIGH')  → defensive-rate names
        cohort_by_tier('dollar_tier', 'USD_INV_STRONG') → gold/metal names
    """
    df = load_profile()
    return df[df[col] == tier_value]["ticker"].tolist()
