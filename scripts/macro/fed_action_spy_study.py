#!/usr/bin/env python3.11
"""
Fed action → SPY forward-return study.

For each FOMC action in the 13-year window (cuts + hikes), compute SPY
forward returns at T+1, T+5, T+30, T+90 and tag with regime context at
the time of the action.

Data sources (all already on disk after today's macro-sensitivity build):
  - Agent_Project ChromaDB `fomc_decisions` collection (31 events, 2015-12 → 2025-12)
  - data/macro/prices_daily_13y.parquet (SPY close + log_ret series)
  - data/macro/macro_join_13y.parquet (DFF / DGS10 / T10Y2Y / VIXCLS aligned)

Hold events not in scope for v1 — ChromaDB only carries actions (cuts+hikes).
v2 would add inferred hold dates from the FOMC calendar.

Output: data/profile/fed_action_spy_response.parquet (one row per event, all
forward returns + regime columns); markdown report to stdout.

Caveats (built in):
  - N small per refined bucket (31 actions / 2-3 regime buckets each)
  - Selection bias severe — cuts cluster in distress, hikes cluster in tightening
  - Pre-meeting expectation/surprise split deferred to v3 (needs historical CME FedWatch)

Usage:
    python3.11 fed_action_spy_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PRICES_PATH = ROOT / "data/macro/prices_daily_13y.parquet"
MACRO_PATH = ROOT / "data/macro/macro_join_13y.parquet"
OUT_PATH = ROOT / "data/profile/fed_action_spy_response.parquet"

HORIZONS = [1, 5, 30, 90]


def load_fomc_events() -> pd.DataFrame:
    sys.path.append(str(Path.home() / "Agent_Project" / "shared"))
    from chromadb_client import DataPipelineChromaDB
    db = DataPipelineChromaDB()
    r = db.get_all_documents("fomc_decisions")
    rows = [{"meeting_date": m.get("meeting_date"),
             "action": m.get("action"),
             "change_bps": m.get("change_bps"),
             "previous_rate": m.get("previous_rate"),
             "new_rate": m.get("new_rate")} for m in r["metadatas"]]
    df = pd.DataFrame(rows)
    df["meeting_date"] = pd.to_datetime(df["meeting_date"])
    return df.sort_values("meeting_date").reset_index(drop=True)


def compute_forward_returns(events: pd.DataFrame, spy_close: pd.Series) -> pd.DataFrame:
    """For each event, compute SPY close-to-close returns at each horizon.

    Method: find the spy_close index value at-or-after each meeting_date, then
    shift forward N trading days. Returns the percentage change from event
    close to T+N close.
    """
    # spy_close is a Series indexed by date
    out = events.copy()

    # Snap each meeting date to the next available trading day (some FOMC
    # decisions fall on a Wednesday with full session — no snap needed in
    # most cases; the searchsorted handles holidays defensively)
    spy_dates = pd.DatetimeIndex(spy_close.index)
    idx_per_event = spy_dates.searchsorted(out["meeting_date"].values, side="left")

    # spy_pre is SPY close ON the FOMC day itself (or next trading day if FOMC
    # was on a non-trading day — rare)
    pre_close = []
    for ev_idx in idx_per_event:
        pre_close.append(spy_close.iloc[ev_idx] if 0 <= ev_idx < len(spy_close) else np.nan)
    out["spy_close_event"] = pre_close

    for h in HORIZONS:
        future_idx = idx_per_event + h
        forward_close = [spy_close.iloc[i] if 0 <= i < len(spy_close) else np.nan
                         for i in future_idx]
        out[f"spy_close_t{h}"] = forward_close
        out[f"ret_t{h}_pct"] = (np.array(forward_close) / np.array(pre_close) - 1) * 100

    return out


def add_regime_context(events: pd.DataFrame, macro_join: pd.DataFrame) -> pd.DataFrame:
    """Tag each event with pre-action regime state."""
    macro = (macro_join[macro_join["ticker"] == "SPY"][
        ["date", "DFF", "DGS10", "T10Y2Y", "VIXCLS"]]
        .drop_duplicates(subset=["date"])
        .sort_values("date")
        .set_index("date"))

    # 180-day trailing DFF context — defines rate-trend at time of action
    macro["DFF_180d_prior"] = macro["DFF"].shift(180)
    macro["DFF_delta_180d"] = macro["DFF"] - macro["DFF_180d_prior"]

    # SPY 30-day trailing return — defines pre-event SPY context
    spy = (macro_join[macro_join["ticker"] == "SPY"][["date", "close"]]
           .drop_duplicates(subset=["date"])
           .sort_values("date").set_index("date"))
    macro["spy_close"] = spy["close"]
    macro["spy_30d_prior"] = macro["spy_close"].shift(30)
    macro["spy_30d_return"] = (macro["spy_close"] / macro["spy_30d_prior"] - 1) * 100

    # SPY 252d-trailing high (proxy for distance-from-ATH)
    macro["spy_252d_high"] = macro["spy_close"].rolling(252, min_periods=20).max()
    macro["spy_pct_from_high"] = (macro["spy_close"] / macro["spy_252d_high"] - 1) * 100

    out = events.copy()
    spy_dates = macro.index
    idx_per_event = spy_dates.searchsorted(out["meeting_date"].values, side="left")

    # snap to event-day (or next trading day if on a holiday)
    def at(col):
        return [macro[col].iloc[i] if 0 <= i < len(macro) else np.nan
                for i in idx_per_event]

    out["dff_at_event"]          = at("DFF")
    out["dgs10_at_event"]        = at("DGS10")
    out["t10y2y_at_event"]       = at("T10Y2Y")
    out["vix_at_event"]          = at("VIXCLS")
    out["dff_delta_180d"]        = at("DFF_delta_180d")
    out["spy_30d_return"]        = at("spy_30d_return")
    out["spy_pct_from_high"]     = at("spy_pct_from_high")

    # Classify rate trend at time of action
    def rate_trend_at(delta):
        if pd.isna(delta):
            return "unknown"
        if delta > 0.5:   return "hiking"
        if delta < -0.5:  return "cutting"
        return "holding"
    out["rate_trend_pre"] = out["dff_delta_180d"].apply(rate_trend_at)

    # "Pivot" flag — first action of opposite direction after at least 180d of
    # the opposite trend
    out["is_pivot"] = (
        ((out["action"] == "cut")  & (out["rate_trend_pre"] == "hiking")) |
        ((out["action"] == "hike") & (out["rate_trend_pre"] == "cutting"))
    )

    # SPY-in-drawdown flag at time of action (more than -5% from 252d high)
    out["spy_in_drawdown"] = out["spy_pct_from_high"] < -5.0

    return out


def report(df: pd.DataFrame) -> None:
    print("\n" + "=" * 72)
    print("FED ACTION → SPY FORWARD RETURN STUDY (v1, 2026-05-28)")
    print("=" * 72)
    print(f"\nN events: {len(df)}  (range: {df['meeting_date'].min().date()} → {df['meeting_date'].max().date()})")
    print(f"By action: {df['action'].value_counts().to_dict()}")

    # --- crude aggregation by action ---
    print("\n── Crude aggregation by action ──")
    for action in ["cut", "hike"]:
        sub = df[df["action"] == action]
        if sub.empty:
            continue
        print(f"\n  Action = {action}  (N={len(sub)})")
        print(f"    horizon  mean%    median%   stdev%   pct_pos   N")
        for h in HORIZONS:
            col = f"ret_t{h}_pct"
            r = sub[col].dropna()
            if r.empty:
                continue
            print(f"    T+{h:<3d}    {r.mean():+6.2f}   {r.median():+6.2f}   "
                  f"{r.std():5.2f}    {(r > 0).mean()*100:5.1f}%   {len(r)}")

    # --- refined: pivot vs continuation ---
    print("\n── Pivot (first opposite-direction action) vs continuation ──")
    for action in ["cut", "hike"]:
        sub = df[df["action"] == action]
        if sub.empty:
            continue
        for label, mask in [("PIVOT", sub["is_pivot"]), ("continuation", ~sub["is_pivot"])]:
            s = sub[mask]
            if s.empty:
                continue
            print(f"\n  {action.upper():4s} × {label:13s}  N={len(s)}")
            for h in HORIZONS:
                r = s[f"ret_t{h}_pct"].dropna()
                if r.empty: continue
                print(f"    T+{h:<3d}  mean {r.mean():+6.2f}%  median {r.median():+6.2f}%  "
                      f"pct_pos {(r > 0).mean()*100:5.1f}%  N={len(r)}")

    # --- refined: SPY drawdown context at action ---
    print("\n── Action × SPY drawdown context at meeting ──")
    for action in ["cut", "hike"]:
        sub = df[df["action"] == action]
        for label, mask in [("SPY in drawdown >5%", sub["spy_in_drawdown"]),
                            ("SPY near highs",      ~sub["spy_in_drawdown"])]:
            s = sub[mask]
            if s.empty:
                continue
            print(f"\n  {action.upper():4s} × {label:22s}  N={len(s)}")
            for h in HORIZONS:
                r = s[f"ret_t{h}_pct"].dropna()
                if r.empty: continue
                print(f"    T+{h:<3d}  mean {r.mean():+6.2f}%  median {r.median():+6.2f}%  "
                      f"pct_pos {(r > 0).mean()*100:5.1f}%  N={len(r)}")

    # --- refined: VIX regime at meeting ---
    print("\n── Action × VIX regime at meeting ──")
    df["vix_bucket"] = pd.cut(df["vix_at_event"],
                              bins=[0, 15, 25, 100],
                              labels=["calm_<15", "elev_15-25", "stressed_>25"])
    for action in ["cut", "hike"]:
        sub = df[df["action"] == action]
        for bucket in ["calm_<15", "elev_15-25", "stressed_>25"]:
            s = sub[sub["vix_bucket"] == bucket]
            if len(s) < 2:
                continue
            print(f"\n  {action.upper():4s} × VIX {bucket:14s}  N={len(s)}")
            for h in HORIZONS:
                r = s[f"ret_t{h}_pct"].dropna()
                if r.empty: continue
                print(f"    T+{h:<3d}  mean {r.mean():+6.2f}%  median {r.median():+6.2f}%  "
                      f"pct_pos {(r > 0).mean()*100:5.1f}%  N={len(r)}")


def main():
    print("Loading FOMC events from ChromaDB...")
    events = load_fomc_events()
    print(f"  loaded {len(events)} events")

    print("Loading SPY close history...")
    prices = pd.read_parquet(PRICES_PATH)
    spy = (prices[prices["ticker"] == "SPY"][["date", "close"]]
           .drop_duplicates(subset=["date"])
           .sort_values("date")
           .set_index("date"))
    print(f"  SPY close: n={len(spy)} from {spy.index.min().date()} → {spy.index.max().date()}")

    print("Computing forward returns...")
    events = compute_forward_returns(events, spy["close"])

    print("Adding regime context...")
    macro_join = pd.read_parquet(MACRO_PATH)
    events = add_regime_context(events, macro_join)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    events.to_parquet(OUT_PATH, index=False, compression="snappy")
    print(f"\nWrote {len(events)} rows × {len(events.columns)} cols → {OUT_PATH}")

    report(events)


if __name__ == "__main__":
    main()
