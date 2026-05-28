"""Compose the pre-cycle data bundle the AI advisor reads.

Four sections (see prompts/pre_cycle_commentary/v1.md):
  1. Macro brief     — lib.macro_brief.build_macro_brief() (curve / FedWatch / news)
  2. Regime snapshot — today's regime_state + 1d-prior + 5d-avg deltas
  3. Verdict review  — today's cycle_qualifier_runs rows (decisions in question)
  4. Open positions  — currently-open placed=1 trades (CONTEXT for concentration,
                       NOT under review)

The output is markdown. Composed via `compose_bundle(run_date)`. A helper
`has_decision_relevant_verdicts(run_date)` lets the cron entry-point skip
all-PENDING / all-SKIP days without spending API.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH  # noqa: E402
from lib.sector_map import get_sector, ETF_SENTINEL, UNKNOWN_SENTINEL  # noqa: E402

# Fields tracked across regime_state for 1d/5d delta comparison
REGIME_NUMERIC_FIELDS = [
    "spy_close", "spy_pct_to_ma200", "spy_ivr_252", "spy_term_spread",
    "spy_vrp", "spy_vix",
]
REGIME_FLAG_FIELDS = [
    "h1_active", "bull_put_signal_active", "if_gate_active",
    "hard_pause_active", "soft_downsize_active", "stage",
]


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def has_decision_relevant_verdicts(run_date: str) -> tuple[bool, int, int]:
    """Return (has_decisions, n_go, n_downsize) for the qualifier run on
    `run_date`. The cron entrypoint uses this to skip empty days.
    """
    with _conn() as c:
        row = c.execute("""
            SELECT
              SUM(CASE WHEN verdict = 'GO' THEN 1 ELSE 0 END)       AS n_go,
              SUM(CASE WHEN verdict = 'DOWNSIZE' THEN 1 ELSE 0 END) AS n_ds
            FROM cycle_qualifier_runs
            WHERE run_date = ?
        """, (run_date,)).fetchone()
    n_go = int(row["n_go"] or 0)
    n_ds = int(row["n_ds"] or 0)
    return (n_go + n_ds) > 0, n_go, n_ds


# ─── Section 1 — Macro brief ──────────────────────────────────────────────

def _macro_section() -> str:
    """Reuse the existing macro_brief renderer."""
    try:
        from lib.macro_brief import build_macro_brief, render_text
        brief = build_macro_brief()
        text = render_text(brief).strip()
        if not text:
            return "## Macro brief\n\n_Macro brief returned empty._"
        return f"## Macro brief\n\n```\n{text}\n```"
    except Exception as e:
        return f"## Macro brief\n\n_Unavailable ({e.__class__.__name__}: {e})._"


# ─── Section 2 — Regime snapshot ──────────────────────────────────────────

def _trading_days_before(run_date: str, n: int) -> list[str]:
    """Return up to N most-recent snapshot_dates strictly before run_date."""
    with _conn() as c:
        rows = c.execute("""
            SELECT snapshot_date FROM regime_state
            WHERE snapshot_date < ?
            ORDER BY snapshot_date DESC
            LIMIT ?
        """, (run_date, n)).fetchall()
    return [r["snapshot_date"] for r in rows]


def _regime_row(snapshot_date: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM regime_state WHERE snapshot_date = ?",
            (snapshot_date,),
        ).fetchone()
    return dict(row) if row else None


def _fmt_pct(v: float | None, places: int = 2) -> str:
    if v is None:
        return "—"
    try:
        return f"{v * 100:+.{places}f}%" if abs(v) < 1 else f"{v:+.{places}f}"
    except Exception:
        return str(v)


def _fmt_delta(curr: float | None, prior: float | None, places: int = 2,
               as_pct: bool = False) -> str:
    if curr is None or prior is None:
        return "Δ —"
    diff = curr - prior
    if as_pct:
        return f"Δ {diff * 100:+.{places}f}pp"
    return f"Δ {diff:+.{places}f}"


def _regime_section(run_date: str) -> str:
    today = _regime_row(run_date)
    # Fall back to most-recent snapshot if today's row isn't in yet (weekend / pre-cron)
    used_date = run_date
    if today is None:
        recent = _trading_days_before(run_date + "z", 1)  # all dates < "Z" string
        if not recent:
            return ("## Regime snapshot\n\n_No regime_state rows available_.")
        used_date = recent[0]
        today = _regime_row(used_date)
    if today is None:
        return "## Regime snapshot\n\n_No regime_state rows available_."

    priors = _trading_days_before(used_date, 5)
    prior_1d = _regime_row(priors[0]) if priors else None
    prior_5d_rows = [_regime_row(d) for d in priors[:5]] if priors else []

    def _avg(field: str) -> float | None:
        vals = [r[field] for r in prior_5d_rows
                if r is not None and r.get(field) is not None]
        return sum(vals) / len(vals) if vals else None

    lines = [f"## Regime snapshot — {used_date}"]
    lines.append("")
    lines.append(f"Stage: **{today['stage']}**  (0=calm/bull, 1=watch, 2=warn, 3=stress, 4=crash)")
    lines.append("")
    lines.append("| Field | Today | 1d prior | Δ 1d | 5d avg | Δ vs 5d |")
    lines.append("|---|---|---|---|---|---|")
    for f in REGIME_NUMERIC_FIELDS:
        curr = today.get(f)
        p1 = prior_1d.get(f) if prior_1d else None
        p5 = _avg(f)
        # Pct-of-spot fields: display as pct, deltas in pp
        as_pct = f in ("spy_pct_to_ma200", "spy_ivr_252", "spy_term_spread", "spy_vrp")
        lines.append(
            f"| {f} | {_fmt_pct(curr, 2 if as_pct else 2)} "
            f"| {_fmt_pct(p1)} | {_fmt_delta(curr, p1, 2, as_pct)} "
            f"| {_fmt_pct(p5)} | {_fmt_delta(curr, p5, 2, as_pct)} |"
        )
    lines.append("")
    lines.append("**Gate flags (today):**")
    for f in REGIME_FLAG_FIELDS:
        if f == "stage":
            continue
        v = today.get(f)
        p = prior_1d.get(f) if prior_1d else None
        change = "" if p is None or p == v else f"  (was {p})"
        lines.append(f"  - `{f}` = **{v}**{change}")
    lines.append("")
    if used_date != run_date:
        lines.append(f"_(Note: regime row for {run_date} not yet in DB; using most-recent snapshot {used_date}.)_")
    return "\n".join(lines)


# ─── Section 3 — Verdict review (forward) ────────────────────────────────

def _verdict_section(run_date: str) -> str:
    with _conn() as c:
        df = pd.read_sql_query("""
            SELECT symbol, structure, window, opex, days_until, verdict, size,
                   reason, sector, sector_rank_position
            FROM cycle_qualifier_runs
            WHERE run_date = ?
            ORDER BY
              CASE verdict
                WHEN 'GO' THEN 0
                WHEN 'DOWNSIZE' THEN 1
                WHEN 'PENDING' THEN 2
                WHEN 'SKIP' THEN 3
                ELSE 4
              END,
              structure, symbol
        """, c, params=(run_date,))

    if df.empty:
        return f"## Verdict review (forward) — run_date {run_date}\n\n_No qualifier rows for this date._"

    parts = [f"## Verdict review (forward) — run_date {run_date}", ""]

    # Per-structure summary first
    summary = df.groupby(["structure", "verdict"]).size().unstack(fill_value=0)
    parts.append("**Per-structure verdict counts:**")
    parts.append("")
    parts.append("```")
    parts.append(summary.to_string())
    parts.append("```")
    parts.append("")

    # GO / DOWNSIZE detail (always full); PENDING/SKIP omitted unless none decisive
    decisive = df[df["verdict"].isin(["GO", "DOWNSIZE"])]
    if not decisive.empty:
        parts.append("**Decision-relevant rows (GO / DOWNSIZE):**")
        parts.append("")
        parts.append("| symbol | structure | window | opex | DTE | verdict | size | sector | reason |")
        parts.append("|---|---|---|---|---|---|---|---|---|")
        for _, r in decisive.iterrows():
            parts.append(
                f"| {r['symbol']} | {r['structure']} | {r['window']} | {r['opex']} | "
                f"{r['days_until']} | **{r['verdict']}** | {r['size']:g} | "
                f"{r['sector'] or '—'} | {r['reason'] or ''} |"
            )
    else:
        parts.append("_All verdicts today are PENDING or SKIP. No new entries in scope._")
        parts.append("")
        # Show a compact summary of why (top reasons by structure)
        top_reasons = (
            df.groupby(["structure", "verdict", "reason"])
            .size()
            .reset_index(name="n")
            .sort_values(["structure", "n"], ascending=[True, False])
        )
        parts.append("**Top reasons (PENDING/SKIP):**")
        parts.append("")
        parts.append("```")
        parts.append(top_reasons.head(20).to_string(index=False))
        parts.append("```")

    return "\n".join(parts)


# ─── Section 4 — Open positions (CONTEXT, not under review) ──────────────

def _open_positions_section() -> str:
    with _conn() as c:
        df = pd.read_sql_query("""
            SELECT id, symbol, spread_type, opex_date,
                   short_strike, long_strike, entry_date, entry_credit, shares
            FROM spread_score_trades
            WHERE status = 'open' AND placed = 1
            ORDER BY opex_date, symbol
        """, c)

    if df.empty:
        return ("## Context — Open positions (NOT under review)\n\n"
                "_No open placed positions._")

    df["sector"] = df["symbol"].apply(get_sector)

    parts = [
        "## Context — Open positions (NOT under review)",
        "",
        "_These positions are governed by mechanical exits (STP LMT GTC at 2× credit, "
        "T-21 management cue). They are included here ONLY for concentration / "
        "correlation context — do not interpret as candidates for management._",
        "",
        "| id | symbol | structure | opex | strikes | entry | shares | sector |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        strikes = (
            f"{r['short_strike']:g}/{r['long_strike']:g}"
            if r["spread_type"] != "stock" else "—"
        )
        entry = f"${r['entry_credit']:+.2f}" if pd.notna(r["entry_credit"]) else "—"
        parts.append(
            f"| {r['id']} | {r['symbol']} | {r['spread_type']} | {r['opex_date']} "
            f"| {strikes} | {entry} | {r['shares']} | {r['sector']} |"
        )

    # Sector concentration roll-up (single names only; ETFs / unknowns excluded)
    single_names = df[~df["sector"].isin([ETF_SENTINEL, UNKNOWN_SENTINEL])]
    if not single_names.empty:
        per_opex = (
            single_names.groupby(["opex_date", "sector"])
            .size()
            .reset_index(name="n_open")
            .sort_values(["opex_date", "n_open"], ascending=[True, False])
        )
        parts.append("")
        parts.append("**Sector concentration in open book (single names only; ETFs exempt):**")
        parts.append("")
        parts.append("```")
        parts.append(per_opex.to_string(index=False))
        parts.append("```")

    return "\n".join(parts)


# ─── Top-level composer ──────────────────────────────────────────────────

def compose_bundle(run_date: str | None = None) -> str:
    """Assemble the four sections into a single markdown payload."""
    if run_date is None:
        run_date = date.today().isoformat()

    sections = [
        f"# Pre-cycle data bundle — {run_date}",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        _macro_section(),
        "",
        _regime_section(run_date),
        "",
        _verdict_section(run_date),
        "",
        _open_positions_section(),
    ]
    return "\n".join(sections)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", default=None,
                    help="ISO date for qualifier run (defaults to today)")
    args = ap.parse_args()
    print(compose_bundle(args.run_date))
