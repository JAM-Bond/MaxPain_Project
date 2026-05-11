"""Query helpers for the Daily Alert page."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def list_runs() -> pd.DataFrame:
    """All archived alert runs, newest first. Used for the date selector
    and the calendar overview."""
    with _conn() as c:
        try:
            df = pd.read_sql_query("""
                SELECT run_date, run_timestamp, subject, severity,
                       n_constructions, has_events,
                       length(text_body) AS text_len
                FROM daily_alert_runs
                ORDER BY run_date DESC
            """, c)
        except Exception:
            return pd.DataFrame()
    return df


def get_run(run_date: str) -> dict | None:
    """Full row including text_body + html_body for the given run_date.
    None if not found."""
    with _conn() as c:
        r = c.execute("""
            SELECT * FROM daily_alert_runs WHERE run_date = ?
        """, (run_date,)).fetchone()
    if r is None:
        return None
    return dict(r)
