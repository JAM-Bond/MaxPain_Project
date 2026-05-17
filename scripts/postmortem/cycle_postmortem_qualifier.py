#!/usr/bin/env python3.11
"""
Cycle post-mortem — qualifier verdict outcomes + signal accuracy.

Usage:
  cycle_postmortem_qualifier.py                 # all closed trades
  cycle_postmortem_qualifier.py --opex 2026-05-15

v2 (2026-04-30) adds three sections measuring signal forward-test accuracy:
  - SIGNAL ACCURACY SCORECARD (per-signal lift vs backtest expected)
  - DIRECTIONAL OUTCOME vs PREDICTION (SPY realized vs pre-reg)
  - REGIME SIGNAL FLIPS (cycle-window scoped)

Pre-registration: docs/SIGNAL_VALIDATION_PREREG.md (sealed 2026-04-26).
"""
import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))

from lib.db import DB_PATH  # noqa: E402

SPY_PARQUET = Path.home() / "MaxPain_Project/data/orats/by_ticker/SPY.parquet"

# ─── Pre-registered backtest expectations (per pre-reg doc) ──────────────────
# Per-share lifts converted to per-contract ($0.01/share = $1.00/contract for
# standard 100-mult equity options). Sourced from the named memory entries.

SIGNAL_BACKTEST_EXPECTATIONS = {
    "bull_put_signal_active": {
        "structure_match": ("bull_put",),
        "expected_lift_per_contract":  2.10,   # +$0.021/share
        "expected_on_per_contract":    1.90,
        "expected_off_per_contract":  -0.20,
        "source": "project_mp_phase2f_rescue.md",
        "claim":  "contango + VRP>0 lifts bull_put cohort mean",
    },
    "h1_active": {
        "structure_match": ("bear_call",),
        "expected_lift_per_contract": 17.90,   # +$0.179/share (0.092 vs -0.087)
        "expected_on_per_contract":    9.20,
        "expected_off_per_contract":  -8.70,
        "source": "project_bear_call_h1_h3_findings.md",
        "claim":  "SPY<200dma + IVR>0.5 lifts bear_call cohort mean",
    },
    "if_gate_active": {
        "structure_match": ("inverted_fly",),
        "expected_lift_per_contract": 44.80,   # 0.639 vs 0.191 = +0.448
        "expected_on_per_contract":   63.90,
        "expected_off_per_contract":  19.10,
        "source": "project_if_phase_a_batch_findings.md",
        "claim":  "term_inverted lifts inverted_fly cohort mean (3.3× lift)",
    },
}


# ─── SPY daily signals (used to backfill regime state for trades placed
#     before the regime_state DB capture began on 2026-04-25) ────────────────

_SPY_DAILY_CACHE = None


def load_spy_daily_signals():
    """Build a daily SPY signals DataFrame from ORATS history.

    Returns df indexed by trade_date with raw values + binary signal columns.
    Cached after first call. Returns None if SPY parquet missing.
    """
    global _SPY_DAILY_CACHE
    if _SPY_DAILY_CACHE is not None:
        return _SPY_DAILY_CACHE
    if not SPY_PARQUET.exists():
        return None
    import numpy as np
    import pandas as pd

    spy = pd.read_parquet(SPY_PARQUET, columns=["trade_date", "expirDate",
                                                  "stkPx", "delta", "cMidIv"])
    spy["trade_date"] = pd.to_datetime(spy["trade_date"])
    spy["exp_dt"] = pd.to_datetime(spy["expirDate"], format="%m/%d/%Y",
                                    errors="coerce")
    spy["dte"] = (spy["exp_dt"] - spy["trade_date"]).dt.days
    spy["delta_dist"] = (spy["delta"] - 0.50).abs()

    front = spy[(spy["dte"] >= 25) & (spy["dte"] <= 35)].sort_values(
        ["trade_date", "delta_dist"]).drop_duplicates("trade_date")
    back = spy[(spy["dte"] >= 65) & (spy["dte"] <= 85)].sort_values(
        ["trade_date", "delta_dist"]).drop_duplicates("trade_date")

    daily = front.set_index("trade_date")[["stkPx", "cMidIv"]].copy()
    daily.columns = ["close", "atm_iv30"]
    daily["atm_iv75"] = back.set_index("trade_date")["cMidIv"]
    daily = daily.sort_index()

    daily["ma200"] = daily["close"].rolling(200, min_periods=100).mean()
    rmin = daily["atm_iv30"].rolling(252, min_periods=120).min()
    rmax = daily["atm_iv30"].rolling(252, min_periods=120).max()
    daily["ivr_252"] = ((daily["atm_iv30"] - rmin)
                       / (rmax - rmin).replace(0, np.nan))
    daily["term_spread"] = daily["atm_iv30"] - daily["atm_iv75"]
    daily["log_ret"] = np.log(daily["close"] / daily["close"].shift(1))
    daily["rv20"] = daily["log_ret"].rolling(20).std() * np.sqrt(252)
    daily["vrp"] = daily["atm_iv30"] - daily["rv20"]

    daily["below_200dma"] = (daily["close"] < daily["ma200"]).astype("Int64")
    daily["ivr_high"] = (daily["ivr_252"] > 0.5).astype("Int64")
    daily["term_inverted"] = (daily["term_spread"] > 0).astype("Int64")
    daily["h1_active"] = ((daily["below_200dma"] == 1)
                         & (daily["ivr_high"] == 1)).astype("Int64")
    daily["hard_pause_active"] = ((daily["below_200dma"] == 1)
                                 & (daily["term_inverted"] == 1)
                                 & (daily["ivr_high"] == 1)).astype("Int64")
    daily["bull_put_signal_active"] = ((daily["term_spread"] < 0)
                                      & (daily["vrp"] > 0)).astype("Int64")
    daily["if_gate_active"] = daily["term_inverted"]

    _SPY_DAILY_CACHE = daily
    return daily


def get_regime_at(spy_daily, date_str):
    """Look up SPY signal row for a date string. Returns the most recent
    row at-or-before date_str (handles weekends/holidays). None if missing."""
    if spy_daily is None or not date_str:
        return None
    import pandas as pd
    try:
        d = pd.to_datetime(date_str)
        valid = spy_daily.index[spy_daily.index <= d]
        if len(valid) == 0:
            return None
        return spy_daily.loc[valid[-1]]
    except Exception:
        return None


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


def report_signal_flips(cur, opex=None):
    """Date-by-date table of regime_state flips. If opex is given, scope to
    the cycle window [min(entry_date), opex] for placed=1 trades on that
    cycle. Otherwise scope to the paper-test window: from
    min(entry_date) of any placed=1 closed trade through today.

    The full regime_state table goes back to 2013-07 (ORATS-backfilled
    history); printing all of that in cumulative mode is noise. The
    paper-test window is the meaningful scope for forward-test review.
    """
    cycle_window = None
    if opex:
        cycle_window = cur.execute(
            """
            SELECT MIN(entry_date) AS first_entry
            FROM spread_score_trades
            WHERE placed = 1 AND opex_date = ?
            """,
            (opex,),
        ).fetchone()
        if cycle_window and cycle_window["first_entry"]:
            section_header(
                f"REGIME SIGNAL FLIPS (cycle window "
                f"{cycle_window['first_entry']} → {opex})"
            )
        else:
            section_header("REGIME SIGNAL FLIPS (no cycle scope; paper-test window)")
            cycle_window = None
    else:
        # Cumulative mode — scope to the paper-test window (min entry of any
        # placed=1 closed trade through today) instead of the full
        # ORATS-backfilled history.
        first = cur.execute(
            """
            SELECT MIN(entry_date) AS first_entry
            FROM spread_score_trades
            WHERE placed = 1 AND status = 'closed'
            """
        ).fetchone()
        if first and first["first_entry"]:
            cycle_window = first
            section_header(
                f"REGIME SIGNAL FLIPS (paper-test window "
                f"{first['first_entry']} → today)"
            )
        else:
            section_header("REGIME SIGNAL FLIPS (no closed paper-test trades yet)")

    if cycle_window:
        end_date = opex if opex else date.today().isoformat()
        rows = cur.execute(
            """
            SELECT snapshot_date, stage, h1_active, if_gate_active,
                   bull_put_signal_active, hard_pause_active,
                   soft_downsize_active, below_200dma, term_inverted
            FROM regime_state
            WHERE snapshot_date BETWEEN ? AND ?
            ORDER BY snapshot_date
            """,
            (cycle_window["first_entry"], end_date),
        ).fetchall()
    else:
        rows = []

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


def report_signal_accuracy_scorecard(cur, opex):
    """Per-signal forward-test scorecard against pre-registered backtest lift.

    For each binary signal in SIGNAL_BACKTEST_EXPECTATIONS:
      - filter trades to relevant structure (e.g. h1_active vs bear_call only)
      - join to regime_state at entry_date; backfill from SPY ORATS for dates
        before regime_state DB capture began (2026-04-25)
      - compute mean P/L, win rate, total per ON/OFF state
      - compare realized lift to backtest-expected lift
      - tag VALIDATED / FALSIFIED / NULL per pre-registration discipline
    """
    section_header("SIGNAL ACCURACY SCORECARD (forward-test vs pre-reg)")

    where = "AND t.opex_date = ?" if opex else ""
    params = (opex,) if opex else ()
    rows = cur.execute(
        f"""
        SELECT t.id, t.symbol, t.spread_type, t.entry_date, t.final_pnl,
               r.bull_put_signal_active, r.h1_active, r.if_gate_active,
               r.term_inverted, r.below_200dma, r.stage
        FROM spread_score_trades t
        LEFT JOIN regime_state r ON r.snapshot_date = t.entry_date
        WHERE t.status = 'closed' AND t.placed = 1 {where}
        """,
        params,
    ).fetchall()

    if not rows:
        print("(no closed placed=1 trades in scope)")
        return

    spy_daily = load_spy_daily_signals()
    if spy_daily is None:
        print("⚠ SPY ORATS parquet not found — cannot backfill regime state.")
        print("  Trades placed before 2026-04-25 will show signal state = unknown.")

    # Backfill missing signal columns from SPY daily where regime_state is null
    backfill_count = 0
    trades = []
    for r in rows:
        d = dict(r)
        if d.get("bull_put_signal_active") is None and spy_daily is not None:
            sig = get_regime_at(spy_daily, d["entry_date"])
            if sig is not None:
                d["bull_put_signal_active"] = (
                    int(sig["bull_put_signal_active"])
                    if sig["bull_put_signal_active"] is not None else None)
                d["h1_active"] = (int(sig["h1_active"])
                                  if sig["h1_active"] is not None else None)
                d["if_gate_active"] = (int(sig["if_gate_active"])
                                       if sig["if_gate_active"] is not None else None)
                d["term_inverted"] = (int(sig["term_inverted"])
                                      if sig["term_inverted"] is not None else None)
                d["below_200dma"] = (int(sig["below_200dma"])
                                     if sig["below_200dma"] is not None else None)
                backfill_count += 1
        trades.append(d)

    print(f"  Trades in scope: {len(trades)}  "
          f"(backfilled signal state from SPY ORATS: {backfill_count})")

    def stats(group):
        pnls = [t["final_pnl"] for t in group if t["final_pnl"] is not None]
        if not pnls:
            return None
        wins = sum(1 for p in pnls if p > 0)
        return {
            "n": len(pnls),
            "mean": sum(pnls) / len(pnls),
            "win_rate": wins / len(pnls),
            "total": sum(pnls),
        }

    for signal, exp in SIGNAL_BACKTEST_EXPECTATIONS.items():
        print()
        print(f"  ── {signal} ───────────────────────────────")
        print(f"    Claim:  {exp['claim']}")
        print(f"    Source: {exp['source']}")

        relevant = [t for t in trades if any(
            sm in (t.get("spread_type") or "") for sm in exp["structure_match"]
        )]
        on_grp = [t for t in relevant if t.get(signal) == 1]
        off_grp = [t for t in relevant if t.get(signal) == 0]
        unknown = len(relevant) - len(on_grp) - len(off_grp)

        on_s = stats(on_grp)
        off_s = stats(off_grp)

        n_on = on_s["n"] if on_s else 0
        n_off = off_s["n"] if off_s else 0
        print(f"    Relevant trades: {len(relevant)}  "
              f"(ON={n_on}, OFF={n_off}, unknown_state={unknown})")
        if on_s:
            print(f"    ON:  mean=${on_s['mean']:>+8.2f}  "
                  f"win={on_s['win_rate']*100:>4.0f}%  "
                  f"total=${on_s['total']:>+9.2f}")
        if off_s:
            print(f"    OFF: mean=${off_s['mean']:>+8.2f}  "
                  f"win={off_s['win_rate']*100:>4.0f}%  "
                  f"total=${off_s['total']:>+9.2f}")

        if n_on >= 3 and n_off >= 3:
            realized_lift = on_s["mean"] - off_s["mean"]
            expected_lift = exp["expected_lift_per_contract"]
            print(f"    Realized lift (ON-OFF): ${realized_lift:>+.2f}/contract")
            print(f"    Backtest expected lift: ${expected_lift:>+.2f}/contract")
            if realized_lift > 0 and expected_lift > 0:
                verdict = "VALIDATED (directional match)"
            elif realized_lift < -1 and expected_lift > 0:
                verdict = "FALSIFIED (sign-flip vs backtest)"
            else:
                verdict = "NULL (lift too small to call)"
            print(f"    Forward-test verdict: {verdict}")
        else:
            print(f"    Forward-test verdict: NULL (need N≥3 each cell, "
                  f"have ON={n_on}, OFF={n_off})")

    # Stage composite — group all trades by entry stage
    print()
    print("  ── stage (composite 0-3) ────────────────────")
    print(f"    Claim:  stage=0 calm/bull, stage=3 H1 bear regime")
    by_stage = {}
    for t in trades:
        stg = t.get("stage")
        if stg is None and spy_daily is not None:
            # Stage isn't in our SPY daily df, leave as unknown
            stg = "unknown"
        by_stage.setdefault(stg, []).append(t)
    for stg in sorted(by_stage.keys(),
                      key=lambda x: (-1 if x == "unknown" else x)):
        s = stats(by_stage[stg])
        if s:
            print(f"    stage={stg!s:>7}: N={s['n']:>3}  "
                  f"mean=${s['mean']:>+8.2f}  "
                  f"win={s['win_rate']*100:>4.0f}%  "
                  f"total=${s['total']:>+9.2f}")


def report_directional_outcome(cur, opex):
    """Compare actual SPY move vs pre-registered prediction at cycle entry."""
    section_header("DIRECTIONAL OUTCOME vs PREDICTION")

    where = "AND opex_date = ?" if opex else ""
    params = (opex,) if opex else ()
    cycle = cur.execute(
        f"""
        SELECT MIN(entry_date) AS first_entry, MAX(entry_date) AS last_entry
        FROM spread_score_trades
        WHERE placed = 1 {where}
        """,
        params,
    ).fetchone()

    if not cycle or not cycle["first_entry"]:
        print("(no placed=1 trades for cycle)")
        return

    spy_daily = load_spy_daily_signals()
    if spy_daily is None:
        print("(SPY ORATS unavailable — cannot compute directional outcome)")
        return

    first = cycle["first_entry"]
    end_anchor = opex if opex else cycle["last_entry"]

    sig_start = get_regime_at(spy_daily, first)
    sig_end = get_regime_at(spy_daily, end_anchor)
    if sig_start is None or sig_end is None:
        print(f"(SPY signals not available for {first} or {end_anchor})")
        return

    # Detect whether end_anchor is in the future (cycle still open) — the
    # lookup will return the most recent at-or-before date in either case.
    import pandas as pd
    end_used = sig_end.name.date() if hasattr(sig_end.name, "date") else sig_end.name
    requested_end = pd.to_datetime(end_anchor).date()
    cycle_in_progress = end_used < requested_end

    spy_start = float(sig_start["close"])
    spy_end = float(sig_end["close"])
    pct_change = (spy_end - spy_start) / spy_start * 100

    print(f"  Cycle entry window first day: {first}")
    print(f"  Cycle end anchor:             {end_anchor}"
          f"{' (cycle in progress; using ' + str(end_used) + ')' if cycle_in_progress else ''}")
    print(f"  SPY at entry:        ${spy_start:>9,.2f}")
    print(f"  SPY at end:          ${spy_end:>9,.2f}")
    print(f"  Realized SPY return: {pct_change:>+7.2f}%  "
          f"(|move| = {abs(pct_change):.2f}%)")
    print()

    # Pre-reg prediction 1: term_inverted at entry → 20% prob ≥10% in 45d
    term_inv = bool(sig_start.get("term_inverted") == 1)
    print(f"  Term spread at entry: {'INVERTED' if term_inv else 'contango'}")
    if term_inv:
        print(f"  Pre-reg claim: 20% probability of ≥10% SPY move in 45d "
              f"(2.6× baseline 7.5%)")
        if cycle_in_progress:
            print(f"  Verdict: PROVISIONAL ({abs(pct_change):.2f}% so far; "
                  f"single cycle = 1/N test of probability)")
        elif abs(pct_change) >= 10:
            print(f"  Verdict: VALIDATED (≥10% move occurred)")
        elif abs(pct_change) >= 5:
            print(f"  Verdict: PARTIAL ({abs(pct_change):.2f}% move; "
                  f"didn't hit 10% threshold but moved meaningfully)")
        else:
            print(f"  Verdict: NULL (no ≥5% move; single cycle = 1/N)")
    else:
        print(f"  Pre-reg claim: contango at entry → quieter forward window")
        if abs(pct_change) >= 10:
            print(f"  Verdict: FALSIFIED ({abs(pct_change):.2f}% move under contango)")
        else:
            print(f"  Verdict: consistent with claim")

    # Pre-reg prediction 2: stage 0 at entry → no major drawdown
    below = bool(sig_start.get("below_200dma") == 1)
    print()
    print(f"  SPY < 200dma at entry: {'YES' if below else 'no'} "
          f"(stage 0 expected if no)")
    if not below:
        if pct_change <= -5:
            print(f"  ⚠ Stage-0 entry but realized {pct_change:+.2f}% "
                  f"— calm-regime assumption breaking down")
        else:
            print(f"  Realized {pct_change:+.2f}% — consistent with calm/bull entry")


def main(opex):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("Cycle Post-Mortem (Qualifier + Signals)")
    print(f"Scope: {'opex ' + opex if opex else 'all closed trades'}")

    report_verdict_outcomes(cur, opex)
    report_counterfactual_skipped(cur, opex)
    report_signal_state_attribution(cur, opex)
    report_signal_accuracy_scorecard(cur, opex)
    report_directional_outcome(cur, opex)
    report_signal_flips(cur, opex)

    conn.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--opex", help="OpEx date (YYYY-MM-DD) to scope, or omit for all")
    args = p.parse_args()
    sys.exit(main(args.opex))
