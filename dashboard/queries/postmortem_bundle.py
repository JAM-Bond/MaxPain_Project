"""Compose the post-mortem data bundle for a given OpEx cycle.

Pulls all seven sources (closed trades, exit-timing counterfactual,
walk-forward context, daily-alert summaries, alert events, regime
snapshots, qualifier verdicts) into one structured markdown payload that
the AI advisor reads.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PROFILE_DIR = ROOT / "data" / "profile"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH  # noqa: E402


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def list_available_opex() -> list[str]:
    with _conn() as c:
        rows = c.execute("""
            SELECT DISTINCT opex_date FROM spread_score_trades
            WHERE status = 'closed' AND opex_date IS NOT NULL
              AND placed = 1
            ORDER BY opex_date DESC
        """).fetchall()
    return [r["opex_date"] for r in rows]


def _closed_trades_section(opex: str) -> tuple[str, dict]:
    with _conn() as c:
        df = pd.read_sql_query("""
            SELECT id, symbol, spread_type, short_strike, long_strike,
                   entry_credit, exit_credit, entry_date, exit_date,
                   final_pnl, shares, qualifier_run_date, target_hit_date,
                   target_hit_pnl
            FROM spread_score_trades
            WHERE opex_date = ? AND status = 'closed' AND placed = 1
            ORDER BY symbol, spread_type
        """, c, params=(opex,))
    if df.empty:
        return "## Closed trades\n\n_No closed placed trades for this OpEx._", {"n_closed": 0, "total_pnl": 0}

    df["pnl_str"] = df["final_pnl"].apply(lambda v: f"${v:+,.0f}" if pd.notna(v) else "—")
    df["entry_str"] = df["entry_credit"].apply(lambda v: f"${v:+.2f}")
    df["exit_str"] = df["exit_credit"].apply(lambda v: f"${v:+.2f}" if pd.notna(v) else "—")
    df["strikes"] = df.apply(lambda r: f"{r['short_strike']:g}/{r['long_strike']:g}", axis=1)

    n_closed = len(df)
    total_pnl = df["final_pnl"].sum()
    wins = (df["final_pnl"] > 0).sum()
    win_rate = wins / n_closed * 100 if n_closed else 0
    by_struct = df.groupby("spread_type")["final_pnl"].agg(["count", "sum", "mean"]).round(0)

    lines = ["## Closed trades", ""]
    lines.append(f"**N = {n_closed}** placed-closed credit verticals · "
                 f"realized **${total_pnl:+,.0f}** · win rate **{win_rate:.0f}%** ({wins}/{n_closed})")
    lines.append("")
    lines.append("By structure:")
    for st, row in by_struct.iterrows():
        lines.append(f"- {st}: n={int(row['count'])}, total ${row['sum']:+,.0f}, mean ${row['mean']:+,.0f}")
    lines.append("")
    lines.append("Per-trade detail:")
    lines.append("")
    lines.append("| id | symbol | structure | strikes | qty | entry | exit | days held | exit→opex | pnl |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for _, r in df.iterrows():
        try:
            ed = datetime.strptime(r["entry_date"], "%Y-%m-%d").date()
            xd = datetime.strptime(r["exit_date"], "%Y-%m-%d").date() if r["exit_date"] else None
            opex_d = datetime.strptime(opex, "%Y-%m-%d").date()
            days_held = (xd - ed).days if xd else "—"
            exit_to_opex = (opex_d - xd).days if xd else "—"
        except Exception:
            days_held = "—"; exit_to_opex = "—"
        qty = int(r["shares"]) if pd.notna(r["shares"]) else 1
        lines.append(
            f"| {r['id']} | {r['symbol']} | {r['spread_type']} | {r['strikes']} | {qty} | "
            f"{r['entry_str']} | {r['exit_str']} | {days_held} | {exit_to_opex}d | {r['pnl_str']} |"
        )
    return "\n".join(lines), {"n_closed": n_closed, "total_pnl": float(total_pnl)}


def _exit_timing_section(opex: str) -> str:
    """Held-to-expiry counterfactual. Runs if OpEx has happened, else
    runs as-of today and labels accordingly."""
    try:
        from scripts.postmortem.exit_timing_counterfactual import run, render
    except Exception as e:
        return f"## Exit-timing counterfactual\n\n_Module import failed: {e}_"

    result = run(opex)
    if not result.get("ok"):
        return f"## Exit-timing counterfactual\n\n_{result.get('error', 'no data')}_"

    rows = [r for r in result["rows"] if r.get("delta") is not None]
    if not rows:
        return f"## Exit-timing counterfactual\n\n_No analyzable rows ({result.get('label')})._"

    sum_actual = sum(r["actual_pnl"] for r in rows)
    sum_held = sum(r["held_pnl"] for r in rows)
    sum_delta = sum_held - sum_actual
    n_too_early = sum(1 for r in rows if r["delta"] > 0)
    n_correct = sum(1 for r in rows if r["delta"] < 0)

    lines = ["## Exit-timing counterfactual", ""]
    lines.append(f"Anchor: **{result['label']}** ({result['anchor_date']})")
    lines.append(f"")
    lines.append(f"- Total actual P/L: **${sum_actual:+,.0f}**")
    lines.append(f"- Total held-to-anchor: **${sum_held:+,.0f}**")
    lines.append(f"- Net delta (held − actual): **${sum_delta:+,.0f}**")
    lines.append(f"- Exited too early: {n_too_early} / {len(rows)}")
    lines.append(f"- Exit was correct: {n_correct} / {len(rows)}")
    lines.append("")
    lines.append("Per-trade (sorted by delta desc):")
    lines.append("")
    lines.append("| id | symbol | structure | strikes | actual | held | Δ | sign |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: x["delta"], reverse=True):
        sign = "↑ too early" if r["delta"] > 0 else ("↓ exit correct" if r["delta"] < 0 else "—")
        lines.append(
            f"| {r['id']} | {r['symbol']} | {r['spread_type']} | {r['strikes']} | "
            f"${r['actual_pnl']:+.0f} | ${r['held_pnl']:+.0f} | ${r['delta']:+.0f} | {sign} |"
        )
    return "\n".join(lines)


def _walkforward_context_section(opex: str) -> str:
    """For each name actually traded in the cycle, show its walk-forward
    recommendation (if any)."""
    with _conn() as c:
        symbols = c.execute("""
            SELECT DISTINCT symbol, spread_type FROM spread_score_trades
            WHERE opex_date = ? AND status = 'closed' AND placed = 1
              AND (spread_type LIKE 'bull_put%' OR spread_type LIKE 'bear_call%' OR spread_type LIKE 'inverted_fly%')
            ORDER BY symbol
        """, (opex,)).fetchall()

    if not symbols:
        return "## Walk-forward context per traded name\n\n_No applicable trades._"

    bp_rec_path = PROFILE_DIR / "bull_put_moneyness_recommendation.parquet"
    bc_rec_path = PROFILE_DIR / "bear_call_moneyness_recommendation.parquet"
    if_rec_path = PROFILE_DIR / "inverted_fly_wing_recommendation.parquet"
    bp_rec = pd.read_parquet(bp_rec_path) if bp_rec_path.exists() else pd.DataFrame()
    bc_rec = pd.read_parquet(bc_rec_path) if bc_rec_path.exists() else pd.DataFrame()
    if_rec = pd.read_parquet(if_rec_path) if if_rec_path.exists() else pd.DataFrame()

    lines = ["## Walk-forward context per traded name", ""]
    lines.append("Per-ticker recommendations from `data/profile/*_recommendation.parquet` "
                 "(only present when both train and val passed Wilcoxon at p < 0.05).")
    lines.append("")
    lines.append("| symbol | structure | recommended | val n | val p |")
    lines.append("|---|---|---|---|---|")
    for r in symbols:
        sym, st = r["symbol"], r["spread_type"]
        rec = ""
        val_n = ""
        val_p = ""
        if st.startswith("bull_put") and not bp_rec.empty:
            m = bp_rec[(bp_rec["ticker"] == sym) & (bp_rec["exit_rule"] == "mgd50")]
            if len(m):
                rec = m.iloc[0]["recommended_moneyness"]
                val_n = int(m.iloc[0]["val_n"])
                val_p = round(m.iloc[0]["val_p"], 4)
        elif st.startswith("bear_call") and not bc_rec.empty:
            m = bc_rec[(bc_rec["ticker"] == sym) & (bc_rec["exit_rule"] == "mgd50")]
            if len(m):
                rec = m.iloc[0]["recommended_moneyness"]
                val_n = int(m.iloc[0]["val_n"])
                val_p = round(m.iloc[0]["val_p"], 4)
        elif st.startswith("inverted_fly") and not if_rec.empty:
            m = if_rec[if_rec["ticker"] == sym]
            if len(m):
                rec = m.iloc[0]["recommended_variant"]
                val_n = int(m.iloc[0]["val_n"])
                val_p = round(m.iloc[0]["val_p"], 4)
        rec_str = rec if rec else "_(no validated rec — uses default)_"
        lines.append(f"| {sym} | {st} | {rec_str} | {val_n} | {val_p} |")
    return "\n".join(lines)


def _daily_alerts_section(opex: str) -> str:
    """Summarize daily_alert_runs entries in the cycle window."""
    opex_d = datetime.strptime(opex, "%Y-%m-%d").date()
    cycle_start = opex_d.replace(day=1).isoformat()  # Approx: cycle is monthly
    with _conn() as c:
        try:
            df = pd.read_sql_query("""
                SELECT run_date, severity, n_constructions, has_events, subject
                FROM daily_alert_runs
                WHERE run_date BETWEEN ? AND ?
                ORDER BY run_date
            """, c, params=(cycle_start, opex))
        except Exception:
            return "## Daily alerts (cycle window)\n\n_daily_alert_runs table not yet populated._"
    if df.empty:
        return "## Daily alerts (cycle window)\n\n_No archived daily-alert runs in window._"
    by_sev = df["severity"].value_counts().to_dict()

    lines = ["## Daily alerts (cycle window)", ""]
    lines.append(f"Archive coverage: **{len(df)}** runs from {df['run_date'].min()} → {df['run_date'].max()}")
    lines.append(f"Severity mix: " + ", ".join(f"{k}={v}" for k, v in by_sev.items()))
    lines.append("")
    lines.append("Per-day:")
    lines.append("")
    lines.append("| date | severity | constructions | events | subject |")
    lines.append("|---|---|---|---|---|")
    for _, r in df.iterrows():
        events = "✓" if r["has_events"] else "—"
        subj = (r["subject"] or "")[:60]
        lines.append(f"| {r['run_date']} | {r['severity']} | {r['n_constructions']} | {events} | {subj} |")
    return "\n".join(lines)


def _alert_events_section(opex: str) -> str:
    with _conn() as c:
        try:
            df = pd.read_sql_query("""
                SELECT alert_date, symbol, severity, alert_type, recommendation,
                       price_at_alert, pin_confidence, gamma_sign, action_taken,
                       outcome_correct
                FROM alert_history
                WHERE opex_date = ?
                ORDER BY alert_date, symbol
            """, c, params=(opex,))
        except Exception:
            return "## Per-symbol alert events\n\n_alert_history not available._"
    if df.empty:
        return "## Per-symbol alert events\n\n_No alert_history rows for this OpEx._"
    by_sev = df["severity"].value_counts().to_dict()
    lines = ["## Per-symbol alert events", ""]
    lines.append(f"N = {len(df)} events. Severity mix: " + ", ".join(f"{k}={v}" for k, v in by_sev.items()))
    lines.append("")
    head = df.head(40)
    lines.append("First 40 rows:")
    lines.append("")
    lines.append("| date | symbol | sev | type | reco | price | pin_conf | gamma | action | correct |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for _, r in head.iterrows():
        pin = f"{r['pin_confidence']:.2f}" if pd.notna(r["pin_confidence"]) else "—"
        price = f"${r['price_at_alert']:.2f}" if pd.notna(r["price_at_alert"]) else "—"
        lines.append(
            f"| {r['alert_date']} | {r['symbol']} | {r['severity']} | {r['alert_type']} | "
            f"{r['recommendation'] or '—'} | {price} | {pin} | {r['gamma_sign'] or '—'} | "
            f"{r['action_taken'] or '—'} | {r['outcome_correct'] if r['outcome_correct'] is not None else '—'} |"
        )
    if len(df) > 40:
        lines.append(f"\n_(+{len(df) - 40} more rows omitted)_")
    return "\n".join(lines)


def _qualifier_section(opex: str) -> str:
    with _conn() as c:
        try:
            df = pd.read_sql_query("""
                SELECT run_date, symbol, structure, verdict, size_factor, reason
                FROM cycle_qualifier_runs
                WHERE opex_date = ?
                ORDER BY run_date DESC, symbol
            """, c, params=(opex,))
        except Exception:
            return "## Qualifier verdicts\n\n_cycle_qualifier_runs not available or schema mismatch._"
    if df.empty:
        return "## Qualifier verdicts\n\n_No qualifier runs for this OpEx._"
    by_verdict = df["verdict"].value_counts().to_dict()
    lines = ["## Qualifier verdicts", ""]
    lines.append(f"N = {len(df)} (run_date span {df['run_date'].min()} → {df['run_date'].max()})")
    lines.append("Verdict mix: " + ", ".join(f"{k}={v}" for k, v in by_verdict.items()))
    lines.append("")
    head = df.head(30)
    lines.append("First 30:")
    lines.append("")
    lines.append("| run_date | symbol | structure | verdict | size | reason |")
    lines.append("|---|---|---|---|---|---|")
    for _, r in head.iterrows():
        sz = f"{r['size_factor']:.2f}" if pd.notna(r["size_factor"]) else "—"
        reason = (r["reason"] or "")[:80]
        lines.append(
            f"| {r['run_date']} | {r['symbol']} | {r['structure']} | {r['verdict']} | {sz} | {reason} |"
        )
    if len(df) > 30:
        lines.append(f"\n_(+{len(df) - 30} more rows omitted)_")
    return "\n".join(lines)


def _macro_signature_section(opex: str) -> str:
    """Per-symbol macro-sensitivity profile for every closed trade in the cycle.

    Surfaces the per-name attributes built by Phase 5 of the macro-sensitivity
    profile (lib/macro_profile.get) so the AI advisor and human reader have a
    quantitative macro-context lens on each stop/win — e.g., "WFC stopped on
    5/12: β_t10yie=-0.06 (use=True), HIKE_2022-regime mean was -0.04 — a
    regime-consistent inflation-shock loser."

    Caveat: the profile is as-of-current-build (latest rolling β + stability
    tags). For post-mortems on cycles older than ~30 days the picture may
    have drifted; the signal is most useful when run right after cycle close.
    """
    try:
        from lib.macro_profile import get as macro_get, load_profile
    except Exception as e:
        return f"## Macro signature\n\n_lib.macro_profile not available: {e}_"

    with _conn() as c:
        df = pd.read_sql_query("""
            SELECT DISTINCT symbol
            FROM spread_score_trades
            WHERE opex_date = ? AND status = 'closed' AND placed = 1
            ORDER BY symbol
        """, c, params=(opex,))
    if df.empty:
        return "## Macro signature\n\n_No closed placed trades for this OpEx._"

    try:
        prof = load_profile()
        as_of = prof["as_of_date"].iloc[0]
        regime = prof["regime"].iloc[0]
    except FileNotFoundError:
        return ("## Macro signature\n\n_macro_profile.parquet not yet built. Run "
                "`python3.11 scripts/macro/build_macro_profile.py`._")

    lines = [
        "## Macro signature",
        "",
        f"_Profile as-of {as_of.date() if hasattr(as_of, 'date') else as_of}; current regime: **{regime}**._",
        f"_Caveat: profile reflects most-recent rolling β + Phase-3 stability tags, NOT the β as of the trade date._",
        "",
        "| symbol | β_mkt (tier) | β_dgs10 (tier, use) | β_credit (tier, use) | β_t10yie (tier, use) | dollar | oil | vol | r² |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for _, r in df.iterrows():
        sym = r["symbol"]
        p = macro_get(sym)
        if p is None:
            lines.append(f"| {sym} | _not in cohort_ |  |  |  |  |  |  |  |")
            continue
        def fmt(beta, tier, use=None):
            base = f"{beta:+.3f} ({tier}"
            if use is not None:
                base += f", use={'✓' if use else '✗'}"
            return base + ")"
        lines.append(
            f"| {sym} | "
            f"{p['beta_mkt']:+.2f} ({p['beta_mkt_tier']}) | "
            f"{fmt(p['beta_dgs10'], p['beta_dgs10_tier'], p['beta_dgs10_use'])} | "
            f"{fmt(p['beta_credit'], p['beta_credit_tier'], p['beta_credit_use'])} | "
            f"{fmt(p['beta_t10yie'], p['beta_t10yie_tier'], p['beta_t10yie_use'])} | "
            f"{p['dollar_tier']} | {p['oil_tier']} | {p['vol_tier']} | "
            f"{p['r2']:.2f} |"
        )
    lines.append("")
    lines.append("**How to read:** `β_*_use = ✓` means Phase 3 stability validation passed and the β is "
                 "a trustworthy quantitative sizing input. `✗` means the β reverses across rate regimes — "
                 "use the *tier* (POS_HIGH / NEG_MED / etc.) for directional context only, not for "
                 "quantitative scaling.")
    return "\n".join(lines)


def compose_bundle(opex: str) -> tuple[str, dict]:
    """Build the full bundle for a given OpEx. Returns (bundle_text, metadata)."""
    closed_text, closed_meta = _closed_trades_section(opex)
    parts = [
        f"# Post-mortem bundle — OpEx {opex}",
        f"",
        f"_Generated {datetime.now().isoformat(timespec='seconds')}_",
        f"",
        closed_text,
        "",
        _exit_timing_section(opex),
        "",
        _walkforward_context_section(opex),
        "",
        _macro_signature_section(opex),
        "",
        _daily_alerts_section(opex),
        "",
        _alert_events_section(opex),
        "",
        _qualifier_section(opex),
    ]
    bundle = "\n".join(parts)
    meta = {
        **closed_meta,
        "opex": opex,
        "char_count": len(bundle),
        "approx_tokens": len(bundle) // 4,
    }
    return bundle, meta
