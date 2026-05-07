"""Read-only DB accessors for the Landing page.

All queries hit ~/Metal_Project/data/shared/metal_project.db via SQLite.
Live close-side marks come from scripts/monitor/close_helper.py.
Cascade state comes from scripts/monitor/regime_health.py via the
regime_state table.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"
ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def may_opex_running_pnl(opex: str = "2026-05-15") -> tuple[float, int]:
    """Sum of final_pnl for closed placed=1 trades on the given OpEx."""
    with _conn() as c:
        row = c.execute("""
            SELECT COALESCE(SUM(final_pnl), 0.0) AS total, COUNT(*) AS n
            FROM spread_score_trades
            WHERE opex_date = ? AND placed = 1 AND status = 'closed'
              AND final_pnl IS NOT NULL
        """, (opex,)).fetchone()
    return float(row["total"] or 0.0), int(row["n"] or 0)


def open_positions_count() -> int:
    with _conn() as c:
        row = c.execute("""
            SELECT COUNT(*) AS n FROM spread_score_trades
            WHERE placed = 1 AND status = 'open'
        """).fetchone()
    return int(row["n"] or 0)


def todays_actionable() -> list[dict]:
    """Latest qualifier run's GO/DOWNSIZE rows with days_until <= 1."""
    with _conn() as c:
        latest = c.execute("SELECT MAX(run_date) AS d FROM cycle_qualifier_runs").fetchone()
        if not latest or not latest["d"]:
            return []
        rows = c.execute("""
            SELECT symbol, structure, target, opex, days_until, verdict, reason
            FROM cycle_qualifier_runs
            WHERE run_date = ? AND verdict IN ('GO','DOWNSIZE') AND days_until <= 1
            ORDER BY structure, symbol
        """, (latest["d"],)).fetchall()
    return [dict(r) for r in rows]


def latest_qualifier_run() -> str | None:
    with _conn() as c:
        row = c.execute("SELECT MAX(run_date) AS d FROM cycle_qualifier_runs").fetchone()
    return row["d"] if row and row["d"] else None


def cascade_state() -> dict | None:
    """Latest cascade composite from regime_health_composites table.

    Returns dict with {snapshot_date, ai_state, qqq_state, spy_state,
    composite, ai_label, qqq_label, spy_label} or None.
    """
    with _conn() as c:
        try:
            latest = c.execute(
                "SELECT MAX(snapshot_date) AS d FROM regime_health_composites"
            ).fetchone()
            if not latest or not latest["d"]:
                return None
            rows = c.execute("""
                SELECT family, composite_status, composite_label, n_yellow, n_red
                FROM regime_health_composites
                WHERE snapshot_date = ?
            """, (latest["d"],)).fetchall()
        except sqlite3.Error:
            return None
    if not rows:
        return None
    by_family = {r["family"]: dict(r) for r in rows}
    # normalize emoji to GREEN/YELLOW/RED for tone mapping
    def _norm(emoji: str | None) -> str:
        if not emoji:
            return "—"
        return {"🟢": "GREEN", "🟡": "YELLOW", "🔴": "RED"}.get(emoji.strip(), emoji.strip())
    ai = by_family.get("ai_ring", {})
    qqq = by_family.get("qqq_ring", {})
    spy = by_family.get("spy_ring", {})
    statuses = [_norm(d.get("composite_status")) for d in (ai, qqq, spy)]
    composite = "RED" if "RED" in statuses else ("YELLOW" if "YELLOW" in statuses else
                ("GREEN" if statuses and all(s == "GREEN" for s in statuses) else "—"))
    return {
        "snapshot_date": latest["d"],
        "ai_state": _norm(ai.get("composite_status")),
        "qqq_state": _norm(qqq.get("composite_status")),
        "spy_state": _norm(spy.get("composite_status")),
        "ai_detail": ai.get("composite_label", ""),
        "qqq_detail": qqq.get("composite_label", ""),
        "spy_detail": spy.get("composite_label", ""),
        "composite": composite,
    }


def next_opex(today: date | None = None) -> date:
    """Return the third Friday of this month if it's today or later, else next month's."""
    today = today or date.today()
    candidate = _third_friday(today.year, today.month)
    if candidate >= today:
        return candidate
    nm_year = today.year + (1 if today.month == 12 else 0)
    nm_month = 1 if today.month == 12 else today.month + 1
    return _third_friday(nm_year, nm_month)


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # Friday is weekday 4. First Friday:
    offset = (4 - d.weekday()) % 7
    first_friday = d + timedelta(days=offset)
    return first_friday + timedelta(days=14)


def days_to_next_opex(today: date | None = None) -> int:
    today = today or date.today()
    return (next_opex(today) - today).days


def open_book_close_marks() -> dict:
    """Invoke close_helper.build_close_block() and return a digest:
       {ok, total_mid_pnl, total_natural_pnl, total_limit_pnl, rows, errors}
    """
    try:
        from scripts.monitor.close_helper import build_close_block
        result = build_close_block()
    except Exception as e:
        return {"ok": False, "error": str(e),
                "total_mid_pnl": 0, "total_natural_pnl": 0, "total_limit_pnl": 0,
                "rows": [], "errors": [str(e)]}
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "unknown"),
                "total_mid_pnl": 0, "total_natural_pnl": 0, "total_limit_pnl": 0,
                "rows": [], "errors": result.get("errors", [])}
    rows = result.get("rows", [])
    total_mid = sum(r.pnl_at_mid for r in rows)
    total_nat = sum(r.pnl_at_natural for r in rows)
    total_lim = sum(r.pnl_at_limit for r in rows)
    return {"ok": True, "total_mid_pnl": total_mid,
            "total_natural_pnl": total_nat, "total_limit_pnl": total_lim,
            "rows": rows, "errors": result.get("errors", [])}


def top_close_candidates(rows: list, n: int = 5) -> list:
    """Top N rows from close_helper output sorted by capture descending."""
    return sorted(rows, key=lambda r: r.capture_at_mid, reverse=True)[:n]
