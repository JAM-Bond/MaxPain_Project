"""Post-Mortem — full-cycle synthesis through the SOUL framework.

Single button push: bundle every cycle artifact, send through the
SOUL.md system prompt, render Claude's interpretation. Cached by
(opex, bundle_hash) so re-clicking the same cycle doesn't re-charge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Post-Mortem — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box, section_header  # noqa: E402
from queries.postmortem_bundle import list_available_opex, compose_bundle  # noqa: E402


inject_css()
sidebar_banner()
page_header(
    "🧠 Post-Mortem",
    "full-cycle synthesis · bundle · SOUL-framed interpretation",
)

opex_options = list_available_opex()
if not opex_options:
    info_box(
        "No closed cycles in the trade ledger yet. The post-mortem becomes "
        "available after the first cycle's trades are closed and recorded.",
        "info",
    )
    st.stop()

# ── Cycle picker ─────────────────────────────────────────────────────────
c_pick, c_info = st.columns([1, 3])
with c_pick:
    sel_opex = st.selectbox(
        "OpEx cycle",
        options=opex_options,
        index=0,
        help="Defaults to most recent cycle with closed trades",
    )
with c_info:
    st.markdown(
        f"<div style='padding-top:28px;color:#c9d1d9;font-size:12px'>"
        f"Available cycles with closed trades: <b>{len(opex_options)}</b>"
        f"</div>",
        unsafe_allow_html=True,
    )

# ── Bundle preview ───────────────────────────────────────────────────────
section_header("Data bundle preview")

with st.spinner("Composing bundle..."):
    bundle_text, bundle_meta = compose_bundle(sel_opex)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Closed trades", f"{bundle_meta.get('n_closed', 0)}")
m2.metric("Realized P/L", f"${bundle_meta.get('total_pnl', 0):+,.0f}")
m3.metric("Bundle size", f"{bundle_meta.get('char_count', 0):,} chars")
m4.metric("≈ tokens", f"{bundle_meta.get('approx_tokens', 0):,}")

with st.expander("View bundle (markdown)", expanded=False):
    st.code(bundle_text, language="markdown")

# ── Generate button ──────────────────────────────────────────────────────
section_header("AI interpretation")

st.markdown(
    "<div style='color:#c9d1d9;font-size:12px;margin-bottom:8px'>"
    "Reads the bundle through <code>config/SOUL.md</code> "
    "(<i>discipline scoreboard, not market predictor</i>). "
    "Cached by bundle hash — re-clicking does not re-charge unless data has changed."
    "</div>",
    unsafe_allow_html=True,
)

bcol1, bcol2 = st.columns([1, 6])
with bcol1:
    generate = st.button("🧠  Generate Post-Mortem", type="primary", use_container_width=True)
with bcol2:
    force_refresh = st.checkbox(
        "Force refresh (re-charges API)",
        value=False,
        help="Bypass cache and re-run even if bundle is unchanged",
    )

if generate:
    try:
        from lib.ai_advisor import generate_postmortem
    except Exception as e:
        info_box(f"AI advisor module load failed: {e}", "warning")
        st.stop()

    with st.spinner("Reading bundle through SOUL.md and generating synthesis..."):
        try:
            result = generate_postmortem(sel_opex, bundle_text, force_refresh=force_refresh)
        except Exception as e:
            info_box(f"Advisor call failed: {type(e).__name__}: {e}", "warning")
            st.stop()

    cache_label = "🟢 Cached" if result.cached else "🆕 Fresh"
    st.markdown(
        f"<div style='padding:8px 12px;background:#1c2128;border-left:4px solid #79c0ff;"
        f"margin-bottom:14px;font-size:12px;color:#c9d1d9'>"
        f"<b>{cache_label}</b>  ·  Model: <code>{result.model}</code>  ·  "
        f"Generated: {result.generated_at}  ·  "
        f"Elapsed: {result.elapsed_seconds:.1f}s"
        f"<br>Tokens — input: {result.input_tokens:,}  ·  output: {result.output_tokens:,}  ·  "
        f"cache read: {result.cache_read_tokens:,}  ·  cache write: {result.cache_creation_tokens:,}"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown(result.response_text)

else:
    info_box(
        "Click <b>Generate Post-Mortem</b> to produce the synthesis. "
        "The first run for a given bundle hits the API; subsequent runs return "
        "the cached response instantly.",
        "info",
    )
