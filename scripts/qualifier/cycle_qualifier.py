#!/usr/bin/env python3.11
"""
MaxPain cycle qualifier — daily GO/PENDING/SKIP grid for the deployable cohort
~/MaxPain_Project/scripts/qualifier/cycle_qualifier.py

Reads:
  - regime_state (populated by 9:20 ET research_cohort_snapshot.py)
  - research_cohort_v15.parquet (cohort + structure-membership flags)
  - earnings calendar (yfinance, cached daily)

Applies the gate logic from TRADING_PLAN.rtf v1.7 and emits per-(symbol,
structure) verdicts: GO, DOWNSIZE, PENDING, SKIP, PAUSE, NOT_IN_COHORT.

Design doc: project_cycle_qualifier_design.md memory.

Persistence:
  - cycle_qualifier_runs table in metal_project.db (one row per verdict)
  - parquet artifact at data/qualifier/qualifier_<run_date>.parquet
  - human-readable console output

Cadence: daily during entry windows; on-demand outside them.
First production runs:
  - 2026-05-05  (45-DTE for June OpEx 2026-06-19)  — IF window
  - 2026-05-08  (T-5 for May OpEx 2026-05-15)      — bull_put / bear_call
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.opex_calendar import (  # noqa: E402
    current_opex, next_n_opexes, trading_day_offset, trading_days_between,
    calendar_days_before,
)
from scripts.qualifier import gate_config as G  # noqa: E402
from scripts.qualifier.earnings_calendar import upcoming_earnings  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"
DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"
COHORT_PATH = ROOT / "data/profile/research_cohort_v15.parquet"
QUALIFIER_DIR = ROOT / "data/qualifier"


# ─── Regime state loader ──────────────────────────────────────────────

def load_regime_state(run_date: date) -> dict | None:
    """Most recent regime_state row on or before run_date. Returns dict or None."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT * FROM regime_state WHERE snapshot_date <= ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (str(run_date),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


# ─── Entry-window calculation ─────────────────────────────────────────

def compute_window_targets(run_date: date) -> dict:
    """For each entry window, return the next target entry day.

    Entry day = the date the qualifier would emit GO for that window's
    cohort, assuming all gates pass.

    Returns a dict keyed by window label, value = (target_date, target_opex,
    days_until).
    """
    out = {}
    # 5 OpEx lookahead so ZEBRA's 75-DTE entry (~2.5 cycles out) is always visible
    opexes = next_n_opexes(5, today=run_date)

    # ── Covered-call window: trading day AFTER prior monthly OpEx ──────
    # Logic: covered-call entry = first trading day after the OpEx that
    # opened the current monthly cycle, expiry = next monthly OpEx.
    # If today is past tolerance for the current cycle, look at the next.
    if opexes:
        from lib.opex_calendar import third_friday

        def _prior_opex(opex_d: date) -> date:
            yr = opex_d.year if opex_d.month > 1 else opex_d.year - 1
            mo = opex_d.month - 1 if opex_d.month > 1 else 12
            return third_friday(yr, mo)

        for opex in opexes[:3]:
            entry_day = trading_day_offset(_prior_opex(opex), 1)
            days_until = trading_days_between(run_date, entry_day)
            within_tolerance = days_until >= -G.WINDOW_COVERED_CALL_AFTER_OPEX_TOLERANCE
            if not within_tolerance:
                continue
            existing = out.get("covered_call_monthly")
            if existing is None or existing[2] > days_until:
                out["covered_call_monthly"] = (entry_day, opex, days_until)
            if days_until >= 0:
                break  # found the soonest forward entry; stop here

    for opex in opexes:
        # 45-DTE entry: calendar days back from OpEx, snapped to nearest weekday
        target_45 = calendar_days_before(opex, 45)
        if target_45.weekday() >= 5:
            # if it falls on weekend, shift to Friday (prior trading day)
            shift = target_45.weekday() - 4
            target_45 = target_45 - pd.Timedelta(days=shift).to_pytimedelta()
        days_until_45 = trading_days_between(run_date, target_45)
        if days_until_45 >= 0:  # only future or today
            for label in ("bull_put_45dte", "bear_call_45dte", "inverted_fly_45dte"):
                if label not in out or out[label][2] > days_until_45:
                    out[label] = (target_45, opex, days_until_45)

        # T-5 entry: 5 trading days before OpEx
        target_t5 = trading_day_offset(opex, -G.WINDOW_BULL_PUT_T5)
        days_until_t5 = trading_days_between(run_date, target_t5)
        if days_until_t5 >= 0:
            if "bull_put_t5" not in out or out["bull_put_t5"][2] > days_until_t5:
                out["bull_put_t5"] = (target_t5, opex, days_until_t5)

        # 75-DTE entry: ZEBRA
        target_75 = calendar_days_before(opex, 75)
        if target_75.weekday() >= 5:
            shift = target_75.weekday() - 4
            target_75 = target_75 - pd.Timedelta(days=shift).to_pytimedelta()
        days_until_75 = trading_days_between(run_date, target_75)
        if days_until_75 >= 0:
            if "zebra_75dte" not in out or out["zebra_75dte"][2] > days_until_75:
                out["zebra_75dte"] = (target_75, opex, days_until_75)

    return out


# ─── Per-(symbol, structure) verdict logic ───────────────────────────

def evaluate_opex_cell(symbol: str, structure: str, window_label: str,
                       target: date, opex: date, days_until: int,
                       regime: dict, run_date: date,
                       earnings_dates: list[date] | None = None) -> dict:
    """Apply gate + window logic to one (symbol, structure, window) tuple.

    Returns a row dict with fields: symbol, structure, window, target, opex,
    days_until, verdict, size, reason.
    """
    row = {
        "symbol": symbol, "structure": structure, "window": window_label,
        "target": str(target), "opex": str(opex),
        "days_until": days_until, "verdict": None, "size": 0.0, "reason": "",
    }

    # 0. Past target — calendar comparison; trading_days_between is unreliable
    # in sign across weekends.
    if target < run_date:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = f"target {target} already past"
        return row

    # 1. Hard-pause check (bull_put only)
    if structure.startswith("bull_put") and not structure.endswith("_earnings"):
        if regime.get("hard_pause_active"):
            row["verdict"] = G.VERDICT_PAUSE
            row["reason"] = "hard pause active (SPY<200dma + term_inv + IVR>0.5)"
            return row

    # 2. Regime gate per structure
    gate_ok = True
    gate_reason = ""
    if structure == "bull_put":
        if not regime.get("bull_put_signal_active"):
            gate_ok = False
            gate_reason = "bull_put gate off (need contango + VRP>0)"
    elif structure == "bear_call":
        if not regime.get("h1_active"):
            gate_ok = False
            gate_reason = "bear_call H1 gate off (need SPY<200dma + IVR>0.5)"
    elif structure in ("inverted_fly_pair", "inverted_fly_single"):
        if symbol in G.IF_NO_GATE_NAMES:
            pass  # GOOGL etc. skip the gate
        elif not regime.get("if_gate_active"):
            gate_ok = False
            gate_reason = "IF term-inv gate off (need term_inverted)"
    # ZEBRA: no regime gate
    # covered_call: no regime gate — range-bound thesis is structural
    # (floating-rate senior loans / high-yield credit ETFs), not regime-driven

    if not gate_ok:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = gate_reason
        return row

    # 2.5 Earnings-in-holding-window gate. Plan v1.7: "No binary earnings
    # inside the DTE window — earnings moves are bimodal and break the carry
    # thesis." Earnings-bias structures bypass: their holding window
    # intentionally straddles the earnings event.
    if not structure.endswith("_earnings") and earnings_dates:
        in_window = [ed for ed in earnings_dates if target <= ed <= opex]
        if in_window:
            row["verdict"] = G.VERDICT_SKIP
            row["reason"] = (
                f"binary earnings {in_window[0]} inside holding window "
                f"[{target}, {opex}]"
            )
            return row

    # 3. Window proximity (target is today or future at this point)
    if days_until > G.ENTRY_WINDOW_TOLERANCE:
        row["verdict"] = G.VERDICT_PENDING
        row["reason"] = (
            f"entry window in {days_until} trading days "
            f"(target {target}, OpEx {opex})"
        )
        return row

    # 4. GO — but check soft-downsize for sizing
    if regime.get("soft_downsize_active") and structure.startswith("bull_put"):
        row["verdict"] = G.VERDICT_DOWNSIZE
        row["size"] = G.SIZE_DOWNSIZE
        row["reason"] = "GO at half size (soft-downsize trigger active)"
    else:
        row["verdict"] = G.VERDICT_GO
        row["size"] = G.SIZE_DEFAULT
        row["reason"] = f"entry day for {window_label} (OpEx {opex})"
    return row


def load_cohort_earnings(run_date: date) -> dict[str, list[date]]:
    """Earnings calendar for the union of every OpEx + earnings cohort.

    Returns {symbol: sorted upcoming earnings dates}. Used by the
    earnings-in-holding-window gate in evaluate_opex_cell. ETFs return empty
    (yfinance does not cover them) — that is the correct state: ETFs have no
    binary earnings event.
    """
    all_syms = sorted(set(
        G.COHORT_BULL_PUT
        + G.COHORT_BEAR_CALL
        + G.COHORT_INVERTED_FLY_PAIR
        + G.COHORT_INVERTED_FLY_SINGLE
        + G.COHORT_ZEBRA_TIER1
        + G.COHORT_ZEBRA_TIER2
        + G.COHORT_EARNINGS_BULL_PUT
        + G.COHORT_EARNINGS_BEAR_CALL
        + G.COHORT_EARNINGS_INVERTED_FLY
    ))
    # 180-day horizon covers the longest window: ZEBRA 75-DTE entry against
    # the 5th-out OpEx (~150d total).
    cal = upcoming_earnings(all_syms, run_date, window_days=180)
    out: dict[str, list[date]] = {}
    if cal.empty:
        return out
    for sym, grp in cal.groupby("ticker"):
        out[sym] = sorted(grp["earnings_date"].tolist())
    return out


def build_opex_verdicts(regime: dict, windows: dict, run_date: date,
                         earnings_by_sym: dict[str, list[date]]) -> list[dict]:
    """For each OpEx-anchored structure, generate one row per (symbol, window)."""
    if regime is None:
        return []
    rows = []

    def ed(sym: str) -> list[date] | None:
        return earnings_by_sym.get(sym)

    # Bull put — both Window A (45-DTE managed) and Window B (T-5)
    if "bull_put_45dte" in windows:
        target, opex, days_until = windows["bull_put_45dte"]
        for sym in G.COHORT_BULL_PUT:
            rows.append(evaluate_opex_cell(
                sym, "bull_put", "45-DTE-managed (Window A)",
                target, opex, days_until, regime, run_date, ed(sym),
            ))
    if "bull_put_t5" in windows:
        target, opex, days_until = windows["bull_put_t5"]
        for sym in G.COHORT_BULL_PUT:
            rows.append(evaluate_opex_cell(
                sym, "bull_put", "T-5 (Window B)",
                target, opex, days_until, regime, run_date, ed(sym),
            ))

    # Bear call — 45-DTE only
    if "bear_call_45dte" in windows:
        target, opex, days_until = windows["bear_call_45dte"]
        for sym in G.COHORT_BEAR_CALL:
            if sym == "SPX" and G.SPX_EXCLUDED_FROM_QUALIFIER:
                continue
            rows.append(evaluate_opex_cell(
                sym, "bear_call", "45-DTE",
                target, opex, days_until, regime, run_date, ed(sym),
            ))

    # Inverted fly — pair + singles, 45-DTE
    if "inverted_fly_45dte" in windows:
        target, opex, days_until = windows["inverted_fly_45dte"]
        for sym in G.COHORT_INVERTED_FLY_PAIR:
            if sym == "SPX" and G.SPX_EXCLUDED_FROM_QUALIFIER:
                continue
            rows.append(evaluate_opex_cell(
                sym, "inverted_fly_pair", "45-DTE",
                target, opex, days_until, regime, run_date, ed(sym),
            ))
        for sym in G.COHORT_INVERTED_FLY_SINGLE:
            rows.append(evaluate_opex_cell(
                sym, "inverted_fly_single", "45-DTE",
                target, opex, days_until, regime, run_date, ed(sym),
            ))

    # ZEBRA — Tier 1 + Tier 2, 75-DTE
    if "zebra_75dte" in windows:
        target, opex, days_until = windows["zebra_75dte"]
        for sym in G.COHORT_ZEBRA_TIER1:
            rows.append(evaluate_opex_cell(
                sym, "zebra_tier1", "75-DTE",
                target, opex, days_until, regime, run_date, ed(sym),
            ))
        for sym in G.COHORT_ZEBRA_TIER2:
            rows.append(evaluate_opex_cell(
                sym, "zebra_tier2", "75-DTE",
                target, opex, days_until, regime, run_date, ed(sym),
            ))

    # Covered call — credit ETFs (BKLN/JNK/HYG), monthly cycle
    # Entry = trading day after prior monthly OpEx; expiry = next monthly OpEx
    # No regime gate; earnings gate naturally inert (ETFs don't report earnings)
    if "covered_call_monthly" in windows:
        target, opex, days_until = windows["covered_call_monthly"]
        for sym in G.COHORT_COVERED_CALL:
            rows.append(evaluate_opex_cell(
                sym, "covered_call", "monthly (post-OpEx entry)",
                target, opex, days_until, regime, run_date, ed(sym),
            ))

    return rows


def evaluate_earnings_cell(symbol: str, structure: str, earnings_date: date,
                            run_date: date, days_before: int) -> dict:
    """Earnings-track verdict: GO if today is exactly T-N before earnings,
    PENDING if upcoming within tolerance window, SKIP otherwise.

    Earnings track is calendar-driven (no regime gate per plan v1.7).
    """
    target = trading_day_offset(earnings_date, -days_before)
    # Calendar comparison for past/future (trading_days_between sign is
    # unreliable across weekends).
    if target < run_date:
        days_until = -1  # sentinel: past
    elif target == run_date:
        days_until = 0
    else:
        days_until = trading_days_between(run_date, target)

    row = {
        "symbol": symbol, "structure": structure,
        "window": f"earnings T-{days_before} ({earnings_date})",
        "target": str(target), "opex": "(earnings-anchored)",
        "days_until": days_until, "verdict": None, "size": 0.0, "reason": "",
    }

    if target < run_date:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = f"target {target} already past"
    elif days_until > 5:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = f"earnings {earnings_date} too far ({days_until} td)"
    elif days_until > G.ENTRY_WINDOW_TOLERANCE:
        row["verdict"] = G.VERDICT_PENDING
        row["reason"] = (
            f"T-{days_before} entry day for {symbol} earnings on {earnings_date} "
            f"in {days_until} trading days"
        )
    else:
        row["verdict"] = G.VERDICT_GO
        row["size"] = G.SIZE_DEFAULT
        row["reason"] = (
            f"T-{days_before} entry day for {symbol} earnings on {earnings_date}"
        )
    return row


def build_earnings_verdicts(run_date: date) -> list[dict]:
    """Look up upcoming earnings for the three earnings cohorts and emit verdicts."""
    all_earnings_names = sorted(set(
        G.COHORT_EARNINGS_BULL_PUT
        + G.COHORT_EARNINGS_BEAR_CALL
        + G.COHORT_EARNINGS_INVERTED_FLY
    ))
    cal = upcoming_earnings(all_earnings_names, run_date, window_days=30)
    if cal.empty:
        return []

    rows = []
    for _, ev in cal.iterrows():
        sym = ev["ticker"]
        ed = ev["earnings_date"]

        # Bull put earnings cohort
        if sym in G.COHORT_EARNINGS_BULL_PUT:
            days_before = (G.WINDOW_EARNINGS_T1
                           if sym in G.EARNINGS_T1_NAMES
                           else G.WINDOW_EARNINGS_T3)
            rows.append(evaluate_earnings_cell(
                sym, "bull_put_earnings", ed, run_date, days_before
            ))

        # INTC bear_call earnings (T-1)
        if sym in G.COHORT_EARNINGS_BEAR_CALL:
            rows.append(evaluate_earnings_cell(
                sym, "bear_call_earnings", ed, run_date, G.WINDOW_EARNINGS_T1
            ))

        # PLTR inverted_fly earnings (T-3)
        if sym in G.COHORT_EARNINGS_INVERTED_FLY:
            rows.append(evaluate_earnings_cell(
                sym, "inverted_fly_earnings", ed, run_date, G.WINDOW_EARNINGS_T3
            ))

    return rows


# ─── Output formatting ────────────────────────────────────────────────

def format_regime_block(regime: dict, run_date: date) -> str:
    if regime is None:
        return f"  (no regime_state available for {run_date})"
    stage = regime.get("stage", "?")
    stage_label = {0: "calm/bull", 1: "soft-downsize", 2: "SPY<200dma",
                   3: "H1 (bear regime)"}.get(stage, f"stage {stage}")
    spy = regime.get("spy_close")
    ma = regime.get("spy_ma200")
    pct = regime.get("spy_pct_to_ma200")
    ivr = regime.get("spy_ivr_252")
    term = regime.get("spy_term_spread")
    vrp = regime.get("spy_vrp")
    h1 = bool(regime.get("h1_active"))
    pause = bool(regime.get("hard_pause_active"))
    soft = bool(regime.get("soft_downsize_active"))
    if_gate = bool(regime.get("if_gate_active"))
    bp_signal = bool(regime.get("bull_put_signal_active"))

    lines = [
        f"  Run date:          {run_date}",
        f"  Regime stage:      {stage} ({stage_label})",
        f"  SPY close (as of {regime.get('as_of_close')}):  ${spy:.2f}  "
        f"({'+' if pct is not None and pct >= 0 else ''}{pct*100 if pct is not None else 0:.1f}% vs 200dma ${ma:.2f})",
        f"  IVR (252-day):     {ivr:.3f}  ({'HIGH' if regime.get('ivr_high') else 'low'})",
        f"  Term spread:       {term:+.4f}  ({'INVERTED' if regime.get('term_inverted') else 'contango'})",
        f"  VRP:               {vrp:+.4f}",
        "",
        f"  H1 active:                 {'ON' if h1 else 'off'}",
        f"  Hard pause active:         {'ON' if pause else 'off'}",
        f"  Soft-downsize active:      {'ON' if soft else 'off'}",
        f"  IF term-inv gate active:   {'ON' if if_gate else 'off'}",
        f"  Bull-put signal active:    {'ON' if bp_signal else 'off'}",
    ]
    return "\n".join(lines)


def format_entry_windows(windows: dict, run_date: date) -> str:
    if not windows:
        return "  (no upcoming windows in next 3 OpEx cycles)"
    rows = []
    for label, (target, opex, days_until) in sorted(windows.items(), key=lambda kv: kv[1][2]):
        if days_until == 0:
            tag = " ← TODAY"
        elif days_until <= G.ENTRY_WINDOW_TOLERANCE:
            tag = f" ← {days_until} trading day{'s' if days_until > 1 else ''} (within tolerance)"
        else:
            tag = ""
        rows.append(
            f"  {label:<22}  target {target}  (OpEx {opex})  "
            f"{days_until} trading days away{tag}"
        )
    return "\n".join(rows)


def format_verdicts(verdict_rows: list[dict]) -> str:
    """Per-structure summary + the actionable cells (GO + DOWNSIZE only)."""
    if not verdict_rows:
        return "  (no opex-window verdicts to report)"

    df = pd.DataFrame(verdict_rows)

    # Per-structure summary table
    summary_lines = ["  Per-structure summary:"]
    summary_lines.append(f"    {'structure':<22} {'window':<25} {'GO':>4} "
                         f"{'DOWN':>5} {'PEND':>5} {'SKIP':>5} {'PAUSE':>6}")
    for (structure, window), grp in df.groupby(["structure", "window"]):
        counts = grp["verdict"].value_counts()
        summary_lines.append(
            f"    {structure:<22} {window:<25} "
            f"{counts.get(G.VERDICT_GO, 0):>4} "
            f"{counts.get(G.VERDICT_DOWNSIZE, 0):>5} "
            f"{counts.get(G.VERDICT_PENDING, 0):>5} "
            f"{counts.get(G.VERDICT_SKIP, 0):>5} "
            f"{counts.get(G.VERDICT_PAUSE, 0):>6}"
        )

    # Actionable rows: GO + DOWNSIZE
    actionable = df[df["verdict"].isin([G.VERDICT_GO, G.VERDICT_DOWNSIZE])]
    pause_rows = df[df["verdict"] == G.VERDICT_PAUSE]

    out_lines = list(summary_lines)
    if not actionable.empty:
        out_lines.append("")
        out_lines.append("  Actionable today (GO/DOWNSIZE):")
        for _, r in actionable.iterrows():
            out_lines.append(
                f"    {r['symbol']:>6} | {r['structure']:<22} | "
                f"{r['window']:<25} | {r['verdict']:<8} | size={r['size']:.2f} "
                f"| {r['reason']}"
            )
    elif not pause_rows.empty:
        out_lines.append("")
        out_lines.append("  PAUSE active for some structures (no GO today).")
    else:
        out_lines.append("")
        out_lines.append("  No GO verdicts today (all PENDING or SKIP).")

    return "\n".join(out_lines)


# ─── Persistence ──────────────────────────────────────────────────────

QUALIFIER_COLUMNS = [
    "run_date", "symbol", "structure", "window",
    "target", "opex", "days_until",
    "verdict", "size", "reason",
    "regime_stage", "regime_h1", "regime_if_gate", "regime_bp_signal",
]


def write_qualifier_runs(rows: list[dict], regime: dict, run_date: date,
                         dry_run: bool = False) -> int:
    """Persist verdict rows to cycle_qualifier_runs in metal_project.db."""
    if not rows or dry_run:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cycle_qualifier_runs (
            run_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            structure TEXT NOT NULL,
            window TEXT,
            target TEXT,
            opex TEXT,
            days_until INTEGER,
            verdict TEXT,
            size REAL,
            reason TEXT,
            regime_stage INTEGER,
            regime_h1 INTEGER,
            regime_if_gate INTEGER,
            regime_bp_signal INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (run_date, symbol, structure, window)
        )
    """)
    placeholders = ", ".join(["?"] * len(QUALIFIER_COLUMNS))
    col_list = ", ".join(QUALIFIER_COLUMNS)
    rs = regime or {}
    for r in rows:
        full_row = {
            **r,
            "run_date": str(run_date),
            "regime_stage": rs.get("stage"),
            "regime_h1": int(rs.get("h1_active", 0)) if rs else None,
            "regime_if_gate": int(rs.get("if_gate_active", 0)) if rs else None,
            "regime_bp_signal": int(rs.get("bull_put_signal_active", 0)) if rs else None,
        }
        cur.execute(
            f"INSERT OR REPLACE INTO cycle_qualifier_runs ({col_list}) "
            f"VALUES ({placeholders})",
            [full_row.get(c) for c in QUALIFIER_COLUMNS],
        )
    conn.commit()
    conn.close()
    return len(rows)


def write_parquet_artifact(rows: list[dict], regime: dict, run_date: date) -> Path:
    """Write parquet snapshot of this run for later inspection / dashboard use."""
    QUALIFIER_DIR.mkdir(parents=True, exist_ok=True)
    out = QUALIFIER_DIR / f"qualifier_{run_date}.parquet"
    if not rows:
        return out
    df = pd.DataFrame(rows)
    df["run_date"] = str(run_date)
    if regime:
        df["regime_stage"] = regime.get("stage")
        df["regime_h1"] = int(regime.get("h1_active", 0))
        df["regime_if_gate"] = int(regime.get("if_gate_active", 0))
        df["regime_bp_signal"] = int(regime.get("bull_put_signal_active", 0))
    df.to_parquet(out, index=False)
    return out


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", default=None,
                        help="Override run date (YYYY-MM-DD). Default: today.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print only; skip DB write and parquet artifact")
    args = parser.parse_args()

    run_date = (
        datetime.strptime(args.run_date, "%Y-%m-%d").date()
        if args.run_date else date.today()
    )

    regime = load_regime_state(run_date)
    windows = compute_window_targets(run_date)
    earnings_by_sym = load_cohort_earnings(run_date)
    opex_rows = (
        build_opex_verdicts(regime, windows, run_date, earnings_by_sym)
        if regime else []
    )
    earnings_rows = build_earnings_verdicts(run_date)
    verdict_rows = opex_rows + earnings_rows

    print()
    print("=" * 78)
    print(f"  MaxPain Cycle Qualifier — {run_date}")
    print("=" * 78)
    print()
    print("REGIME STATE")
    print("-" * 78)
    print(format_regime_block(regime, run_date))
    print()
    print("UPCOMING ENTRY WINDOWS")
    print("-" * 78)
    print(format_entry_windows(windows, run_date))
    print()
    print("PER-STRUCTURE VERDICTS")
    print("-" * 78)
    print(format_verdicts(verdict_rows))
    print()

    # Persist
    if not args.dry_run and verdict_rows:
        n = write_qualifier_runs(verdict_rows, regime, run_date)
        artifact = write_parquet_artifact(verdict_rows, regime, run_date)
        print(f"  ✓ Persisted {n} rows to cycle_qualifier_runs")
        print(f"  ✓ Parquet artifact: {artifact}")
    elif args.dry_run:
        print("  (dry-run — DB and parquet writes skipped)")

    print("=" * 78)


if __name__ == "__main__":
    main()
