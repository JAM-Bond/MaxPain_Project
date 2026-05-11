"""Query helpers for the Cohorts page.

Joins the static cohort lists from `scripts.qualifier.gate_config` with
the per-ticker walk-forward recommendation parquets in `data/profile/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PROFILE_DIR = ROOT / "data" / "profile"
sys.path.insert(0, str(ROOT))


def _gate_config():
    from scripts.qualifier import gate_config
    return gate_config


def _load_recommendation(parquet_name: str) -> pd.DataFrame:
    p = PROFILE_DIR / parquet_name
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def bull_put_cohort_df() -> pd.DataFrame:
    """One row per ticker in COHORT_BULL_PUT with walk-forward recommendation
    columns left-joined (NULL when ticker isn't in the recommendation parquet)."""
    g = _gate_config()
    cohort = pd.DataFrame({"Symbol": list(g.COHORT_BULL_PUT)})
    rec = _load_recommendation("bull_put_moneyness_recommendation.parquet")
    if rec.empty:
        cohort["Moneyness"] = "—"
        cohort["Exit rule"] = "—"
        cohort["val n"] = pd.NA
        cohort["val p"] = pd.NA
        return cohort
    # Pick mgd50 row preferentially (this is the rule we trade), fall back to other
    rec_mgd = rec[rec["exit_rule"] == "mgd50"].copy()
    rec_other = rec[~rec["ticker"].isin(rec_mgd["ticker"])]
    rec_combined = pd.concat([rec_mgd, rec_other], ignore_index=True)
    rec_combined = rec_combined.drop_duplicates("ticker", keep="first")
    cohort = cohort.merge(
        rec_combined[["ticker", "exit_rule", "recommended_moneyness", "val_n", "val_p"]],
        left_on="Symbol", right_on="ticker", how="left",
    ).drop(columns=["ticker"])
    cohort = cohort.rename(columns={
        "exit_rule": "Exit rule",
        "recommended_moneyness": "Moneyness",
        "val_n": "val n",
        "val_p": "val p",
    })
    return cohort.sort_values("Symbol").reset_index(drop=True)


def bear_call_cohort_df() -> pd.DataFrame:
    g = _gate_config()
    cohort = pd.DataFrame({"Symbol": list(g.COHORT_BEAR_CALL)})
    rec = _load_recommendation("bear_call_moneyness_recommendation.parquet")
    if rec.empty:
        cohort["Moneyness"] = "—"; cohort["Exit rule"] = "—"
        cohort["val n"] = pd.NA; cohort["val p"] = pd.NA
        return cohort
    rec_mgd = rec[rec["exit_rule"] == "mgd50"].copy()
    rec_other = rec[~rec["ticker"].isin(rec_mgd["ticker"])]
    rec_combined = pd.concat([rec_mgd, rec_other], ignore_index=True).drop_duplicates("ticker", keep="first")
    cohort = cohort.merge(
        rec_combined[["ticker", "exit_rule", "recommended_moneyness", "val_n", "val_p"]],
        left_on="Symbol", right_on="ticker", how="left",
    ).drop(columns=["ticker"])
    return cohort.rename(columns={
        "exit_rule": "Exit rule",
        "recommended_moneyness": "Moneyness",
        "val_n": "val n", "val_p": "val p",
    }).sort_values("Symbol").reset_index(drop=True)


def inverted_fly_cohort_df() -> pd.DataFrame:
    """Combined IF cohort table: pair vs single, with wing-width recommendation."""
    g = _gate_config()
    rows = []
    for sym in g.COHORT_INVERTED_FLY_PAIR:
        rows.append({"Symbol": sym, "Variant": "pair"})
    for sym in g.COHORT_INVERTED_FLY_SINGLE:
        rows.append({"Symbol": sym, "Variant": "single"})
    cohort = pd.DataFrame(rows)
    rec = _load_recommendation("inverted_fly_wing_recommendation.parquet")
    if rec.empty:
        cohort["Wing"] = "—"; cohort["val n"] = pd.NA; cohort["val p"] = pd.NA
        return cohort
    cohort = cohort.merge(
        rec[["ticker", "recommended_variant", "val_n", "val_p"]],
        left_on="Symbol", right_on="ticker", how="left",
    ).drop(columns=["ticker"])
    return cohort.rename(columns={
        "recommended_variant": "Wing",
        "val_n": "val n", "val_p": "val p",
    }).sort_values(["Variant", "Symbol"]).reset_index(drop=True)


def zebra_cohort_df() -> pd.DataFrame:
    g = _gate_config()
    rows = [{"Symbol": s, "Tier": "tier1"} for s in g.COHORT_ZEBRA_TIER1]
    rows += [{"Symbol": s, "Tier": "tier2"} for s in g.COHORT_ZEBRA_TIER2]
    return pd.DataFrame(rows).sort_values(["Tier", "Symbol"]).reset_index(drop=True)


def earnings_cohort_df() -> pd.DataFrame:
    g = _gate_config()
    rows = []
    for s in g.COHORT_EARNINGS_BULL_PUT:
        rows.append({"Symbol": s, "Bias": "bull_put"})
    for s in g.COHORT_EARNINGS_BEAR_CALL:
        rows.append({"Symbol": s, "Bias": "bear_call"})
    for s in g.COHORT_EARNINGS_INVERTED_FLY:
        rows.append({"Symbol": s, "Bias": "inverted_fly"})
    if not rows:
        return pd.DataFrame(columns=["Symbol", "Bias", "Tier"])
    df = pd.DataFrame(rows)
    df["Tier"] = df["Symbol"].apply(lambda s: "T1" if s in g.EARNINGS_T1_NAMES else "T3")
    return df.sort_values(["Bias", "Symbol"]).reset_index(drop=True)


def gate_constants() -> dict:
    """Pull the constants worth surfacing on the page."""
    g = _gate_config()
    return {
        "MIN_CREDIT_WIDTH": g.MIN_CREDIT_WIDTH,
        "MAX_SPOT_INVERTED_FLY": g.MAX_SPOT_INVERTED_FLY,
        "MAX_SPOT_ZEBRA": g.MAX_SPOT_ZEBRA,
        "BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD": g.BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD,
        "ZEBRA_TREND_BELOW_200DMA_THRESHOLD": g.ZEBRA_TREND_BELOW_200DMA_THRESHOLD,
        "WINDOW_BULL_PUT_45DTE": g.WINDOW_BULL_PUT_45DTE,
        "WINDOW_BEAR_CALL_45DTE": g.WINDOW_BEAR_CALL_45DTE,
        "WINDOW_INVERTED_FLY_45DTE": g.WINDOW_INVERTED_FLY_45DTE,
        "WINDOW_ZEBRA_75DTE": g.WINDOW_ZEBRA_75DTE,
    }
