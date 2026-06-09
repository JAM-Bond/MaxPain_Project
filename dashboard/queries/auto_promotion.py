"""Query helpers for the Auto-Promotion page.

Reads the `cohort_changes` table (written nightly by
`scripts/maintenance/auto_promotion_nightly.py`) and joins applied PROMOTEs to
`spread_score_trades` to close the loop between cohort management and realized
trade outcomes.

Key semantics (verified 2026-06-09):
  - action ∈ {PROMOTE, DEMOTE, DEMOTE_DEFERRED}
  - applied=1 means the change was written to the live cohort; applied=0 means it
    was proposed but NOT applied (almost always because the nightly safety brake
    halted that structure — safety_halt_reason is set). Most rows are applied=0.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH  # noqa: E402

# cohort_changes.structure → spread_score_trades.spread_type prefix
_STRUCT_PREFIX = {
    "bull_put": "bull_put", "bear_call": "bear_call",
    "inverted_fly": "inverted_fly", "zebra": "zebra",
}


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def has_data() -> bool:
    with _conn() as c:
        try:
            return c.execute("SELECT COUNT(*) FROM cohort_changes").fetchone()[0] > 0
        except sqlite3.OperationalError:
            return False


def latest_run_date() -> str | None:
    with _conn() as c:
        r = c.execute("SELECT MAX(run_date) FROM cohort_changes").fetchone()
        return r[0] if r else None


def run_summary(run_date: str) -> dict:
    """Headline counts for one nightly run: applied promotes/demotes and how many
    proposals were halted (proposed but not applied)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT action, applied, COUNT(*) FROM cohort_changes "
            "WHERE run_date=? GROUP BY action, applied", (run_date,)).fetchall()
        halt = c.execute(
            "SELECT COUNT(*) FROM cohort_changes "
            "WHERE run_date=? AND safety_halt_reason IS NOT NULL", (run_date,)).fetchone()[0]
        halt_reasons = [r[0] for r in c.execute(
            "SELECT DISTINCT safety_halt_reason FROM cohort_changes "
            "WHERE run_date=? AND safety_halt_reason IS NOT NULL", (run_date,)).fetchall()]
    promoted = sum(n for a, ap, n in rows if a == "PROMOTE" and ap == 1)
    demoted = sum(n for a, ap, n in rows if a.startswith("DEMOTE") and ap == 1)
    proposed = sum(n for _, _, n in rows)
    return {
        "run_date": run_date, "promoted": promoted, "demoted": demoted,
        "proposed": proposed, "halted": halt, "halt_reasons": halt_reasons,
    }


def per_structure_breakdown(run_date: str) -> pd.DataFrame:
    """Structure × action counts for the run, split applied vs proposed."""
    with _conn() as c:
        df = pd.read_sql_query(
            "SELECT structure, action, "
            "SUM(applied) AS applied, COUNT(*) AS proposed "
            "FROM cohort_changes WHERE run_date=? "
            "GROUP BY structure, action ORDER BY structure, action",
            c, params=(run_date,))
    return df


def recent_changes(limit: int = 150) -> pd.DataFrame:
    with _conn() as c:
        df = pd.read_sql_query(
            "SELECT run_date, ticker, structure, action, applied, "
            "safety_halt_reason, reason "
            "FROM cohort_changes ORDER BY run_date DESC, structure, ticker "
            "LIMIT ?", c, params=(limit,))
    if not df.empty:
        df["applied"] = df["applied"].map({1: "✅ applied", 0: "⛔ halted/not applied"})
    return df


def cohort_net_change() -> pd.DataFrame:
    """Cumulative NET applied membership change per structure over time
    (PROMOTE +1, DEMOTE/DEMOTE_DEFERRED -1; applied rows only). Not absolute
    membership — the table has no starting roster — but shows churn direction."""
    with _conn() as c:
        df = pd.read_sql_query(
            "SELECT run_date, structure, action FROM cohort_changes "
            "WHERE applied=1 ORDER BY run_date", c)
    if df.empty:
        return df
    df["delta"] = df["action"].map(lambda a: 1 if a == "PROMOTE" else -1)
    pivot = (df.groupby(["run_date", "structure"])["delta"].sum()
               .unstack(fill_value=0).sort_index().cumsum())
    return pivot


def ticker_timeline(ticker: str) -> pd.DataFrame:
    with _conn() as c:
        df = pd.read_sql_query(
            "SELECT run_date, structure, action, applied, cohort_name, "
            "most_recent_mean, mean_threshold, most_recent_val_n, "
            "safety_halt_reason, reason "
            "FROM cohort_changes WHERE ticker=? ORDER BY run_date DESC, structure",
            c, params=(ticker.upper(),))
    if not df.empty:
        df["applied"] = df["applied"].map({1: "✅", 0: "⛔"})
    return df


def all_tickers() -> list[str]:
    with _conn() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT ticker FROM cohort_changes ORDER BY ticker").fetchall()]


def halted_log() -> pd.DataFrame:
    """One row per (run_date, structure, halt reason): how many proposals the
    safety brake blocked that night, plus a sample of the tickers."""
    with _conn() as c:
        df = pd.read_sql_query(
            "SELECT run_date, structure, safety_halt_reason, "
            "COUNT(*) AS n_blocked, GROUP_CONCAT(ticker, ', ') AS tickers "
            "FROM cohort_changes WHERE safety_halt_reason IS NOT NULL "
            "GROUP BY run_date, structure, safety_halt_reason "
            "ORDER BY run_date DESC, structure", c)
    return df


def promote_outcomes() -> pd.DataFrame:
    """For each APPLIED PROMOTE, did a trade follow? Left-joins spread_score_trades
    on ticker + matching spread_type prefix, entered on/after the promote date.
    Surfaces n trades placed, realized P/L on closed ones, and open count."""
    with _conn() as c:
        promos = pd.read_sql_query(
            "SELECT run_date, ticker, structure FROM cohort_changes "
            "WHERE action='PROMOTE' AND applied=1 ORDER BY run_date DESC", c)
        if promos.empty:
            return promos
        # one row per (ticker, structure) — keep the most recent promote (DESC → first)
        promos = promos.drop_duplicates(["ticker", "structure"], keep="first")
        rows = []
        for _, p in promos.iterrows():
            prefix = _STRUCT_PREFIX.get(p["structure"], p["structure"])
            tr = pd.read_sql_query(
                "SELECT status, final_pnl, placed FROM spread_score_trades "
                "WHERE symbol=? AND spread_type LIKE ? AND entry_date>=? "
                "AND spread_type!='stock'",
                c, params=(p["ticker"], prefix + "%", p["run_date"]))
            n_trades = len(tr)
            placed = int(tr["placed"].fillna(0).sum()) if n_trades else 0
            closed = tr[tr["status"].isin(["closed", "expired"])] if n_trades else tr
            realized = float(closed["final_pnl"].dropna().sum()) if len(closed) else None
            n_open = int((tr["status"] == "open").sum()) if n_trades else 0
            rows.append({
                "promoted": p["run_date"], "ticker": p["ticker"],
                "structure": p["structure"], "trades_after": n_trades,
                "placed": placed, "open": n_open,
                "realized_pnl": realized,
            })
    return pd.DataFrame(rows)
