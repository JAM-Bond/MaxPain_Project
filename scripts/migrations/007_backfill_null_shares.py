#!/usr/bin/env python3.11
"""Migration 007 — backfill shares=1 on legacy NULL-shares rows.

Follow-on to 006 (final_pnl -> TOTAL). 74 early-era rows (entered before the
`shares` column existed) have shares=NULL, which every consumer silently treats
as 1 (`shares or 1`). Making it explicit removes the ambiguity that motivated the
target_hit_pnl concern: with shares=1 recorded, both final_pnl and the dormant
target_hit_pnl are unambiguously TOTAL (= the per-contract value at 1 contract),
and any future per-contract*shares logic is correct.

Verified safe before writing: every NULL-shares closed credit spread has
final_pnl == (entry_credit - exit_credit)*100 to within fees (the only apparent
exception, AXP id 62 at 796.74 vs 800.0 gross, is a 1-contract net-of-fees figure,
not a multi-contract total). All non-1-contract structures (stock=50, butterflies,
long_puts, zebra) already carry explicit shares and are untouched.

Idempotent: re-run finds 0 NULL rows. DB backed up before running.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402


def main(apply: bool) -> None:
    c = sqlite3.connect(DB_PATH)
    n_null = c.execute("SELECT COUNT(*) FROM spread_score_trades WHERE shares IS NULL").fetchone()[0]
    # Guard: refuse if any NULL-shares row looks like a multi-contract total
    # (final_pnl materially exceeds its 1-contract credit-spread value).
    suspect = c.execute("""
        SELECT id, symbol, final_pnl, (entry_credit - exit_credit)*100 AS unit
        FROM spread_score_trades
        WHERE shares IS NULL AND status='closed' AND final_pnl IS NOT NULL
          AND exit_credit IS NOT NULL
          AND (spread_type LIKE 'bull_put%' OR spread_type LIKE 'bear_call%'
               OR spread_type LIKE 'inverted_fly%')
          AND ABS(final_pnl - (entry_credit - exit_credit)*100) > 10
    """).fetchall()
    print(f"NULL-shares rows: {n_null}")
    if suspect:
        print("  ⚠ suspect (final_pnl far from 1ct value — NOT backfilling, review):")
        for s in suspect:
            print(f"    id {s[0]} {s[1]}: final_pnl={s[2]} 1ct_unit={s[3]:.1f}")
        print("  Aborting — resolve suspects first.")
        c.close()
        return
    if apply:
        cur = c.execute("UPDATE spread_score_trades SET shares=1 WHERE shares IS NULL")
        c.commit()
        print(f"APPLIED: set shares=1 on {cur.rowcount} rows")
    else:
        print(f"DRY-RUN: would set shares=1 on {n_null} rows")
    c.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
