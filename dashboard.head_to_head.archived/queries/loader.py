"""Read-only data loaders for the head-to-head dashboard.

Opens ~/Metal_Project/data/shared/metal_project.db via SQLite's read-only URI
during the bake-off. Writes raise at the driver level.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

METAL_DB = Path.home() / "Metal_Project" / "data" / "shared" / "metal_project.db"
SQL_DIR = Path(__file__).parent


def _connect_readonly() -> sqlite3.Connection:
    uri = f"file:{METAL_DB}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _load_sql(filename: str) -> pd.DataFrame:
    sql = (SQL_DIR / filename).read_text()
    with _connect_readonly() as conn:
        return pd.read_sql_query(sql, conn)


def _format_strikes(short: float, long: float) -> str:
    if pd.isna(short) or pd.isna(long):
        return ""
    return f"-{short:g}/{long:g}"


def load_original_book() -> pd.DataFrame:
    """Per-leg closed trades from spread_cycle_summary."""
    df = _load_sql("original_book.sql")
    if not df.empty:
        df.insert(
            df.columns.get_loc("short_strike"),
            "strikes",
            [_format_strikes(s, l) for s, l in zip(df["short_strike"], df["long_strike"])],
        )
    return df


def load_score_book() -> pd.DataFrame:
    """Per-trade rows from spread_score_trades (open + closed), with latest MTM."""
    df = _load_sql("score_book.sql")
    if df.empty:
        return df

    df.insert(
        df.columns.get_loc("short_strike"),
        "strikes",
        [_format_strikes(s, l) for s, l in zip(df["short_strike"], df["long_strike"])],
    )

    # Unified P&L: realized if closed, else latest mark-to-market.
    pnl = df["final_pnl"].where(df["final_pnl"].notna(), df["mtm_pnl"])
    df["pnl"] = pnl
    df["pnl_is_live"] = df["final_pnl"].isna() & df["mtm_pnl"].notna()
    return df


def load_comparison_summary() -> pd.DataFrame:
    """Trimmed head-to-head summary per (symbol, opex_date)."""
    return _load_sql("comparison_summary.sql")


def load_comparison() -> pd.DataFrame:
    """Full-detail head-to-head join per (symbol, opex_date). Kept for drill-down."""
    return _load_sql("comparison.sql")
