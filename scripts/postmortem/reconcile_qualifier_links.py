#!/usr/bin/env python3.11
"""
Stamp qualifier_run_date on spread_score_trades rows by joining
to cycle_qualifier_runs on (entry_date, symbol, structure root).

Runs nightly after the 9:25 ET qualifier and any same-day trade entries.
Only updates rows where qualifier_run_date IS NULL.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))

from lib.db import DB_PATH, connect  # noqa: E402


def main(dry_run: bool) -> int:
    conn = connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT id, symbol, spread_type, entry_date
        FROM spread_score_trades
        WHERE qualifier_run_date IS NULL
        """
    ).fetchall()

    stamped = 0
    no_match = 0
    matches = []
    for r in rows:
        match = cur.execute(
            """
            SELECT run_date, structure, verdict
            FROM cycle_qualifier_runs
            WHERE run_date = ? AND symbol = ?
              AND (structure = ? OR structure LIKE ? || '\\_%' ESCAPE '\\')
            ORDER BY
                CASE WHEN structure = ? THEN 0 ELSE 1 END,
                CASE verdict
                    WHEN 'GO' THEN 0
                    WHEN 'DOWNSIZE' THEN 1
                    WHEN 'PENDING' THEN 2
                    WHEN 'SKIP' THEN 3
                    WHEN 'PAUSE' THEN 4
                    ELSE 5
                END
            LIMIT 1
            """,
            (r["entry_date"], r["symbol"], r["spread_type"],
             r["spread_type"], r["spread_type"]),
        ).fetchone()

        if match is None:
            no_match += 1
            continue

        matches.append((r["id"], r["symbol"], r["spread_type"],
                        r["entry_date"], match["structure"], match["verdict"]))
        if not dry_run:
            cur.execute(
                "UPDATE spread_score_trades SET qualifier_run_date = ? WHERE id = ?",
                (match["run_date"], r["id"]),
            )
        stamped += 1

    if not dry_run:
        conn.commit()

    mode = "dry-run" if dry_run else "committed"
    print(f"reconcile_qualifier_links: examined={len(rows)} stamped={stamped} "
          f"no_match={no_match} ({mode})")
    if matches:
        print("  stamped rows:")
        for tid, sym, spt, ed, struct, verdict in matches:
            print(f"    id={tid} {sym} {spt} entry={ed} -> "
                  f"structure={struct} verdict={verdict}")
    conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Preview matches without writing")
    args = p.parse_args()
    sys.exit(main(dry_run=args.dry_run))
