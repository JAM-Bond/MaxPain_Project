"""
Cross-trade analytics on the enriched ledger.

Each query returns a DataFrame with N + adequacy_flag per cell, per
project_trade_ledger_learning.md's eight-query specification. The eight
queries answer:

  1. exit_type_breakdown — which exit rules are firing? (discipline audit)
  2. per_name_x_structure  — which names actually carry the cohort?
  3. qualifier_vs_off_script — does qualifier discipline pay off?
  4. structure_x_regime    — win rate of bull_put in contango+VRP>0 vs other
  5. mae_vs_final           — drawdown vs P/L (would I have held in live?)
  6. regime_transition       — trades open during regime stage transitions
  7. sizing_audit            — were positions sized per the plan?
  8. earnings_overlap        — earnings event during hold helps or hurts?

Adequacy thresholds (project convention):
  N <  10 → PRELIMINARY    (directional only — never override backtest)
  N <  20 → SUGGESTIVE
  N <  30 → DEVELOPING
  N >= 30 → ADEQUATE

CLI usage:
  python3.11 -m lib.trade_analytics            # all queries
  python3.11 -m lib.trade_analytics --query exit_type
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"

from lib.trade_ledger import load_trade_ledger, adequacy_flag  # noqa: E402


# ── Aggregation helpers ────────────────────────────────────────────────────

def _agg(group: pd.DataFrame) -> pd.Series:
    n = int(group["final_pnl"].notna().sum())
    if n == 0:
        return pd.Series({
            "n": 0, "mean_pnl": np.nan, "median_pnl": np.nan,
            "win_rate": np.nan, "total_pnl": np.nan, "worst": np.nan,
            "best": np.nan, "adequacy": "PRELIMINARY",
        })
    pnl = group["final_pnl"].dropna()
    return pd.Series({
        "n": n,
        "mean_pnl": float(pnl.mean()),
        "median_pnl": float(pnl.median()),
        "win_rate": float((pnl > 0).mean()),
        "total_pnl": float(pnl.sum()),
        "worst": float(pnl.min()),
        "best": float(pnl.max()),
        "adequacy": adequacy_flag(n),
    })


def _closed_only(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["status"] == "closed"].copy()


def _placed_closed(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["status"] == "closed") & (df["placed"] == 1)].copy()


# ── Query 1: exit-type breakdown ────────────────────────────────────────────

def exit_type_breakdown(df: pd.DataFrame) -> pd.DataFrame:
    sub = _placed_closed(df)
    if sub.empty:
        return pd.DataFrame()
    out = sub.groupby("exit_type", dropna=False).apply(
        _agg, include_groups=False
    )
    return out.reset_index().sort_values("n", ascending=False)


# ── Query 2: per-name × structure carrier table ────────────────────────────

def per_name_x_structure(df: pd.DataFrame) -> pd.DataFrame:
    sub = _placed_closed(df)
    if sub.empty:
        return pd.DataFrame()
    out = sub.groupby(["symbol", "structure"], dropna=False).apply(
        _agg, include_groups=False
    )
    return out.reset_index().sort_values(
        ["structure", "total_pnl"], ascending=[True, False]
    )


# ── Query 3: qualifier-confirmed vs off-script ─────────────────────────────

def qualifier_vs_off_script(df: pd.DataFrame) -> pd.DataFrame:
    sub = _placed_closed(df)
    if sub.empty:
        return pd.DataFrame()
    sub = sub.copy()

    def _bucket(r):
        if r["off_script"] == 1:
            return "off_script"
        v = r["qualifier_verdict"]
        if v in ("GO", "DOWNSIZE"):
            return f"qualifier_{v}"
        if v in ("SKIP", "PAUSE", "PENDING", "NOT_IN_COHORT"):
            return f"qualifier_{v}_overridden"
        return "unmatched"

    sub["track"] = sub.apply(_bucket, axis=1)
    out = sub.groupby("track", dropna=False).apply(_agg, include_groups=False)
    return out.reset_index().sort_values("n", ascending=False)


# ── Query 4: structure × regime ─────────────────────────────────────────────

def _regime_label(row: pd.Series) -> str:
    """Compact regime descriptor at entry. NaN → 'unknown'."""
    if pd.isna(row.get("entry_stage")):
        return "unknown"
    parts = [f"stage{int(row['entry_stage'])}"]
    if row.get("entry_h1_active") == 1:
        parts.append("H1")
    if row.get("entry_bull_put_signal_active") == 1:
        parts.append("BPsig")
    if row.get("entry_if_gate_active") == 1:
        parts.append("IFgate")
    if row.get("entry_hard_pause_active") == 1:
        parts.append("PAUSE")
    return "+".join(parts)


def structure_x_regime(df: pd.DataFrame) -> pd.DataFrame:
    sub = _placed_closed(df)
    if sub.empty:
        return pd.DataFrame()
    sub = sub.copy()
    sub["regime_at_entry"] = sub.apply(_regime_label, axis=1)
    out = sub.groupby(["structure", "regime_at_entry"], dropna=False).apply(
        _agg, include_groups=False
    )
    return out.reset_index().sort_values(
        ["structure", "n"], ascending=[True, False]
    )


# ── Query 5: MAE vs final P/L ───────────────────────────────────────────────

def mae_vs_final(df: pd.DataFrame) -> pd.DataFrame:
    """Per-trade table with MAE alongside final P/L. Surfaces 'would I have
    held this in live?' candidates — large MAE + winning trades are exactly
    the ones that test paper→live discipline."""
    sub = _placed_closed(df)
    sub = sub[sub["mae"].notna()].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["mae_recovered"] = sub["final_pnl"] - sub["mae"]
    return sub[[
        "trade_id", "symbol", "structure", "entry_date", "exit_date",
        "final_pnl", "mae", "mae_recovered", "exit_type",
    ]].sort_values("mae", ascending=True)


# ── Query 6: regime-transition trades ──────────────────────────────────────

def regime_transition(df: pd.DataFrame) -> pd.DataFrame:
    """Group trades by whether the regime stage changed during the hold."""
    sub = _placed_closed(df)
    if sub.empty:
        return pd.DataFrame()
    sub = sub.copy()
    sub["transition"] = np.where(
        sub["regime_transitioned"] == 1, "transitioned", "stable"
    )
    out = sub.groupby("transition", dropna=False).apply(
        _agg, include_groups=False
    )
    return out.reset_index()


# ── Query 7: sizing audit ───────────────────────────────────────────────────

def sizing_audit(df: pd.DataFrame) -> pd.DataFrame:
    """Compare qualifier-prescribed size vs actual contracts. Mismatch is a
    process-discipline error, not a strategy error. Only meaningful for
    trades with a qualifier match (off_script trades have no prescription)."""
    sub = _placed_closed(df)
    sub = sub[sub["qualifier_size"].notna()].copy()
    if sub.empty:
        return pd.DataFrame()
    sub["size_match"] = np.where(
        sub["qualifier_size"].fillna(1.0).round(2)
        == sub["shares"].fillna(0).clip(lower=1).map(lambda x: 1.0).round(2),
        "match", "drift",
    )
    return sub[[
        "trade_id", "symbol", "structure",
        "qualifier_verdict", "qualifier_size", "shares", "size_match",
    ]]


# ── Query 8: earnings overlap (placeholder until earnings cache wired) ─────

def earnings_overlap(df: pd.DataFrame, conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Trades open during an earnings event for the underlying.

    Joins against the earnings_calendar_cache parquet if available. When the
    cache isn't present, returns an empty frame with a note column.
    """
    sub = _placed_closed(df)
    if sub.empty:
        return pd.DataFrame()
    cache = ROOT / "data/profile/earnings_calendar_cache.parquet"
    if not cache.exists():
        return pd.DataFrame({"note": ["earnings_calendar_cache.parquet not"
                                        " present — wire via "
                                        "scripts/pipeline/refresh_earnings_calendar.py"]})
    cal = pd.read_parquet(cache)
    cal["earnings_date"] = pd.to_datetime(cal["earnings_date"]).dt.normalize()
    sub = sub.copy()
    sub["entry_date"] = pd.to_datetime(sub["entry_date"]).dt.normalize()
    sub["exit_date"] = pd.to_datetime(sub["exit_date"]).dt.normalize()

    def _has_earnings(row):
        e = cal[(cal["ticker"] == row["symbol"])
                & (cal["earnings_date"] >= row["entry_date"])
                & (cal["earnings_date"] <= row["exit_date"])]
        return not e.empty

    sub["earnings_during_hold"] = sub.apply(_has_earnings, axis=1)
    out = sub.groupby("earnings_during_hold", dropna=False).apply(
        _agg, include_groups=False
    )
    return out.reset_index()


# ── Pretty-print runner ─────────────────────────────────────────────────────

ALL_QUERIES = {
    "exit_type": exit_type_breakdown,
    "per_name_x_structure": per_name_x_structure,
    "qualifier_vs_off_script": qualifier_vs_off_script,
    "structure_x_regime": structure_x_regime,
    "mae_vs_final": mae_vs_final,
    "regime_transition": regime_transition,
    "sizing_audit": sizing_audit,
    "earnings_overlap": earnings_overlap,
}


def _print_section(title: str, frame: pd.DataFrame) -> None:
    print()
    print("═" * 78)
    print(f"  {title}")
    print("═" * 78)
    if frame is None or frame.empty:
        print("  (no data)")
        return
    pd.set_option("display.max_columns", 100)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 200)
    # Format dollar columns
    fmt = frame.copy()
    for c in ("mean_pnl", "median_pnl", "total_pnl", "worst", "best",
              "final_pnl", "mae", "mae_recovered"):
        if c in fmt.columns:
            fmt[c] = fmt[c].apply(
                lambda v: f"${v:+,.2f}" if pd.notna(v) else ""
            )
    if "win_rate" in fmt.columns:
        fmt["win_rate"] = fmt["win_rate"].apply(
            lambda v: f"{v*100:.0f}%" if pd.notna(v) else ""
        )
    print(fmt.to_string(index=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", choices=list(ALL_QUERIES) + ["all"],
                    default="all")
    args = p.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    df = load_trade_ledger(conn)

    n_total = len(df)
    n_closed = (df["status"] == "closed").sum()
    n_placed_closed = ((df["status"] == "closed") & (df["placed"] == 1)).sum()
    print()
    print(f"Trade ledger: {n_total} total | {n_closed} closed | "
          f"{n_placed_closed} placed+closed (analytics base)")
    print(f"Adequacy of full closed-placed cohort: "
          f"{adequacy_flag(int(n_placed_closed))}")

    if args.query == "all":
        _print_section("EXIT-TYPE BREAKDOWN", exit_type_breakdown(df))
        _print_section("PER-NAME × STRUCTURE", per_name_x_structure(df))
        _print_section("QUALIFIER vs OFF-SCRIPT", qualifier_vs_off_script(df))
        _print_section("STRUCTURE × REGIME (at entry)", structure_x_regime(df))
        _print_section("MAE vs FINAL P/L (per trade — sorted by worst MAE)",
                        mae_vs_final(df))
        _print_section("REGIME TRANSITION DURING HOLD", regime_transition(df))
        _print_section("SIZING AUDIT", sizing_audit(df))
        _print_section("EARNINGS OVERLAP DURING HOLD", earnings_overlap(df, conn))
    else:
        fn = ALL_QUERIES[args.query]
        result = fn(df, conn) if args.query == "earnings_overlap" else fn(df)
        _print_section(args.query.upper(), result)

    conn.close()


if __name__ == "__main__":
    main()
