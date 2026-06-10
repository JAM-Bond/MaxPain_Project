"""Filled Book — the real Schwab order activity (read-only).

Mirrors Schwab at the leg level (order_legs, PK order_id+leg_id) and shows the
positions derived from it. Read-only: nothing here places/modifies/cancels an order.
Populates from the order reconciler (scripts/maintenance/reconcile_orders.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Filled Book — MaxPain", layout="wide",
                   initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, section_header  # noqa: E402
from queries.filled_book import (  # noqa: E402
    order_summary_df, legs_df, reconciled_positions_df,
)

inject_css()
sidebar_banner()
page_header("🧾 Filled Book",
            "real Schwab orders — leg-level mirror + derived positions · read-only")

days = st.slider("Lookback (days)", min_value=7, max_value=365, value=90, step=7)

tab_orders, tab_legs, tab_pos = st.tabs(
    ["Orders (grouped)", "Legs (raw mirror)", "Reconciled positions"])

# ── Orders grouped by spread ─────────────────────────────────────────────────
with tab_orders:
    section_header("Filled orders — one row per spread")
    df = order_summary_df(days)
    if df.empty:
        st.info("No filled orders mirrored yet. Populate with "
                "`python3.11 -m scripts.maintenance.reconcile_orders --apply` "
                "(or `--mirror-only` to mirror without touching the book).")
    else:
        st.caption("net_price: + = net credit taken in, − = net debit paid. "
                   "effect: OPEN / CLOSE / MIXED (roll).")
        st.dataframe(df, use_container_width=True, hide_index=True)

# ── Raw leg mirror ───────────────────────────────────────────────────────────
with tab_legs:
    section_header("Leg-level mirror (order_legs)")
    dl = legs_df(days)
    if dl.empty:
        st.info("No legs mirrored yet.")
    else:
        st.caption("One row per Schwab order leg. Compound key (order_id, leg_id) "
                   "is unique — duplicate-proof. fill_price + fees are per-leg.")
        st.dataframe(dl, use_container_width=True, hide_index=True)

# ── Reconciled positions ─────────────────────────────────────────────────────
with tab_pos:
    section_header("Positions recorded from Schwab orders")
    dp = reconciled_positions_df()
    if dp.empty:
        st.info("No positions recorded from real orders yet (open_order_id is set when "
                "the reconciler records an opening order).")
    else:
        n_open = int((dp["status"] == "open").sum())
        realized = float(dp.loc[dp["status"] == "closed", "final_pnl"].dropna().sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Open", n_open)
        c2.metric("Closed", int((dp["status"] == "closed").sum()))
        c3.metric("Realized P/L (net)", f"${realized:+,.0f}")
        st.dataframe(dp, use_container_width=True, hide_index=True)
