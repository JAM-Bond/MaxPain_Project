"""MaxPain Dashboard — Landing page.

Visual style: GitHub dark + IBM Plex (matches Agent_Project 8501).
Run: streamlit run dashboard/app.py --server.port 8502

Read-only against ~/Metal_Project/data/shared/metal_project.db.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# Make sibling modules importable
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))  # for scripts.monitor.*

st.set_page_config(page_title="MaxPain Dashboard", page_icon="🎯", layout="wide",
                   initial_sidebar_state="expanded")

from components.style import (  # noqa: E402
    inject_css, sidebar_banner, page_header, section_header, delta_card, info_box,
)
from queries.landing import (  # noqa: E402
    may_opex_running_pnl, open_positions_count, todays_actionable,
    latest_qualifier_run, cascade_state, next_opex, days_to_next_opex,
    open_book_close_marks, top_close_candidates,
)

inject_css()
sidebar_banner()
st.sidebar.markdown(
    "<div style='font-family:IBM Plex Mono,monospace;font-size:0.7rem;color:#adbac7;"
    "padding:0 0.5rem'>Read-only against<br/><code>metal_project.db</code></div>",
    unsafe_allow_html=True,
)

page_header(
    "🎯 MaxPain Dashboard",
    "open book · cascade · today's actionable · regime gates",
)

# ── Top stat strip ────────────────────────────────────────────────────────────
may_pnl, may_n = may_opex_running_pnl()
open_n = open_positions_count()
close_marks = open_book_close_marks()
mid_pnl = close_marks.get("total_mid_pnl", 0)
limit_pnl = close_marks.get("total_limit_pnl", 0)
casc = cascade_state()
casc_label = "—"
casc_tone = "neutral"
if casc:
    composite = (casc.get("composite") or "").strip()
    casc_label = composite or "—"
    casc_tone = {"GREEN": "up", "YELLOW": "warn", "RED": "down"}.get(composite.upper(), "neutral")
qual = todays_actionable()
qual_count = len(qual)
opex_dt = next_opex()
dte = days_to_next_opex()

cols = st.columns(5)
cols[0].markdown(delta_card(
    "MAY OpEx Realized", f"${may_pnl:+,.0f}",
    "up" if may_pnl > 0 else "down" if may_pnl < 0 else "neutral",
), unsafe_allow_html=True)
cols[1].markdown(delta_card(
    "Open Book @ Mid", f"${mid_pnl:+,.0f}",
    "up" if mid_pnl > 0 else "down" if mid_pnl < 0 else "neutral",
), unsafe_allow_html=True)
cols[2].markdown(delta_card(
    "Open Book @ Limit", f"${limit_pnl:+,.0f}",
    "up" if limit_pnl > 0 else "down" if limit_pnl < 0 else "neutral",
), unsafe_allow_html=True)
cols[3].markdown(delta_card(
    "Cascade State", casc_label, casc_tone,
), unsafe_allow_html=True)
cols[4].markdown(delta_card(
    f"DTE → {opex_dt.isoformat()}", f"{dte}d",
    "info",
), unsafe_allow_html=True)

# ── Mid section: cascade detail | top closes | actionable ────────────────────
left, mid, right = st.columns([1, 1, 1])

with left:
    section_header("Cascade rings")
    if casc:
        ring_data = [
            ("AI",  casc.get("ai_state"),  casc.get("ai_detail", "")),
            ("QQQ", casc.get("qqq_state"), casc.get("qqq_detail", "")),
            ("SPY", casc.get("spy_state"), casc.get("spy_detail", "")),
        ]
        for name, state, detail in ring_data:
            state_str = (state or "—").upper()
            color = {"GREEN": "#3fb950", "YELLOW": "#e3b341", "RED": "#f85149"}.get(state_str, "#adbac7")
            emoji = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(state_str, "⚪")
            st.markdown(
                f"""<div class="ring-card">
  <div class="ring-name">{name} ring</div>
  <div class="ring-state" style="color:{color}">{emoji} {state_str}</div>
  <div class="ring-detail">{detail or '—'}</div>
</div>""",
                unsafe_allow_html=True,
            )
    else:
        info_box("Cascade state unavailable — regime_state table missing or empty.", "warn")

with mid:
    section_header("Top closes by capture")
    rows = close_marks.get("rows", [])
    if not rows:
        info_box("No open positions.", "info")
    else:
        top = top_close_candidates(rows, n=5)
        for r in top:
            cap = r.capture_at_mid * 100
            tone = "up" if cap >= 50 else ("warn" if cap >= 25 else ("down" if cap < 0 else "neutral"))
            label = f"{r.symbol} {r.spread_type} · {r.short_strike:g}/{r.long_strike:g}"
            wide_tag = "  ⚠ WIDE" if r.wide_warning else ""
            st.markdown(delta_card(
                label,
                f"{cap:+.0f}% · ${r.pnl_at_limit:+,.0f}@lim{wide_tag}",
                tone,
            ), unsafe_allow_html=True)

with right:
    section_header("Today's actionable")
    run_date = latest_qualifier_run()
    if not qual:
        info_box(
            f"No GO/DOWNSIZE rows at days_until ≤ 1 in latest qualifier run "
            f"(run_date {run_date or '—'}).",
            "info",
        )
    else:
        for q in qual:
            verdict_tone = "up" if q["verdict"] == "GO" else "warn"
            st.markdown(delta_card(
                f"{q['symbol']} {q['structure']}  ·  {q['opex']}",
                f"{q['verdict']}  ·  {q.get('reason', '')[:60]}",
                verdict_tone,
            ), unsafe_allow_html=True)

st.markdown("<hr/>", unsafe_allow_html=True)

# ── Footer / context ─────────────────────────────────────────────────────────
section_header("Context")
ctx_cols = st.columns(3)
ctx_cols[0].markdown(delta_card(
    "Open positions", f"{open_n}", "info",
), unsafe_allow_html=True)
ctx_cols[1].markdown(delta_card(
    "MAY closes", f"{may_n}", "info",
), unsafe_allow_html=True)
ctx_cols[2].markdown(delta_card(
    "Latest qualifier", run_date or "—", "info",
), unsafe_allow_html=True)

if close_marks.get("errors"):
    info_box(
        "close_helper errors: " + " · ".join(close_marks["errors"][:5]),
        "warn",
    )

st.caption(
    f"Last refresh: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ET  ·  "
    f"Data: spread_score_trades, cycle_qualifier_runs, regime_state, live Schwab chains via close_helper"
)
