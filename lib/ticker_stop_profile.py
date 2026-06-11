"""Per-ticker breach-recovery / stop-loss profile — DESCRIPTIVE alert annotation.

Reads data/profile/per_ticker_stop_profile.parquet (from
scripts/backtest/per_ticker_stop_study.py) and answers, per (ticker, structure):

  • MEAN-REVERTER → breaches recover; hold through them. Surfaces the recovery
    rate and the median trading days to recovery ("how long to wait").
  • NON-REVERTER (robust only) → breaches keep going; surfaces a per-ticker
    STOP depth (% beyond the short strike).

Robustness gate: a name is only called NON-REVERTER if its stop signal is
walk-forward stable (train/test same sign). Non-robust "non-reverters" (the
sign flips across the split = sampling noise) fall back to mean-reverter, so the
alert never recommends a stop on an unstable signal.

DESCRIPTIVE only: this informs the trader's MANUAL stop placement; it does not
gate or change any recommended size. (Pre-register before it gates anything.)
Soft-fail: returns None if the profile is missing/unreadable.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

PROFILE_PATH = Path.home() / "MaxPain_Project" / "data/profile/per_ticker_stop_profile.parquet"
MIN_BREACHED = 12  # below this → insufficient evidence, no annotation


def _norm_structure(structure: str) -> Optional[str]:
    s = (structure or "").lower()
    if s.startswith("bull_put"):
        return "bull_put"
    if s.startswith("bear_call"):
        return "bear_call"
    return None  # only credit verticals carry a breach-stop profile


@lru_cache(maxsize=1)
def _load() -> Optional[pd.DataFrame]:
    try:
        if not PROFILE_PATH.exists():
            return None
        return pd.read_parquet(PROFILE_PATH)
    except Exception:
        return None


def lookup(ticker: str, structure: str) -> Optional[dict]:
    """Return the (ticker, structure) profile with a robustness-gated effective
    classification, or None if unavailable/insufficient."""
    struct = _norm_structure(structure)
    if struct is None:
        return None
    df = _load()
    if df is None:
        return None
    m = df[(df["ticker"] == ticker) & (df["structure"] == struct)]
    if m.empty:
        return None
    r = m.iloc[0]
    if int(r["n_breached"]) < MIN_BREACHED or r["classification"] == "INSUFFICIENT":
        return None
    # robustness gate: only a walk-forward-stable non-reverter keeps its stop
    robust_nonrevert = (r["classification"] == "NON_REVERT") and bool(r["wf_stable"]) \
        and pd.notna(r["stop_depth"])
    effective = "NON_REVERT" if robust_nonrevert else "MEAN_REVERT"
    return {
        "ticker": ticker, "structure": struct, "effective": effective,
        "raw_classification": r["classification"], "wf_stable": bool(r["wf_stable"]),
        "stop_depth": float(r["stop_depth"]) if pd.notna(r["stop_depth"]) else None,
        "recovery_rate": float(r["recovery_rate"]) if pd.notna(r["recovery_rate"]) else None,
        "median_recovery_days": float(r["median_recovery_days"]) if pd.notna(r["median_recovery_days"]) else None,
        "n_breached": int(r["n_breached"]),
    }


def card_note(ticker: str, structure: str) -> Optional[dict]:
    """One-line breach-recovery note for a credit-vertical construction card.
    Returns {'text','html'} or None. Descriptive — informs manual stop placement."""
    p = lookup(ticker, structure)
    if p is None:
        return None
    rate = f"{p['recovery_rate']*100:.0f}%" if p["recovery_rate"] is not None else "?"
    if p["effective"] == "NON_REVERT":
        days = (f", and when it does, ~{p['median_recovery_days']:.0f}d"
                if p["median_recovery_days"] is not None else "")
        text = (f"  ⛔ BREACH PROFILE: {ticker} {p['structure']} historically does NOT mean-revert "
                f"(only {rate} of breaches recovered{days}) — set a STOP ~{p['stop_depth']*100:.0f}% "
                f"beyond the short strike rather than riding the 2× rule. [walk-forward stable]")
        color, bg = "#a00", "#fdecea"
    else:
        days = (f"~{p['median_recovery_days']:.0f} trading days"
                if p["median_recovery_days"] is not None else "a few days")
        soft = " (a no-revert read existed but failed walk-forward — treat as revert)" \
            if p["raw_classification"] == "NON_REVERT" else ""
        text = (f"  🔁 BREACH PROFILE: {ticker} {p['structure']} mean-reverts — {rate} of short-strike "
                f"breaches recover, typically within {days}; hold through a transient breach{soft}.")
        color, bg = "#1a5fb4", "#f0f6ff"
    html = (f"<div style='font-size:12px;color:{color};margin:4px 0 12px 0;"
            f"padding:6px 10px;background:{bg};border-left:3px solid {color}'>{text.strip()}</div>")
    return {"text": text, "html": html}
