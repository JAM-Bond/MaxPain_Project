"""ZEBRA long-put overlay strike rule — regime-conditional.

Validated 2026-05-14 (Phase 1 + Phase 2 backtests on tier-1 cohort):

  Stage 1-2 (bull, all cascade rings green) → 10% OTM put (cheap insurance)
  Stage 3 OR any cascade ring yellow         → ATM put (regime-robust)
  Stage 4-5 / bear gate open + SPY actively
    deepening (new 60d low in last 30d)      → 5% ITM put (deepening lift)
  Stage 4-5 / bear gate open + no recent
    new 60d low (trough / unwinding)         → 10% OTM put (recovery)

All overlays share the parent ZEBRA's expiration and are held to OpEx.

Reads from regime_state + regime_health_snapshots in the live DB.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import date, timedelta

import pandas as pd

DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"

# Lookback constants (calendar days). The 60-day low + 30-day recency
# match the rule in TRADING_PLAN.rtf ZEBRA section.
DEEPENING_LOW_WINDOW = 60     # rolling-min window
DEEPENING_RECENCY = 30        # "new low in past N days"
HISTORY_DAYS = 90             # spy_close history fetch depth

RING_FAMILIES = ("ai_ring", "qqq_ring", "spy_ring")


def _latest_regime_row(conn: sqlite3.Connection) -> dict | None:
    cur = conn.execute(
        "SELECT * FROM regime_state ORDER BY snapshot_date DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def _latest_ring_status(conn: sqlite3.Connection) -> dict[str, str | None]:
    """Latest component_status per family from regime_health_snapshots."""
    rings = {}
    for family in RING_FAMILIES:
        row = conn.execute(
            "SELECT component_status FROM regime_health_snapshots "
            "WHERE family = ? ORDER BY snapshot_date DESC LIMIT 1",
            (family,),
        ).fetchone()
        rings[family] = row[0] if row else None
    return rings


def _spy_active_deepening(conn: sqlite3.Connection) -> tuple[bool, str]:
    """True if SPY made a new DEEPENING_LOW_WINDOW-day low in the last
    DEEPENING_RECENCY calendar days. Returns (flag, human_readable_explanation)."""
    df = pd.read_sql_query(
        "SELECT snapshot_date, spy_close FROM regime_state "
        f"ORDER BY snapshot_date DESC LIMIT {HISTORY_DAYS}",
        conn,
    )
    if df.empty:
        return False, "no SPY history"
    df = df.sort_values("snapshot_date").reset_index(drop=True)
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    df["rolling_low"] = df["spy_close"].rolling(
        DEEPENING_LOW_WINDOW, min_periods=10
    ).min()
    df["is_new_low"] = df["spy_close"] <= (df["rolling_low"] + 0.005)

    today = df["snapshot_date"].max()
    cutoff = today - timedelta(days=DEEPENING_RECENCY)
    recent = df[df["snapshot_date"] >= cutoff]
    if recent.empty:
        return False, "no recent history"

    new_low_rows = recent[recent["is_new_low"]]
    if new_low_rows.empty:
        last_low = df[df["is_new_low"]]["snapshot_date"].max() if df["is_new_low"].any() else None
        if last_low is None:
            return False, f"no 60d low ever (history N={len(df)})"
        return False, f"last new 60d low {last_low} ({(today - last_low).days}d ago, threshold {DEEPENING_RECENCY}d)"

    most_recent_new_low = new_low_rows["snapshot_date"].max()
    days_ago = (today - most_recent_new_low).days
    return True, f"new 60d low on {most_recent_new_low} ({days_ago}d ago, threshold {DEEPENING_RECENCY}d)"


def regime_overlay_rule(conn: sqlite3.Connection | None = None) -> dict:
    """Determine which long-put overlay strike to recommend at ZEBRA entry.

    Returns:
        {
          'rule_label': str,              # short label for the card header
          'strike_pct_offset': float,     # signed % offset from spot, applied as
                                          # target_strike = spot * (1 + offset).
                                          # +0.05 means 5% above spot (ITM put).
                                          # −0.10 means 10% below spot (OTM put).
                                          # NOTE: this is signed in the
                                          # spot-direction convention, NOT the
                                          # backtest's strike_pct_below_spot.
          'tolerance_pct': float,         # max allowed % deviation when
                                          # selecting the nearest tradeable strike
          'regime_summary': str,          # one-line human summary
          'rationale': list[str],         # multi-line breakdown of decision
          'stage': int | None,
          'rings': dict,
          'active_deepening': bool,
          'deepening_detail': str,
        }
    """
    own_conn = False
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH))
        own_conn = True
    try:
        state = _latest_regime_row(conn)
        rings = _latest_ring_status(conn)
        active_deep, deep_detail = _spy_active_deepening(conn)
    finally:
        if own_conn:
            conn.close()

    stage = state.get("stage") if state else None
    h1_active = state.get("h1_active", 0) if state else 0
    below_200dma = state.get("below_200dma", 0) if state else 0

    n_red = sum(1 for v in rings.values() if v == "🔴")
    n_yellow = sum(1 for v in rings.values() if v == "🟡")
    any_yellow = n_yellow >= 1 or n_red >= 1
    bear_gate_open = (stage is not None and stage >= 4) or n_red >= 2 \
        or (h1_active == 1 and below_200dma == 1)

    rationale = [
        f"regime_state stage={stage}; h1_active={h1_active}; below_200dma={below_200dma}",
        f"cascade rings: AI={rings['ai_ring']} QQQ={rings['qqq_ring']} SPY={rings['spy_ring']} "
        f"({n_red} red / {n_yellow} yellow)",
        f"SPY active-deepening: {active_deep} — {deep_detail}",
    ]

    if bear_gate_open and active_deep:
        rule = {
            "rule_label": "ITM5 (5% in-the-money)",
            "strike_pct_offset": +0.05,
            "tolerance_pct": 0.02,
            "regime_summary": "BEAR GATE OPEN + active deepening",
        }
    elif bear_gate_open:
        rule = {
            "rule_label": "OTM10 (10% out-of-the-money) — troughing",
            "strike_pct_offset": -0.10,
            "tolerance_pct": 0.025,
            "regime_summary": "BEAR GATE OPEN but no recent new 60d low — troughing/recovery",
        }
    elif (stage is not None and stage == 3) or any_yellow:
        rule = {
            "rule_label": "ATM (at-the-money)",
            "strike_pct_offset": 0.00,
            "tolerance_pct": 0.015,
            "regime_summary": "FRAGILE — Stage 3 or any cascade ring yellow",
        }
    else:
        rule = {
            "rule_label": "OTM10-15 (10–15% out-of-the-money)",
            "strike_pct_offset": -0.10,
            "tolerance_pct": 0.05,  # allow up to 15% OTM if 10% unavailable
            "regime_summary": "BULL / CALM — Stage 1-2, cascade rings green",
        }

    rule.update({
        "rationale": rationale,
        "stage": stage,
        "rings": rings,
        "active_deepening": active_deep,
        "deepening_detail": deep_detail,
    })
    return rule


if __name__ == "__main__":
    import json
    r = regime_overlay_rule()
    print("=== ZEBRA long-put overlay rule (live) ===")
    print(f"Rule:            {r['rule_label']}")
    print(f"Strike offset:   {r['strike_pct_offset']:+.0%} (target = spot × {1 + r['strike_pct_offset']:.2f})")
    print(f"Regime summary:  {r['regime_summary']}")
    print("Rationale:")
    for line in r["rationale"]:
        print(f"  - {line}")
