#!/usr/bin/env python3.11
"""
Cycle post-mortem — qualifier verdict outcomes + signal accuracy.

Usage:
  cycle_postmortem_qualifier.py                 # all closed trades
  cycle_postmortem_qualifier.py --opex 2026-05-15
"""
import argparse
import sqlite3
import sys

DB_PATH = "/Users/josephmorris/Metal_Project/data/shared/metal_project.db"


def fmt_pct(x):
    return f"{x*100:5.1f}%" if x is not None else "  n/a"


def fmt_money(x):
    return f"${x:9.2f}" if x is not None else "    n/a  "


def section_header(title: str):
    bar = "=" * 78
    print(f"\n{bar}\n{title}\n{bar}")


def _verdict_query(cur, opex, placed_filter: str):
    where_opex = "AND t.opex_date = ?" if opex else ""
    params = (opex,) if opex else ()
    return cur.execute(
        f"""
        SELECT
            COALESCE(q.verdict, '(pre-qualifier)') AS verdict,
            COUNT(*) AS n,
            SUM(CASE WHEN t.final_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            AVG(t.final_pnl) AS mean_pnl,
            SUM(t.final_pnl) AS total_pnl,
            MIN(t.final_pnl) AS worst,
            MAX(t.final_pnl) AS best
        FROM spread_score_trades t
        LEFT JOIN cycle_qualifier_runs q
          ON q.run_date = t.qualifier_run_date
         AND q.symbol = t.symbol
         AND (q.structure = t.spread_type
              OR q.structure LIKE t.spread_type || '\\_%' ESCAPE '\\')
        WHERE t.status = 'closed' AND {placed_filter} {where_opex}
        GROUP BY verdict
        ORDER BY verdict
        """,
        params,
    ).fetchall()


def _print_verdict_table(rows, label):
    if not rows:
        print(f"  {label}: (none)")
        return
    print(f"  {label}:")
    print(f"  {'verdict':18} {'n':>4} {'wins':>5} {'win%':>6} "
          f"{'mean':>11} {'total':>11} {'worst':>11} {'best':>11}")
    for r in rows:
        wr = r["wins"] / r["n"] if r["n"] else 0
        print(f"  {r['verdict']:18} {r['n']:>4} {r['wins']:>5} {fmt_pct(wr)} "
              f"{fmt_money(r['mean_pnl'])} {fmt_money(r['total_pnl'])} "
              f"{fmt_money(r['worst'])} {fmt_money(r['best'])}")


def report_verdict_outcomes(cur, opex):
    section_header("VERDICT OUTCOMES (closed trades, split by placed)")
    placed_rows = _verdict_query(cur, opex, "t.placed = 1")
    algo_rows = _verdict_query(cur, opex, "t.placed = 0")
    _print_verdict_table(placed_rows, "ACTUAL BOOK (placed=1) — your trades")
    print()
    _print_verdict_table(algo_rows, "ALGO RECS (placed=0) — system-evaluation only")


def report_counterfactual_skipped(cur, opex):
    section_header("COUNTERFACTUAL: GO / DOWNSIZE verdicts not taken")
    where = "AND q.opex = ?" if opex else ""
    params = (opex,) if opex else ()

    rows = cur.execute(
        f"""
        SELECT q.run_date, q.symbol, q.structure, q.verdict, q.opex,
               q.size, q.reason
        FROM cycle_qualifier_runs q
        LEFT JOIN spread_score_trades t
          ON t.qualifier_run_date = q.run_date
         AND t.symbol = q.symbol
         AND (q.structure = t.spread_type
              OR q.structure LIKE t.spread_type || '\\_%' ESCAPE '\\')
        WHERE q.verdict IN ('GO', 'DOWNSIZE')
          AND t.id IS NULL
          {where}
        ORDER BY q.run_date, q.symbol
        """,
        params,
    ).fetchall()

    if not rows:
        print("(none — every GO/DOWNSIZE verdict in scope was acted on)")
        return

    print(f"{'date':12} {'symbol':8} {'structure':22} "
          f"{'verdict':10} {'opex':12} {'size':>5}  reason")
    for r in rows:
        size = f"{r['size']:.2f}" if r["size"] is not None else "  -- "
        print(f"{r['run_date']:12} {r['symbol']:8} {r['structure']:22} "
              f"{r['verdict']:10} {r['opex'] or '':12} {size:>5}  "
              f"{r['reason'] or ''}")


def report_signal_state_attribution(cur, opex):
    section_header("PER-TRADE SIGNAL-STATE ATTRIBUTION (closed trades)")
    where = "AND t.opex_date = ?" if opex else ""
    params = (opex,) if opex else ()

    rows = cur.execute(
        f"""
        SELECT
            t.spread_type,
            r.bull_put_signal_active AS bp_sig,
            r.h1_active AS bc_sig,
            r.if_gate_active AS if_sig,
            r.stage AS stage,
            COUNT(*) AS n,
            SUM(CASE WHEN t.final_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            AVG(t.final_pnl) AS mean_pnl
        FROM spread_score_trades t
        LEFT JOIN regime_state r ON r.snapshot_date = t.entry_date
        WHERE t.status = 'closed' {where}
        GROUP BY t.spread_type, bp_sig, bc_sig, if_sig, stage
        ORDER BY t.spread_type, stage
        """,
        params,
    ).fetchall()

    if not rows:
        print("(no closed trades have a regime_state row at entry_date)")
        print("  expected for trades opened before 2026-04-25 — regime_state")
        print("  capture began on that date.")
        return

    print(f"{'spread':12} {'bp':>3} {'bc':>3} {'if':>3} {'stg':>4} "
          f"{'n':>4} {'wins':>5} {'win%':>6} {'mean':>11}")
    for r in rows:
        wr = r["wins"] / r["n"] if r["n"] else 0
        print(f"{r['spread_type']:12} "
              f"{r['bp_sig'] if r['bp_sig'] is not None else '-':>3} "
              f"{r['bc_sig'] if r['bc_sig'] is not None else '-':>3} "
              f"{r['if_sig'] if r['if_sig'] is not None else '-':>3} "
              f"{r['stage'] if r['stage'] is not None else '-':>4} "
              f"{r['n']:>4} {r['wins']:>5} {fmt_pct(wr)} "
              f"{fmt_money(r['mean_pnl'])}")


def report_signal_flips(cur):
    section_header("REGIME SIGNAL FLIPS (live history, all dates)")
    rows = cur.execute(
        """
        SELECT snapshot_date, stage, h1_active, if_gate_active,
               bull_put_signal_active, hard_pause_active,
               soft_downsize_active, below_200dma, term_inverted
        FROM regime_state
        ORDER BY snapshot_date
        """
    ).fetchall()

    if not rows:
        print("(no regime_state rows yet)")
        return

    if len(rows) == 1:
        r = rows[0]
        print(f"Only 1 day of regime_state ({r['snapshot_date']}) — need >=5 "
              "days before flips become measurable.")
        print(f"  current: stage={r['stage']} h1={r['h1_active']} "
              f"if_gate={r['if_gate_active']} bp_signal={r['bull_put_signal_active']} "
              f"pause={r['hard_pause_active']} downsize={r['soft_downsize_active']} "
              f"<200dma={r['below_200dma']} term_inv={r['term_inverted']}")
        return

    flip_cols = [
        "stage", "h1_active", "if_gate_active",
        "bull_put_signal_active", "hard_pause_active",
        "soft_downsize_active", "below_200dma", "term_inverted",
    ]
    flip_count = 0
    prev = None
    for r in rows:
        if prev is None:
            prev = r
            continue
        flips = [c for c in flip_cols if r[c] != prev[c]]
        if flips:
            flip_count += 1
            changes = ", ".join(f"{c}: {prev[c]}->{r[c]}" for c in flips)
            print(f"{r['snapshot_date']}: {changes}")
        prev = r
    if flip_count == 0:
        print(f"(no flips across {len(rows)} days of regime_state)")


def main(opex):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("Cycle Post-Mortem (Qualifier + Signals)")
    print(f"Scope: {'opex ' + opex if opex else 'all closed trades'}")

    report_verdict_outcomes(cur, opex)
    report_counterfactual_skipped(cur, opex)
    report_signal_state_attribution(cur, opex)
    report_signal_flips(cur)

    conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--opex", help="OpEx date (YYYY-MM-DD) to scope, or omit for all")
    args = p.parse_args()
    sys.exit(main(args.opex))
