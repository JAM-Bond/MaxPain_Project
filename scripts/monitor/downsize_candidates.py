"""DOWNSIZE CANDIDATES section for the daily alert.

Two groups, each affected ticker carrying a brief reason:

  A. New entries — names the cycle qualifier flagged DOWNSIZE (half-size) today
     (soft-downsize stage, bull_put >10% below 200-DMA, breadth-🔴 zebra).

  B. Open positions to trim — long-delta open positions (bull_put / zebra) under
     elevated risk. Triggers (any; user-selected 2026-06-11):
       • breadth ring 🔴 (narrowing + extended = top signature)
       • early-warning cascade 🚨 (2+ rings 🔴 = book-wide exit posture)
       • the position's own name 🔴 (below its 200-DMA + system stress)
       • macro-concentration cluster (shares a macro tier with other open names)
     Scoped to LONG-DELTA structures: trimming is about reducing bullish exposure
     under stress, and 🔴 on a bear_call is favorable/gate-driven, not adverse.
     Descriptive only — mechanical bull_put downsizing on breadth-🔴 was falsified
     (defined-risk managed bull_puts absorb it), so this informs, it does not gate.
     Positions already flagged as CLOSE candidates are excluded (close, don't trim).

render returns {'text': str}; empty when nothing fires.
"""
from __future__ import annotations

import sqlite3

LONG_DELTA = {"bull_put", "bull_put_mp", "zebra", "zebra_protected"}


def _qualifier_downsize_rows(conn: sqlite3.Connection) -> list[tuple]:
    """Latest cycle_qualifier_runs rows with a DOWNSIZE verdict (symbol, structure, reason)."""
    try:
        latest = conn.execute("SELECT MAX(run_date) FROM cycle_qualifier_runs").fetchone()
        if not latest or not latest[0]:
            return []
        rows = conn.execute(
            "SELECT symbol, structure, reason FROM cycle_qualifier_runs "
            "WHERE run_date = ? AND verdict = 'DOWNSIZE' ORDER BY structure, symbol",
            (latest[0],),
        ).fetchall()
        out = []
        for sym, struct, reason in rows:
            reason = (reason or "").split(" [orig:")[0].strip()  # drop the noisy [orig: …] tail
            out.append((sym, struct, reason))
        return out
    except Exception:
        return []


def _breadth_red(conn: sqlite3.Connection) -> bool:
    try:
        from lib.breadth_ring import latest_persisted_ring
        ring = latest_persisted_ring(conn)
        return bool(ring and ring.get("status") == "🔴")
    except Exception:
        return False


def _macro_cluster_map(open_syms: list[str]) -> dict:
    """symbol → (bucket_label, [other symbols in the bucket]) for the first
    macro-concentration bucket the symbol falls in. Empty on any failure."""
    out: dict = {}
    try:
        from lib.macro_profile import cohort_macro_concentration
        dupes = cohort_macro_concentration(open_syms) or {}
        for _dim, buckets in dupes.items():
            for bucket, syms in buckets.items():
                for s in syms:
                    out.setdefault(s, (bucket, [x for x in syms if x != s]))
    except Exception:
        return {}
    return out


def build_downsize_candidates(conn, regime_assessment: dict,
                              exclude_symbols=None) -> dict:
    """Assemble both downsize groups. `regime_assessment` is the dict from
    regime_health.assess_all (for per-position status + the cascade). Soft-fail."""
    exclude = set(exclude_symbols or [])

    # ── Group A: qualifier DOWNSIZE verdicts (new entries) ──
    a_rows = _qualifier_downsize_rows(conn)

    # ── Group B: open positions to trim ──
    pos_by_fam = (regime_assessment or {}).get("positions", {}) or {}
    open_syms = sorted({p["symbol"] for plist in pos_by_fam.values() for p in plist})
    breadth_red = _breadth_red(conn)
    cascade = (regime_assessment or {}).get("cascade") or {}
    cascade_firing = cascade.get("alert_state") == "CASCADE"
    n_red_rings = cascade.get("n_red_today", 0)
    macro_map = _macro_cluster_map(open_syms)

    b_rows = []
    seen = set()
    for plist in pos_by_fam.values():
        for p in plist:
            sym = p.get("symbol")
            struct = (p.get("structure") or "").lower()
            tid = p.get("trade_id")
            if struct not in LONG_DELTA or sym in exclude:
                continue
            reasons = []
            if breadth_red:
                reasons.append("breadth 🔴 (narrowing + extended) — elevated drawdown risk")
            if cascade_firing:
                reasons.append(f"cascade 🚨 ({n_red_rings} rings 🔴) — book-wide exit posture")
            # the position's OWN name going against it — key on name_status, not
            # combined_status (combined also turns 🔴 when the entry GATE is closed,
            # which is not the position-level adverse signal we want to flag here).
            if p.get("name_status") == "🔴":
                reasons.append("name 🔴 — below its 200-DMA (going against the position)")
            if sym in macro_map:
                bucket, others = macro_map[sym]
                if others:
                    reasons.append(f"macro cluster {bucket} — correlated with "
                                   f"{', '.join(others)}")
            if reasons and tid not in seen:
                seen.add(tid)
                b_rows.append((sym, struct, reasons))

    if not a_rows and not b_rows:
        return {"text": ""}

    lines = ["  DOWNSIZE CANDIDATES", "  " + "-" * 68]
    if a_rows:
        lines.append("  New entries — qualifier says half-size:")
        for sym, struct, reason in a_rows:
            lines.append(f"    ↓ {sym:<6} {struct:<16} — {reason}")
    if b_rows:
        if a_rows:
            lines.append("")
        lines.append("  Open positions — elevated risk, consider trimming:")
        for sym, struct, reasons in b_rows:
            lines.append(f"    ▼ {sym:<6} {struct:<16} — {'; '.join(reasons)}")
    return {"text": "\n".join(lines)}
