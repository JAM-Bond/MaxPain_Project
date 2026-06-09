"""Auto-Promotion — nightly cohort management, made visible.

Surfaces the `cohort_changes` table written by the nightly auto-promotion driver
(`scripts/maintenance/auto_promotion_nightly.py`): what was promoted/demoted,
what the safety brake halted, per-cohort churn over time, per-ticker history, and
whether applied promotions actually led to placed trades + realized P/L.

Without this the pipeline runs invisibly except for the nightly email.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Auto-Promotion — MaxPain", layout="wide",
                   initial_sidebar_state="expanded")

from components.style import (  # noqa: E402
    inject_css, sidebar_banner, page_header, info_box, section_header,
)
from queries.auto_promotion import (  # noqa: E402
    has_data, latest_run_date, run_summary, per_structure_breakdown,
    recent_changes, cohort_net_change, ticker_timeline, all_tickers,
    halted_log, promote_outcomes,
)

inject_css()
sidebar_banner()
page_header(
    "🧬 Auto-Promotion",
    "nightly cohort promote/demote · safety-brake halts · churn · promotion→trade outcomes",
)

if not has_data():
    info_box("No cohort_changes rows yet — the auto-promotion driver hasn't "
             "written any nightly results to this DB.", kind="info")
    st.stop()

latest = latest_run_date()
summ = run_summary(latest)

# ── Top metric strip — latest nightly run ────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("Latest run", latest)
m2.metric("Promoted (applied)", summ["promoted"])
m3.metric("Demoted (applied)", summ["demoted"])
m4.metric("Proposed but halted", summ["halted"])

info_box(
    "Most proposals are <b>halted</b> by the nightly safety brake (applied=0) — "
    "that's expected, it's the conservative default. 'Applied' rows are the ones "
    "that actually changed the live cohort. Promote→trade outcomes below close the "
    "loop to realized P/L.",
    kind="info",
)
if summ["halt_reasons"]:
    st.caption("Halt reason(s) on the latest run: " + " · ".join(summ["halt_reasons"]))

# ── Latest nightly result, per structure ─────────────────────────────────
section_header("Latest nightly result — by structure")
bd = per_structure_breakdown(latest)
if bd.empty:
    st.caption("No changes on the latest run.")
else:
    st.dataframe(bd, use_container_width=True, hide_index=True)

# ── Cohort net change over time ──────────────────────────────────────────
section_header("Cohort net change over time (applied only, cumulative)")
st.caption("Cumulative net membership change per structure (PROMOTE +1, "
           "DEMOTE −1). Direction/churn, not absolute roster size.")
net = cohort_net_change()
if net.empty:
    st.caption("No applied changes yet.")
else:
    st.line_chart(net, use_container_width=True)

# ── Per-ticker history ───────────────────────────────────────────────────
section_header("Per-ticker history")
tickers = all_tickers()
pick = st.selectbox("Ticker", options=tickers, index=0 if tickers else None)
if pick:
    tl = ticker_timeline(pick)
    if tl.empty:
        st.caption("No changes recorded for this ticker.")
    else:
        st.dataframe(tl, use_container_width=True, hide_index=True)

# ── Promotion → trade outcomes ───────────────────────────────────────────
section_header("Promotion → trade outcomes")
st.caption("For each applied PROMOTE: did a matching trade get placed afterward, "
           "and what was the realized P/L on closed ones?")
out = promote_outcomes()
if out.empty:
    st.caption("No applied promotions yet.")
else:
    placed = out[out["trades_after"] > 0]
    o1, o2, o3 = st.columns(3)
    o1.metric("Applied promotes", len(out))
    o2.metric("…that saw a trade", len(placed))
    realized_total = out["realized_pnl"].dropna().sum()
    o3.metric("Realized P/L (closed)", f"${realized_total:,.0f}")
    st.dataframe(
        out.style.format({"realized_pnl": lambda v: "—" if pd.isna(v) else f"${v:,.0f}"}),
        use_container_width=True, hide_index=True,
    )

# ── Halted-night log ─────────────────────────────────────────────────────
section_header("Safety-brake halt log")
hl = halted_log()
if hl.empty:
    st.caption("No halts recorded.")
else:
    st.dataframe(hl, use_container_width=True, hide_index=True)

# ── Recent changes (raw feed) ────────────────────────────────────────────
section_header("Recent changes (raw feed)")
rc = recent_changes(150)
st.dataframe(rc, use_container_width=True, hide_index=True)
