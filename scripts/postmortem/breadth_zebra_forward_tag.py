#!/usr/bin/env python3.11
"""Forward-tag monitor for the sealed ZEBRA breadth-sizing gate (Gate C).

docs/BREADTH_RING_ZEBRA_SIZING_PREREG.md §5–6. During the paper window the gate is
OFF (tag-don't-downsize). This report tags each closed paper ZEBRA cycle by its
ENTRY-DAY breadth-ring state and checks the forward outcomes do NOT contradict the
in-sample tail penalty (🔴 entries should not OUTPERFORM / show a tighter tail).

It is a NON-CONTRADICTION guardrail, not a powered test: forward 🔴-zebra N is
expected to be tiny (🔴 ≈ 16% of days; zebra entries are infrequent), so a thin or
empty result is the expected state and is reported as such — never dressed up as
confirmation. Read-only.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from lib.db import DB_PATH  # noqa: E402


def load() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    trades = pd.read_sql(
        """SELECT symbol, spread_type, entry_date, final_pnl, placed, status
           FROM spread_score_trades
           WHERE LOWER(spread_type) LIKE 'zebra%'""",
        conn, parse_dates=["entry_date"])
    ring = pd.read_sql("SELECT asof, top_warning FROM breadth_ring_daily",
                       conn, parse_dates=["asof"])
    conn.close()
    if trades.empty or ring.empty:
        return trades.assign(top_warning=pd.NA) if not trades.empty else trades
    trades = trades.sort_values("entry_date")
    ring = ring.sort_values("asof")
    t = pd.merge_asof(trades, ring, left_on="entry_date", right_on="asof",
                      direction="backward")
    t["is_red"] = t["top_warning"] == 1
    return t


def report(t: pd.DataFrame, closed_only: bool = True) -> None:
    print("=" * 72)
    print("ZEBRA breadth-sizing — FORWARD-TAG monitor (Gate C non-contradiction)")
    print("=" * 72)
    if t.empty:
        print("  No ZEBRA trades on record yet — nothing to tag (expected during paper).")
        return
    scope = t[t["status"].str.lower().eq("closed")] if closed_only else t
    print(f"  zebra trades: {len(t)} total ({int((t['placed'] == 1).sum())} placed) | "
          f"closed: {len(scope)}")
    if scope.empty:
        print("  No CLOSED zebra cycles yet — Gate C pending (expected during paper).")
        return
    red, nonred = scope[scope.is_red], scope[~scope.is_red]
    def line(s, lbl):
        if len(s) == 0:
            print(f"  {lbl:10} n=0"); return
        print(f"  {lbl:10} n={len(s):3} | mean P&L={s.final_pnl.mean():+.2f} | "
              f"worst={s.final_pnl.min():+.2f} | win={100*(s.final_pnl>0).mean():.0f}%")
    line(red, "🔴 entry"); line(nonred, "non-🔴")
    print("\n  Non-contradiction read:")
    if len(red) == 0:
        print("    No 🔴-entry zebra cycles yet → Gate C 'not contradicted' by default "
              "(promotion would rest on the backtest gates A+B; this stays a monitor).")
    elif len(red) < 3:
        print(f"    Only {len(red)} 🔴-entry cycle(s) → too thin to confirm OR contradict; "
              "monitor continues. Not treated as confirmation.")
    else:
        contradicts = red.final_pnl.mean() > nonred.final_pnl.mean()
        print(f"    🔴 mean {red.final_pnl.mean():+.2f} vs non-🔴 {nonred.final_pnl.mean():+.2f} "
              f"→ {'CONTRADICTS the penalty (🔴 outperformed) — flag for review' if contradicts else 'consistent with the in-sample penalty (🔴 not better)'}.")


def main() -> int:
    report(load())
    return 0


if __name__ == "__main__":
    sys.exit(main())
