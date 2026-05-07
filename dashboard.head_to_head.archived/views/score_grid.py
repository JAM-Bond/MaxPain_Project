"""Score book grid — per-trade rows from spread_score_trades."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import JsCode

from ._aggrid_utils import render_aggrid, render_column_defs


_COL_DEFS = """
Primary view shows 9 columns. Select a row to drill into the full detail.

- **symbol / opex_date** — ticker and monthly OpEx
- **spread_type** — `bull_put` / `bear_call`
- **strikes** — `-short/long`; short leg shown negative for clarity
- **status** — `open` or `closed`
- **entry_date / entry_credit** — when entered and credit collected ($)
- **entry_composite** — 7-metric weighted entry score 0–1 (entry floor ≥ 0.45)
- **pnl** — realized if closed, else latest mark-to-market (italic = live MTM)

Drill-down also includes: tier, rank_score, entry_price, entry_iv_rank,
entry_vrp, entry_short_delta, entry_charm_sign, entry_vix, exit_date,
exit_credit, final_pnl, target_hit / target_hit_pnl / target_hit_days_held,
mtm_date / mtm_credit / mtm_pnl / mtm_pnl_pct, short/long_strike, width.
"""


_STATUS_STYLE = JsCode("""
function(params) {
    if (params.value === 'open')   return {backgroundColor: '#fef3c7', color: '#92400e'};
    if (params.value === 'closed') return {backgroundColor: '#e5e7eb', color: '#374151'};
    return {};
}
""")

_PNL_STYLE = JsCode("""
function(params) {
    if (params.data && params.data.pnl_is_live) {
        return {fontStyle: 'italic', color: '#4b5563'};
    }
    return {};
}
""")

_VISIBLE_COLS = (
    "symbol", "opex_date", "spread_type", "strikes",
    "status", "entry_date", "entry_credit", "entry_composite", "pnl",
)

_FLOAT_COLS = (
    "short_strike", "long_strike", "width", "rank_score",
    "entry_price", "entry_credit",
    "entry_composite", "entry_iv_rank", "entry_vrp", "entry_short_delta",
    "entry_vix",
    "exit_credit", "final_pnl", "target_hit_pnl",
    "mtm_credit", "mtm_pnl", "mtm_pnl_pct",
    "pnl",
)

_NARROW_COLS = ("status", "target_hit", "target_hit_days_held")


def render_score_grid(df: pd.DataFrame) -> dict | None:
    render_column_defs(_COL_DEFS)
    if df.empty:
        st.info("No Score book rows yet.")
        return None

    return render_aggrid(
        df,
        key="score_grid",
        float_cols=_FLOAT_COLS,
        narrow_cols=_NARROW_COLS,
        column_config={
            "status": {"cellStyle": _STATUS_STYLE},
            "pnl": {"cellStyle": _PNL_STYLE},
        },
        visible_cols=_VISIBLE_COLS,
        height=380,
    )
