"""Cycle scorecard — per-opex head-to-head totals strip.

Companion to the comparison grid. Answers "who won this cycle?" at a glance.

Unit note: `orig_final_pnl` and `score_final_pnl` are per-contract dollars
as stored in the source tables (not per-share decimals). No multiplier.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st


def render_scorecard(df: pd.DataFrame) -> None:
    st.subheader("Cycle scorecard")
    if df.empty:
        st.info("Nothing to score yet.")
        return

    for opex in sorted(df["opex_date"].unique(), reverse=True):
        sub = df[df["opex_date"] == opex]

        in_orig = sub["orig_types"].notna()
        in_score = sub["score_types"].notna()

        n_both = int((in_orig & in_score).sum())
        n_only_orig = int((in_orig & ~in_score).sum())
        n_only_score = int((~in_orig & in_score).sum())

        orig_pnl = float(sub["orig_final_pnl"].sum())  # sum skips NaN
        score_pnl = float(sub["score_final_pnl"].sum())

        wins = sub["winner"].value_counts().to_dict()

        st.markdown(f"### OpEx {opex}")

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Both covered", n_both)
        c2.metric("Only Original", n_only_orig)
        c3.metric("Only Score", n_only_score)
        c4.metric("Original P&L", f"${orig_pnl:,.2f}")
        c5.metric("Score P&L", f"${score_pnl:,.2f}")

        if n_both > 0:
            st.caption(
                f"Head-to-head wins — "
                f"Original: {wins.get('original', 0)}  ·  "
                f"Score: {wins.get('score', 0)}  ·  "
                f"Ties: {wins.get('tie', 0)}  ·  "
                f"Pending: {wins.get('pending', 0)}"
            )
        elif n_only_score and not n_only_orig:
            st.caption("Score-only cycle — Original book not yet run or closed.")
        elif n_only_orig and not n_only_score:
            st.caption("Original-only cycle.")

        st.divider()
