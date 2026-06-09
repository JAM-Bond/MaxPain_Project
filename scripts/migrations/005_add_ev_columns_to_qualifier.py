"""Migration 005 — add + populate EV-rank columns on cycle_qualifier_runs.

Adds ev_per_risk, ev_pop, ev_credit, ev_max_loss, ev_rank_position (spec step A)
and enriches the most recent run with ZEBRA candidates as an initial population.
Idempotent + re-runnable. Ongoing population is the 16:35 ET ev_enrich cron.

Usage: python3.11 scripts/migrations/005_add_ev_columns_to_qualifier.py [run_date]
"""
import sys
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import connect                              # noqa: E402
from lib.ev_enrich import ensure_ev_columns, enrich_run  # noqa: E402


def main():
    run_date = sys.argv[1] if len(sys.argv) > 1 else None
    conn = connect()
    try:
        ensure_ev_columns(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(cycle_qualifier_runs)")
                if r[1].startswith("ev_")]
        print(f"✓ EV columns present: {cols}")
        if run_date is None:
            run_date = conn.execute(
                "SELECT MAX(run_date) FROM cycle_qualifier_runs "
                "WHERE verdict IN ('GO','DOWNSIZE')").fetchone()[0]
        rd, ok, fail = enrich_run(conn, run_date)
        print(f"✓ initial enrich — run {rd}: {ok} scored, {fail} fail-open")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
