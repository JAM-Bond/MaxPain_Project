"""Per-day, per-ticker feature extractors.

Each function takes the rows for a single (ticker, trade_date) slice and returns
a scalar value. Designed to be extended: add a feature function, register it in
build_daily_summary, and it flows through to the final profile.
"""
import numpy as np
import pandas as pd


def total_oi(rows: pd.DataFrame) -> float:
    return float(rows["cOi"].sum() + rows["pOi"].sum())


def total_volume(rows: pd.DataFrame) -> float:
    return float(rows["cVolu"].sum() + rows["pVolu"].sum())


def n_contracts(rows: pd.DataFrame) -> int:
    return int(len(rows))


def n_expirations(rows: pd.DataFrame) -> int:
    return int(rows["expirDate"].nunique())


def min_dte(rows: pd.DataFrame) -> float:
    return float(rows["yte"].min() * 365.0)


def max_dte(rows: pd.DataFrame) -> float:
    return float(rows["yte"].max() * 365.0)


def stk_px(rows: pd.DataFrame) -> float:
    return float(rows["stkPx"].iloc[0])


def atm_iv(rows: pd.DataFrame) -> float:
    """IV at strike closest to spot, nearest front-month expiration. Avg of call/put mid IV."""
    spot = rows["stkPx"].iloc[0]
    front = rows.loc[rows["yte"] == rows["yte"].min()]
    if front.empty:
        return np.nan
    idx = (front["strike"] - spot).abs().idxmin()
    row = front.loc[idx]
    c_iv = row.get("cMidIv", np.nan)
    p_iv = row.get("pMidIv", np.nan)
    vals = [v for v in (c_iv, p_iv) if pd.notna(v) and v > 0]
    return float(np.mean(vals)) if vals else np.nan


def iv_skew_10d(rows: pd.DataFrame) -> float:
    """10-delta put IV minus 10-delta call IV on the front month. Positive = put-side premium.

    ORATS stores call delta (range ~0 to 1) in one column per (strike, expiration) row.
    10-delta call row: call_delta ≈ 0.10
    10-delta put row:  call_delta ≈ 0.90  (put_delta = call_delta − 1 ≈ −0.10)
    """
    front = rows.loc[rows["yte"] == rows["yte"].min()].copy()
    if len(front) < 20 or "delta" not in front.columns:
        return np.nan
    front = front.dropna(subset=["delta", "cMidIv", "pMidIv"])
    if front.empty:
        return np.nan
    call_idx = (front["delta"] - 0.10).abs().idxmin()
    put_idx = (front["delta"] - 0.90).abs().idxmin()
    c_iv = front.loc[call_idx, "cMidIv"]
    p_iv = front.loc[put_idx, "pMidIv"]
    if pd.isna(p_iv) or pd.isna(c_iv) or p_iv <= 0 or c_iv <= 0:
        return np.nan
    return float(p_iv - c_iv)


def has_weekly(rows: pd.DataFrame) -> int:
    return int(rows["yte"].min() * 365.0 <= 7.5)


FEATURES = {
    "n_contracts": n_contracts,
    "n_expirations": n_expirations,
    "total_oi": total_oi,
    "total_volume": total_volume,
    "stk_px": stk_px,
    "min_dte": min_dte,
    "max_dte": max_dte,
    "atm_iv": atm_iv,
    "iv_skew_10d": iv_skew_10d,
    "has_weekly": has_weekly,
}
