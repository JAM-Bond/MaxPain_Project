"""Daily Macro Brief — curve + FedWatch + Fed news with optional AI narrative.

Phase 1 of the AI advisory layer per project_ai_advisory_layer_plan.md.
The brief data layer (lib/macro_brief.py) pulls today's snapshot from
Agent_Project's ChromaDB; the AI narrative (lib/ai_macro_brief.py) reads
that bundle through SOUL.md and produces a short interpretive synthesis.

Read-only. Does not influence the qualifier or alert pipeline.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Macro Brief — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box, section_header  # noqa: E402
from lib.macro_brief import build_macro_brief, render_text, render_html  # noqa: E402

inject_css()
sidebar_banner()
page_header(
    "🌐 Daily Macro Brief",
    "Agent_Project ChromaDB · curve · FedWatch · Fed news · AI narrative",
)

# ── Data layer ─────────────────────────────────────────────────────────────
with st.spinner("Reading Agent_Project ChromaDB..."):
    brief = build_macro_brief()

# Top metrics
m1, m2, m3, m4 = st.columns(4)
c = brief["curve"]
if c.get("ok"):
    m1.metric("DGS10", f"{c['dgs10']:.2f}%")
    m2.metric("2s10s spread", f"{c['spread_2s10s']:+.2f}%",
              delta=(c['spread_2s10s'] - c['avg_30d']['spread_2s10s'])
              if c['avg_30d']['spread_2s10s'] is not None else None,
              delta_color="off")
else:
    m1.metric("DGS10", "—")
    m2.metric("2s10s spread", "—")

fw = brief["fedwatch"]
if fw.get("ok") and fw["meetings"]:
    next_m = fw["meetings"][0]
    m3.metric(f"Next FOMC {next_m['meeting_str']}", next_m["most_likely"],
              delta=f"hold {next_m['hold']:.1f}% ({(next_m['hold'] - next_m['prior_hold']):+.1f})",
              delta_color="off")
else:
    m3.metric("Next FOMC", "—")

n = brief["news"]
if n.get("ok"):
    m4.metric(f"Fed news (last {n['days_back']}d)", f"{len(n['items'])}")
else:
    m4.metric("Fed news", "—")

# ── Rendered brief ─────────────────────────────────────────────────────────
section_header("Brief")
st.markdown(render_html(brief), unsafe_allow_html=True)

with st.expander("View text version (for daily alert reference)", expanded=False):
    st.code(render_text(brief))

# ── AI Narrative ───────────────────────────────────────────────────────────
section_header("AI narrative")
st.markdown(
    "<div style='color:#c9d1d9;font-size:12px;margin-bottom:8px'>"
    "Reads the brief above through <code>config/SOUL.md</code> for a 3-5 paragraph synthesis. "
    "Advisory only — never overrides mechanical gates. "
    "Cached by (date, brief_hash) — re-clicking with unchanged data is free."
    "</div>",
    unsafe_allow_html=True,
)

c1, c2 = st.columns([1, 4])
go = c1.button("🧠 Generate AI narrative")
force = c2.checkbox("Force refresh (skip cache)", value=False)

if go:
    from lib.ai_macro_brief import generate_macro_brief_narrative
    with st.spinner("Calling Claude (Opus 4.7) — typically 10-30 seconds..."):
        try:
            result = generate_macro_brief_narrative(force_refresh=force)
        except Exception as e:
            st.error(f"AI narrative failed: {type(e).__name__}: {e}")
            st.stop()

    badge = "🟢 cache hit" if result.cached else "🆕 fresh call"
    st.caption(
        f"{badge}  ·  in={result.input_tokens:,} out={result.output_tokens:,} "
        f"cache_read={result.cache_read_tokens:,}  "
        f"elapsed={result.elapsed_seconds:.1f}s  "
        f"model={result.model}  ·  generated {result.generated_at}"
    )
    st.markdown(result.response_text)
