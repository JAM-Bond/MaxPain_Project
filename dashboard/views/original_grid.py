"""Original book grid — per-leg closed trades from spread_cycle_summary."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import JsCode

from ._aggrid_utils import render_aggrid, render_column_defs


_COL_DEFS = """
Primary view shows 9 columns. Select a row to drill into the full detail.

- **symbol / opex_date** — ticker and monthly OpEx (3rd Friday)
- **spread_type** — `bull_put` or `bear_call`
- **strikes** — `-short/long`; short leg shown negative for clarity
- **entry_credit** — credit collected ($ per share; ×100 per contract)
- **final_pnl / final_pnl_pct** — realized P&L per contract ($ and % of max profit)
- **won** — 1 if profitable, else 0
- **mc_prob_profit** — pre-trade MC P(profit); compare to realized to eyeball calibration

Drill-down also includes: tier, rank_score, final_mark, short/long_strike,
width, mc_expected_pnl, mc_sharpe, mc_max_loss, liquidity_flag,
strike_spacing, mark_date.
"""


_WON_STYLE = JsCode("""
function(params) {
    if (params.value === 1) return {backgroundColor: '#d1fae5', color: '#065f46'};
    if (params.value === 0) return {backgroundColor: '#fee2e2', color: '#991b1b'};
    return {};
}
""")

_VISIBLE_COLS = (
    "symbol", "opex_date", "spread_type", "strikes",
    "entry_credit", "final_pnl", "final_pnl_pct", "won", "mc_prob_profit",
)

_FLOAT_COLS = (
    "short_strike", "long_strike", "width", "rank_score",
    "entry_credit", "final_mark", "final_pnl", "final_pnl_pct",
    "mc_prob_profit", "mc_expected_pnl", "mc_sharpe", "mc_max_loss",
    "strike_spacing",
)

_NARROW_COLS = ("won", "liquidity_flag")


def render_original_grid(df: pd.DataFrame) -> dict | None:
    render_column_defs(_COL_DEFS)
    if df.empty:
        st.info("No Original book rows yet — no cycle closed.")
        return None

    return render_aggrid(
        df,
        key="original_grid",
        float_cols=_FLOAT_COLS,
        narrow_cols=_NARROW_COLS,
        column_config={"won": {"cellStyle": _WON_STYLE}},
        visible_cols=_VISIBLE_COLS,
        height=380,
    )
