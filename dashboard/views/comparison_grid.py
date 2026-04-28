"""Head-to-head summary grid — per (symbol, opex_date), minimal columns.

Driven by comparison_summary.sql. Full-detail join lives in comparison.sql
and is available via loader.load_comparison() if needed.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import JsCode

from ._aggrid_utils import render_aggrid, render_column_defs


_COL_DEFS = """
- **symbol / opex_date** — natural key
- **both_covered** — 1 if both books took a position at this (symbol, opex)
- **orig_types** — comma-separated `spread_type`s in Original book
- **orig_final_pnl** — sum of Original legs' realized P&L ($ per contract)
- **score_types** — comma-separated `spread_type`s in Score book
- **score_final_pnl** — sum of Score trades' realized P&L ($ per contract)
- **winner** — badge classifying the row:
    - **original / score / tie** — head-to-head outcome when both books closed
    - **pending** — both covered, at least one Score trade still open
    - **only_original / only_score** — one book covered this (symbol, opex), the other did not
    - **unknown** — neither book has final P&L yet
"""


_WINNER_STYLE = JsCode("""
function(params) {
    if (!params.value) return {};
    const colors = {
        'original':      {backgroundColor: '#3b82f6', color: 'white'},
        'score':         {backgroundColor: '#10b981', color: 'white'},
        'tie':           {backgroundColor: '#6b7280', color: 'white'},
        'pending':       {backgroundColor: '#f3f4f6', color: '#6b7280'},
        'only_original': {backgroundColor: '#dbeafe', color: '#1e40af'},
        'only_score':    {backgroundColor: '#d1fae5', color: '#065f46'},
        'unknown':       {backgroundColor: '#fee2e2', color: '#991b1b'},
    };
    return colors[params.value] || {};
}
""")


_FLOAT_COLS = ("orig_final_pnl", "score_final_pnl")

_NARROW_COLS = ("both_covered",)


def render_comparison_grid(df: pd.DataFrame) -> dict | None:
    render_column_defs(_COL_DEFS)
    if df.empty:
        st.info("No comparison data yet.")
        return None

    return render_aggrid(
        df,
        key="comparison_grid",
        float_cols=_FLOAT_COLS,
        narrow_cols=_NARROW_COLS,
        column_config={
            "winner": {"cellStyle": _WINNER_STYLE, "pinned": "left", "width": 130},
        },
        height=320,
    )
