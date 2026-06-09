#!/usr/bin/env python3.11
"""Migration 006 — normalize spread_score_trades.final_pnl to the TOTAL convention.

Historically final_pnl was written inconsistently: most rows stored PER-CONTRACT
dollar P/L, some stored TOTAL (per-contract x shares). Downstream consumers assume
TOTAL — landing.py / auto_promotion.py / postmortem do SUM(final_pnl); and
exit_timing_counterfactual computes held_pnl = (entry_credit - intrinsic) * 100 *
shares and subtracts final_pnl, so a per-contract final_pnl produced wrong deltas.
Canonical convention is therefore TOTAL.

This migration multiplies the identified per-contract rows by their share count so
SUM(final_pnl) and the counterfactual delta are correct. It is an EXPLICIT per-id
old->new map (not a formula) because butterfly rows carry inconsistent
entry/exit_credit sign conventions that defeat any generic (entry-exit)*100 rule
(e.g. DELL id161 / PDD id164 are already TOTAL but a formula would misread them).
Each UPDATE is guarded by the expected old value, so the migration is idempotent and
a no-op on re-run.

Rows left untouched (verified already TOTAL or single-contract): all shares=1 rows;
stock rows 130-140 ((exit-entry)*shares); long_put 155/156/158/183; zebra 101;
butterflies 161 (DELL) and 164 (PDD).

Backup taken before running: data/shared/backups/maxpain_pre_pnl_normalize_*.db
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402

# id -> (symbol, old_per_contract_pnl, shares, new_total_pnl)
CONVERSIONS = {
    142: ("SMH", 110.0, 2, 220.0),
    144: ("JPM", -85.0, 2, -170.0),
    145: ("SPY", 68.0, 2, 136.0),
    146: ("FSLR", 100.0, 4, 400.0),
    147: ("DAL", 59.0, 2, 118.0),
    148: ("WFC", -90.0, 2, -180.0),
    149: ("QQQ", 67.0, 2, 134.0),
    162: ("MU", 285.0, 2, 570.0),
    170: ("TOL", 85.0, 5, 425.0),
    173: ("GOOG", 85.0, 5, 425.0),
    174: ("KKR", 2.0, 5, 10.0),
    177: ("NEM", -85.0, 5, -425.0),
    178: ("AAPL", -130.0, 5, -650.0),
    179: ("MRK", 114.0, 5, 570.0),
    184: ("COF", -25.0, 10, -250.0),
    185: ("AMGN", -35.0, 5, -175.0),
    159: ("AFRM", 26.0, 4, 104.0),   # put_butterfly
    160: ("AG", 37.0, 5, 185.0),     # call_butterfly
}


def main(apply: bool) -> None:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    changed = total_delta = 0
    skipped = []
    for tid, (sym, old, sh, new) in CONVERSIONS.items():
        row = c.execute(
            "SELECT symbol, final_pnl, shares, status FROM spread_score_trades WHERE id=?",
            (tid,)).fetchone()
        if row is None:
            skipped.append(f"id {tid} ({sym}): not found")
            continue
        if row["symbol"] != sym:
            skipped.append(f"id {tid}: symbol mismatch (db={row['symbol']} expected {sym})")
            continue
        cur = None if row["final_pnl"] is None else round(float(row["final_pnl"]), 2)
        if cur == round(new, 2):
            skipped.append(f"id {tid} ({sym}): already total ({new:+.0f}) — no-op")
            continue
        if cur != round(old, 2):
            skipped.append(f"id {tid} ({sym}): UNEXPECTED current {cur} (expected old {old}) — SKIPPED")
            continue
        print(f"  id {tid:<4} {sym:<5} {old:+8.0f} (x{sh}) -> {new:+8.0f}")
        if apply:
            c.execute("UPDATE spread_score_trades SET final_pnl=? WHERE id=? AND final_pnl=?",
                      (new, tid, old))
        changed += 1
        total_delta += new - old
    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: {changed} rows, total delta {total_delta:+.0f}")
    if skipped:
        print("skipped/notes:")
        for s in skipped:
            print("  " + s)
    if apply:
        c.commit()
    c.close()


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
