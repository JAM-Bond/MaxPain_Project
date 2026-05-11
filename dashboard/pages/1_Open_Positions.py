"""Positions — Open + Closed tabs.

Open: live close_helper marks (mid/natural/limit/capture %) sorted desc by capture.
Closed: realized P/L, % captured, exit type, off-script flag — filterable by OpEx.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Positions — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box, section_header  # noqa: E402
from queries.positions import (  # noqa: E402
    open_positions_df, closed_positions_df, closed_opex_options,
)


def _label_for_id(df: pd.DataFrame, i: int) -> str:
    row = df[df["id"] == i].iloc[0]
    return (f"{row['Symbol']}  {row['Structure']}  {row['Strikes']}  "
            f"·  {row['OpEx']}  ·  cap {row['Capture %']:+.0f}%")


inject_css()
sidebar_banner()
page_header(
    "📈 Positions",
    "open marks (live) · closed history · sorted by capture % desc",
)

tab_open, tab_closed = st.tabs(["Open", "Closed"])

# ── OPEN TAB ──────────────────────────────────────────────────────────────────
with tab_open:
    df = open_positions_df()
    if df.empty:
        info_box("No open placed positions.", "info")
    else:
        # Top metric row
        n = len(df)
        total_mid = df["P/L @ mid"].sum()
        total_lim = df["P/L @ limit"].sum()
        avg_cap = df["Capture %"].mean()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Open positions", f"{n}")
        m2.metric("Total P/L @ mid", f"${total_mid:+,.0f}")
        m3.metric("Total P/L @ limit", f"${total_lim:+,.0f}")
        m4.metric("Avg capture %", f"{avg_cap:+.0f}%")

        section_header("Open positions — sorted by capture % (best closes first)")

        # Display columns + formatting
        display = df.drop(columns=["id", "_health_detail", "_natural_pnl",
                                   "_stop_trigger", "_stop_dollar", "_pct_to_stop",
                                   "_t21_sort", "_t21_emoji",
                                   "P/L @ mid", "Natural"], errors="ignore").copy()
        st.dataframe(
            display,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Entry":       st.column_config.NumberColumn(format="$%.2f"),
                "Mid close":   st.column_config.NumberColumn(format="$%.2f"),
                "Limit close": st.column_config.NumberColumn(format="$%.2f"),
                "P/L @ limit": st.column_config.NumberColumn(format="$%,.0f"),
                "Capture %":   st.column_config.NumberColumn(format="%.0f%%"),
                "DTE":         st.column_config.NumberColumn(format="%d"),
            },
        )

        section_header("Drill-down")
        sel = st.selectbox(
            "Select a position",
            options=df["id"].tolist(),
            format_func=lambda i: _label_for_id(df, i),
            label_visibility="collapsed",
        )
        if sel is not None:
            row = df[df["id"] == sel].iloc[0]
            d1, d2, d3 = st.columns(3)
            with d1:
                st.markdown("**Entry**")
                st.write(f"Credit/debit: **${row['Entry']:+.2f}**")
                st.write(f"Strikes: **{row['Strikes']}**")
                st.write(f"Qty: **{row['Qty']} contracts**")
                st.write(f"OpEx: **{row['OpEx']}**  (DTE {row['DTE']})")
            with d2:
                st.markdown("**Close marks (live)**")
                st.write(f"Mid: **${row['Mid close']:.2f}**  →  P/L ${df.loc[df['id']==sel, 'P/L @ mid'].iloc[0]:+,.0f}")
                st.write(f"Limit (patient): **${row['Limit close']:.2f}**  →  P/L ${row['P/L @ limit']:+,.0f}")
                st.write(f"Natural worst: ${row['Natural']:.2f}  →  P/L ${row['_natural_pnl']:+,.0f}")
                if row["Liq"]:
                    st.markdown(
                        f"<div class='alert-box'>🚨 {row['Liq']} bid-ask. Limit may sit unfilled.</div>",
                        unsafe_allow_html=True,
                    )
            with d3:
                st.markdown("**Health & stop**")
                st.write(f"{row['Health']}  {row['_health_detail']}")
                st.write(f"Capture: **{row['Capture %']:+.0f}%**")
                if row.get("_t21_emoji"):
                    st.markdown(
                        f"<div class='alert-box'>{row['_t21_emoji']} "
                        f"T-21: <b>{row['T-21']}</b> — close or roll regardless of capture %</div>",
                        unsafe_allow_html=True,
                    )
                if row.get("_stop_trigger") is not None:
                    pct = row["_pct_to_stop"] or 0
                    st.write(
                        f"Stop @ **${row['_stop_trigger']:.2f}**  "
                        f"(${row['_stop_dollar']:+,.0f} max loss · {pct:.0f}% to stop)"
                    )
                else:
                    st.write("Stop: n/a (debit structure — capped at entry debit)")
                st.markdown(
                    f"**Close GTC**:  BUY +{row['Qty']} {row['Symbol']} VERTICAL "
                    f"{row['OpEx']} {row['Strikes']} @ **{row['Limit close']:.2f}** LMT GTC"
                )
                if row.get("_stop_trigger") is not None:
                    st.markdown(
                        f"**Stop GTC**:  BUY +{row['Qty']} {row['Symbol']} VERTICAL "
                        f"{row['OpEx']} {row['Strikes']} "
                        f"STP **{row['_stop_trigger']:.2f}** "
                        f"LMT **{row['_stop_trigger'] + 0.10:.2f}** "
                        f"MARK GTC"
                    )

# ── CLOSED TAB ────────────────────────────────────────────────────────────────
with tab_closed:
    opex_opts = ["All"] + closed_opex_options()
    f1, _ = st.columns([1, 4])
    with f1:
        opex_filter = st.selectbox("OpEx filter", opex_opts, index=0)

    cdf = closed_positions_df(None if opex_filter == "All" else opex_filter)
    if cdf.empty:
        info_box("No closed positions for that filter.", "info")
    else:
        n_c = len(cdf)
        total_realized = cdf["Realized P/L"].sum()
        wins = (cdf["Realized P/L"] > 0).sum()
        win_rate = wins / n_c * 100 if n_c else 0
        avg_cap_c = cdf["% captured"].mean()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Closed trades", f"{n_c}")
        m2.metric("Realized P/L", f"${total_realized:+,.0f}")
        m3.metric("Win rate", f"{win_rate:.0f}%  ({wins}/{n_c})")
        m4.metric("Avg capture %", f"{avg_cap_c:+.0f}%")

        section_header(
            f"Closed positions — {opex_filter}  ·  sorted by capture % desc"
        )

        display_c = cdf.drop(columns=[
            "id", "qualifier_run_date", "target_hit_date",
            "short_strike", "long_strike",
        ], errors="ignore")
        # Reorder columns
        col_order = ["Symbol", "Structure", "Strikes", "Qty", "OpEx",
                     "Entry date", "Exit date", "Days held",
                     "Entry", "Exit", "Realized P/L", "% captured",
                     "Exit type", "Off-script"]
        display_c = display_c[[c for c in col_order if c in display_c.columns]]
        st.dataframe(
            display_c,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Entry":         st.column_config.NumberColumn(format="$%.2f"),
                "Exit":          st.column_config.NumberColumn(format="$%.2f"),
                "Realized P/L":  st.column_config.NumberColumn(format="$%,.0f"),
                "% captured":    st.column_config.NumberColumn(format="%.0f%%"),
                "Days held":     st.column_config.NumberColumn(format="%d"),
                "Off-script":    st.column_config.CheckboxColumn(),
            },
        )

