"""MaxPain head-to-head dashboard — port 8503.

Three-grid layout:
  top-left    Original book (spread_cycle_summary)
  top-right   Score book    (spread_score_trades)
  bottom      Head-to-head  (who won per symbol-cycle)

Reads: ~/Metal_Project/data/shared/metal_project.db  (read-only)
Run:   streamlit run dashboard/app.py --server.port 8503
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

from queries.loader import (
    load_comparison_summary,
    load_original_book,
    load_score_book,
)
from views.comparison_grid import render_comparison_grid
from views.original_grid import render_original_grid
from views.score_grid import render_score_grid
from views.scorecard import render_scorecard
from views.summary import render_summary

st.set_page_config(page_title="MaxPain — Head-to-Head", layout="wide")
st.title("MaxPain — Head-to-Head")
st.caption(
    "Bake-off viewer: Metal spread_evaluator (Original) vs "
    "spread_score_tracker (Score). Read-only against metal_project.db."
)

df_original = load_original_book()
df_score = load_score_book()
df_comparison = load_comparison_summary()

tab_main, tab_score, tab_summary = st.tabs(["Books", "Scorecard", "Executive Summary"])

with tab_main:
    st.subheader(f"Original book  ·  {len(df_original)} legs")
    sel_original = render_original_grid(df_original)

    st.divider()
    st.subheader(f"Score book  ·  {len(df_score)} trades")
    sel_score = render_score_grid(df_score)

    st.divider()
    st.subheader(f"Final results  ·  {len(df_comparison)} symbol-cycles")
    sel_comparison = render_comparison_grid(df_comparison)

    # Drill-down for whichever row was most recently selected
    latest_sel = sel_comparison or sel_score or sel_original
    if latest_sel:
        import pandas as pd

        label_parts = [latest_sel.get("symbol", ""), latest_sel.get("opex_date", "")]
        if "spread_type" in latest_sel:
            label_parts.append(latest_sel["spread_type"])
        label = "  ·  ".join(str(p) for p in label_parts if p)

        hidden = {"pnl_is_live"}
        rows = [
            (k, v) for k, v in latest_sel.items()
            if k not in hidden and v not in (None, "") and not (isinstance(v, float) and pd.isna(v))
        ]
        detail = pd.DataFrame(rows, columns=["field", "value"])

        with st.expander(f"Selected: {label}  —  full detail", expanded=True):
            st.dataframe(detail, hide_index=True, use_container_width=True)

with tab_score:
    render_scorecard(df_comparison)

with tab_summary:
    render_summary()
