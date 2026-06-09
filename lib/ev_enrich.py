"""EV-rank enrichment for cycle_qualifier_runs (spec step A — persistence).

Standalone pass that scores each GO/DOWNSIZE candidate via lib.trade_ev and writes
the EV columns back onto the run's rows, so:
  - the 16:45 daily alert can order its construction cards by reward/risk (step B), and
  - the post-mortem can later ask "did the EV-ranked keeps beat the downsized tail?"

Runs 16:35 ET (after reconcile@16:25, before the 16:45 alert) — at the close, chains
are complete, so coverage is good (the 9:25 thin-chain worry is a morning/cap-wiring
concern only). Idempotent + fail-open: a candidate whose chain/construction fails
just gets NULL EV (the alert then sorts it last, i.e. alphabetical fallback).

Persisted columns (ev_per_risk + ev_rank_position are the universal ones; pop/credit/
max_loss populate for verticals only, NULL for zebra/IF):
  ev_per_risk, ev_pop, ev_credit, ev_max_loss, ev_rank_position

Usage: python3.11 -m lib.ev_enrich [run_date]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH, connect          # noqa: E402
from lib.trade_ev import rank_candidates     # noqa: E402

EV_COLUMNS = {
    "ev_per_risk": "REAL", "ev_pop": "REAL", "ev_credit": "REAL",
    "ev_max_loss": "REAL", "ev_rank_position": "TEXT",
}


def ensure_ev_columns(conn) -> None:
    """Idempotently add the EV columns to cycle_qualifier_runs."""
    have = [r[1] for r in conn.execute("PRAGMA table_info(cycle_qualifier_runs)")]
    for col, typ in EV_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE cycle_qualifier_runs ADD COLUMN {col} {typ}")
    conn.commit()


def enrich_run(conn, run_date: str | None = None) -> tuple[str, int, int]:
    """Score the run's GO/DOWNSIZE candidates and persist EV columns.
    Returns (run_date, n_scored, n_failopen). Idempotent (re-runnable)."""
    ensure_ev_columns(conn)
    if run_date is None:
        run_date = conn.execute("SELECT MAX(run_date) FROM cycle_qualifier_runs").fetchone()[0]

    rows = conn.execute(
        "SELECT symbol, structure, opex, verdict FROM cycle_qualifier_runs "
        "WHERE run_date=? AND verdict IN ('GO','DOWNSIZE')", (run_date,)).fetchall()
    if not rows:
        return run_date, 0, 0

    candidates = [dict(symbol=s, structure=st, expiry=opex, verdict=v)
                  for s, st, opex, v in rows]
    scored = rank_candidates(candidates)   # adds 'ev' (EVScore) + 'ev_rank_position'

    n_ok = n_fail = 0
    for r in scored:
        ev = r["ev"]
        ok = (ev.error is None) and (ev.ev_per_risk is not None)
        n_ok += ok
        n_fail += (not ok)
        conn.execute(
            "UPDATE cycle_qualifier_runs SET ev_per_risk=?, ev_pop=?, ev_credit=?, "
            "ev_max_loss=?, ev_rank_position=? "
            "WHERE run_date=? AND symbol=? AND structure=? AND opex=?",
            (ev.ev_per_risk, ev.pop, ev.credit, ev.max_loss, r.get("ev_rank_position"),
             run_date, r["symbol"], r["structure"], r["expiry"]))
    conn.commit()
    return run_date, n_ok, n_fail


def main():
    run_date = sys.argv[1] if len(sys.argv) > 1 else None
    conn = connect()
    try:
        rd, ok, fail = enrich_run(conn, run_date)
        print(f"EV enrich — run {rd}: {ok} scored, {fail} fail-open (NULL EV)")
        # quick coverage echo
        if ok or fail:
            for structure, n, nev in conn.execute(
                    "SELECT structure, COUNT(*), "
                    "SUM(CASE WHEN ev_per_risk IS NOT NULL THEN 1 ELSE 0 END) "
                    "FROM cycle_qualifier_runs WHERE run_date=? AND verdict IN ('GO','DOWNSIZE') "
                    "GROUP BY structure ORDER BY structure", (rd,)):
                print(f"  {structure:<22} {nev}/{n} scored")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
