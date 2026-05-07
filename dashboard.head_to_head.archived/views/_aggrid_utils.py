"""Shared AgGrid helpers. Imported by every grid view."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode


def render_column_defs(md: str, *, expanded: bool = False) -> None:
    """Render a collapsible 'Column definitions' expander above a grid."""
    with st.expander("Column definitions", expanded=expanded):
        st.markdown(md)


NUM_FMT = JsCode("""
function(p) {
    if (p.value == null || p.value === '') return '';
    return Number(p.value).toFixed(2);
}
""")


def render_aggrid(
    df: pd.DataFrame,
    *,
    key: str,
    float_cols: tuple[str, ...] = (),
    narrow_cols: tuple[str, ...] = (),
    column_config: dict | None = None,
    visible_cols: tuple[str, ...] | None = None,
    height: int = 350,
) -> dict | None:
    """Render a standardized single-selection AgGrid.

    When visible_cols is provided, only those columns are shown; the rest are
    hidden but remain in the selected-row payload so a drill-down can use them.
    Returns the selected row as a dict, or None.
    """
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(filter=True, sortable=True, resizable=True)
    gb.configure_selection("single", use_checkbox=False)

    if visible_cols is not None:
        visible = set(visible_cols)
        for col in df.columns:
            if col not in visible:
                gb.configure_column(col, hide=True)

    if "symbol" in df.columns:
        gb.configure_column("symbol", pinned="left", width=80)
    if "opex_date" in df.columns:
        gb.configure_column("opex_date", pinned="left", width=110)

    for col in narrow_cols:
        if col in df.columns:
            gb.configure_column(col, width=90)

    for col in float_cols:
        if col in df.columns:
            gb.configure_column(
                col,
                type=["numericColumn", "numberColumnFilter"],
                valueFormatter=NUM_FMT,
            )

    if column_config:
        for col, cfg in column_config.items():
            if col in df.columns:
                gb.configure_column(col, **cfg)

    grid = AgGrid(
        df,
        gridOptions=gb.build(),
        allow_unsafe_jscode=True,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        fit_columns_on_grid_load=False,
        height=height,
        theme="streamlit",
        key=key,
    )

    sel = grid.get("selected_rows")
    if sel is None:
        return None
    if hasattr(sel, "empty"):
        return None if sel.empty else sel.iloc[0].to_dict()
    return sel[0] if len(sel) else None
