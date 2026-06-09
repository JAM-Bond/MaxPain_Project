"""Cohorts & Universe — what's in each cohort + walk-forward recommendations.

Reads cohort lists from `scripts.qualifier.gate_config` and joins with
per-ticker recommendation parquets in `data/profile/`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Cohorts — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box, section_header  # noqa: E402
from queries.cohorts import (  # noqa: E402
    bull_put_cohort_df, bear_call_cohort_df, inverted_fly_cohort_df,
    zebra_cohort_df, earnings_cohort_df, gate_constants,
)


inject_css()
sidebar_banner()
page_header(
    "📋 Cohorts & Universe",
    "bull_put · bear_call · inverted_fly · zebra · earnings · with walk-forward stats",
)

bp = bull_put_cohort_df()
bc = bear_call_cohort_df()
ifly = inverted_fly_cohort_df()
zb = zebra_cohort_df()
ern = earnings_cohort_df()
G = gate_constants()

# ── Top metric strip (cohort sizes) ──────────────────────────────────────
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Bull put", f"{len(bp)}")
m2.metric("Bear call", f"{len(bc)}")
m3.metric("Inverted fly", f"{len(ifly)}")
m4.metric("Zebra", f"{len(zb)}")
m5.metric("Earnings", f"{len(ern)}")

# ── Gates / constants strip ──────────────────────────────────────────────
section_header("Gates & constants")
g1, g2, g3, g4 = st.columns(4)
with g1:
    st.markdown(
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:13px;color:#e6edf3'>"
        f"<b>MIN_CREDIT_WIDTH</b><br>"
        f"<span style='color:#79c0ff'>{G['MIN_CREDIT_WIDTH']:.2f}</span> "
        f"<span style='color:#8b949e'>· min credit / wing</span></div>",
        unsafe_allow_html=True,
    )
with g2:
    st.markdown(
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:13px;color:#e6edf3'>"
        f"<b>BULL_PUT_BELOW_MA</b><br>"
        f"<span style='color:#f0c674'>{G['BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD']*100:+.0f}%</span> "
        f"<span style='color:#8b949e'>· DOWNSIZE if spot &lt; 200dma by this</span></div>",
        unsafe_allow_html=True,
    )
with g3:
    st.markdown(
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:13px;color:#e6edf3'>"
        f"<b>MAX_SPOT_INVERTED_FLY</b><br>"
        f"<span style='color:#79c0ff'>${G['MAX_SPOT_INVERTED_FLY']:.0f}</span> "
        f"<span style='color:#8b949e'>· IF cap on underlying price</span></div>",
        unsafe_allow_html=True,
    )
with g4:
    st.markdown(
        f"<div style='font-family:IBM Plex Mono,monospace;font-size:13px;color:#e6edf3'>"
        f"<b>ZEBRA spot cap</b><br>"
        f"<span style='color:#79c0ff'>none</span> "
        f"<span style='color:#8b949e'>· dropped 2026-06-09; overlay-protected, discretion-sized</span></div>",
        unsafe_allow_html=True,
    )

# ── Tabs per structure ───────────────────────────────────────────────────
tabs = st.tabs([
    f"Bull put ({len(bp)})",
    f"Bear call ({len(bc)})",
    f"Inverted fly ({len(ifly)})",
    f"Zebra ({len(zb)})",
    f"Earnings ({len(ern)})",
])

def _coverage_caption(df: pd.DataFrame) -> str:
    if df.empty or "val n" not in df.columns:
        return ""
    have = df["val n"].notna().sum()
    return (f"Walk-forward coverage: <b>{have}/{len(df)}</b> tickers have validated "
            f"per-ticker recommendations (NULL = use default).")


with tabs[0]:
    st.markdown(
        f"<div style='color:#c9d1d9;font-size:12px;margin-bottom:6px'>"
        f"45-DTE entry window · MIN_CREDIT_WIDTH = {G['MIN_CREDIT_WIDTH']:.2f} · "
        f"DOWNSIZE if spot &lt; 200-DMA by {G['BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD']*100:+.0f}%"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:#8b949e;font-size:12px;margin-bottom:10px'>{_coverage_caption(bp)}</div>",
        unsafe_allow_html=True,
    )
    st.dataframe(
        bp, hide_index=True, use_container_width=True,
        column_config={
            "val n": st.column_config.NumberColumn(format="%d"),
            "val p": st.column_config.NumberColumn(format="%.4f"),
        },
    )

with tabs[1]:
    st.markdown(
        f"<div style='color:#c9d1d9;font-size:12px;margin-bottom:6px'>"
        f"45-DTE · gated by H1 (only fires when SPY &lt; 200-DMA &amp; IVR &gt; 0.5)"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:#8b949e;font-size:12px;margin-bottom:10px'>{_coverage_caption(bc)}</div>",
        unsafe_allow_html=True,
    )
    st.dataframe(
        bc, hide_index=True, use_container_width=True,
        column_config={
            "val n": st.column_config.NumberColumn(format="%d"),
            "val p": st.column_config.NumberColumn(format="%.4f"),
        },
    )

with tabs[2]:
    st.markdown(
        f"<div style='color:#c9d1d9;font-size:12px;margin-bottom:6px'>"
        f"45-DTE · spot cap ${G['MAX_SPOT_INVERTED_FLY']:.0f} · "
        f"variant = wing width (narrow_2pct / medium_5pct / wide_10pct)"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:#8b949e;font-size:12px;margin-bottom:10px'>{_coverage_caption(ifly)}</div>",
        unsafe_allow_html=True,
    )
    st.dataframe(
        ifly, hide_index=True, use_container_width=True,
        column_config={
            "val n": st.column_config.NumberColumn(format="%d"),
            "val p": st.column_config.NumberColumn(format="%.4f"),
        },
    )

with tabs[3]:
    st.markdown(
        f"<div style='color:#c9d1d9;font-size:12px;margin-bottom:6px'>"
        f"75-DTE · no spot cap (overlay-protected, discretion-sized) · "
        f"trend gate: spot &gt; 200-DMA (252-day lookback)"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='color:#8b949e;font-size:12px;margin-bottom:10px'>"
        f"Tier 1 = full size · Tier 2 = half size · TastyTrade 21-DTE roll cue applies."
        f"</div>",
        unsafe_allow_html=True,
    )
    st.dataframe(zb, hide_index=True, use_container_width=True)

with tabs[4]:
    if ern.empty:
        info_box("No earnings cohort entries configured.", "info")
    else:
        st.markdown(
            f"<div style='color:#c9d1d9;font-size:12px;margin-bottom:10px'>"
            f"Earnings T1 = trade day-before · T3 = 3 days before · "
            f"bias direction set by earnings_bias_per_ticker study."
            f"</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(ern, hide_index=True, use_container_width=True)
