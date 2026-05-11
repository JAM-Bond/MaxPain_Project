"""Analytics — trade-ledger cross-trade queries.

Wraps the 8 queries from `lib/trade_analytics.py` in tabs. Each query
returns N + adequacy flag per cell; the page colors adequacy and lets you
filter by OpEx cycle.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Analytics — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box, section_header  # noqa: E402
from lib.trade_ledger import load_trade_ledger  # noqa: E402
from lib.trade_analytics import (  # noqa: E402
    exit_type_breakdown, per_name_x_structure, qualifier_vs_off_script,
    structure_x_regime, mae_vs_final, regime_transition,
    sizing_audit, earnings_overlap,
)

DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"


_ADEQUACY_EMOJI = {
    "ADEQUATE":    "🟢",
    "DEVELOPING":  "🟡",
    "SUGGESTIVE":  "🟠",
    "PRELIMINARY": "🔴",
}


@st.cache_data(ttl=60)
def _load_ledger():
    with sqlite3.connect(str(DB_PATH)) as conn:
        return load_trade_ledger(conn)


def _format_pnl_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert raw $ values to formatted strings for display."""
    out = df.copy()
    for col in ("mean_pnl", "median_pnl", "total_pnl", "worst", "best", "final_pnl", "mae"):
        if col in out.columns:
            out[col] = out[col].apply(
                lambda v: f"${v:+,.0f}" if pd.notna(v) else "—"
            )
    if "win_rate" in out.columns:
        out["win_rate"] = out["win_rate"].apply(
            lambda v: f"{v*100:.0f}%" if pd.notna(v) and isinstance(v, (int, float)) else v
        )
    if "adequacy" in out.columns:
        out["adequacy"] = out["adequacy"].apply(
            lambda v: f"{_ADEQUACY_EMOJI.get(v, '⚪')} {v}" if pd.notna(v) else "—"
        )
    return out


def _render_query(title: str, caption: str, df: pd.DataFrame):
    section_header(title)
    if caption:
        st.markdown(
            f"<div style='color:#c9d1d9;font-size:12px;margin:-6px 0 8px 0'>{caption}</div>",
            unsafe_allow_html=True,
        )
    if df is None or df.empty:
        info_box("No data — query returned empty.", "info")
        return
    display = _format_pnl_columns(df)
    st.dataframe(display, hide_index=True, use_container_width=True)


inject_css()
sidebar_banner()
page_header(
    "📊 Analytics",
    "trade-ledger cross-trade queries · per-name · per-structure · exit-type · regime",
)

# ── Load ledger ──────────────────────────────────────────────────────────
try:
    ledger = _load_ledger()
except Exception as e:
    info_box(f"Failed to load trade ledger: {e}", "warning")
    st.stop()

if ledger is None or ledger.empty:
    info_box("Trade ledger is empty.", "info")
    st.stop()

# ── Top metric strip ─────────────────────────────────────────────────────
total_n = len(ledger)
closed_n = int(ledger["status"].eq("closed").sum()) if "status" in ledger.columns else 0
placed_closed = ledger[(ledger.get("placed", 0) == 1) & (ledger["status"] == "closed")]
placed_closed_n = len(placed_closed)
total_realized = placed_closed["final_pnl"].sum() if "final_pnl" in placed_closed.columns else 0
win_rate = (
    (placed_closed["final_pnl"] > 0).mean() * 100
    if placed_closed_n else 0
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Trade ledger", f"{total_n}")
m2.metric("Closed", f"{closed_n}")
m3.metric("Placed + closed", f"{placed_closed_n}")
m4.metric("Realized P/L", f"${total_realized:+,.0f}")
m5.metric("Win rate", f"{win_rate:.0f}%")

# ── Sidebar filter: OpEx cycle ───────────────────────────────────────────
opex_options = ["All"]
if "opex_date" in ledger.columns:
    opex_options += sorted(
        [d for d in ledger["opex_date"].dropna().unique() if d],
        reverse=True,
    )
sel_opex = st.sidebar.selectbox("OpEx filter", opex_options, index=0)
if sel_opex != "All":
    ledger_filtered = ledger[ledger["opex_date"] == sel_opex]
else:
    ledger_filtered = ledger

# ── Tabs (one per query) ─────────────────────────────────────────────────
tabs = st.tabs([
    "Exit type",
    "Per-name × structure",
    "Qualifier vs off-script",
    "Structure × regime",
    "MAE vs final",
    "Regime transition",
    "Sizing audit",
    "Earnings overlap",
])

with tabs[0]:
    _render_query(
        "Exit-type breakdown",
        "Discipline audit — which exit rules are firing? "
        "Mix of <code>profit_target / t21_managed / t3_5_window / managed_close / manual_close</code>.",
        exit_type_breakdown(ledger_filtered),
    )

with tabs[1]:
    _render_query(
        "Per-name × structure",
        "Which names actually carry the cohort? Sorted by structure, then mean P/L desc. "
        "PRELIMINARY rows (N=1) are individual trades; ADEQUATE bands need ~30 cycles.",
        per_name_x_structure(ledger_filtered),
    )

with tabs[2]:
    _render_query(
        "Qualifier vs off-script",
        "Does the qualifier discipline pay off? Compares <code>placed=1</code> trades that "
        "had a <code>qualifier_run_date</code> (qualifier-driven) vs trades without (off-script).",
        qualifier_vs_off_script(ledger_filtered),
    )

with tabs[3]:
    _render_query(
        "Structure × regime (at entry)",
        "Win rate of each structure conditioned on the regime label at entry. "
        "Regime labels: <code>stage0 / stage0+BPsig / stage2+IFgate</code> etc.",
        structure_x_regime(ledger_filtered),
    )

with tabs[4]:
    _render_query(
        "MAE vs final P/L",
        "Per-trade table sorted by worst MAE. Answers: <i>would I have held in live?</i> "
        "<code>mae_recovered</code> = how much the trade clawed back from its low.",
        mae_vs_final(ledger_filtered),
    )

with tabs[5]:
    _render_query(
        "Regime transition",
        "Trades whose hold straddled a regime stage transition. Tests whether positions "
        "opened in regime A but closed in regime B perform differently.",
        regime_transition(ledger_filtered),
    )

with tabs[6]:
    _render_query(
        "Sizing audit",
        "Qualifier-prescribed size vs actual contracts traded. Mismatch flags discipline drift "
        "(intended <code>SIZE_DOWNSIZE</code> ignored, intended pause overridden, etc.).",
        sizing_audit(ledger_filtered),
    )

with tabs[7]:
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            df = earnings_overlap(ledger_filtered, conn=conn)
    except Exception as e:
        df = None
        info_box(f"earnings_overlap query failed: {e}", "warning")
    _render_query(
        "Earnings overlap",
        "Trades whose hold-window overlapped an earnings event. Did earnings during the trade "
        "help or hurt? <code>earnings_t1</code> / <code>earnings_t3</code> classifications.",
        df,
    )

# ── Adequacy legend at bottom ────────────────────────────────────────────
section_header("Adequacy bands")
st.markdown(
    "<div style='font-family:IBM Plex Mono,monospace;font-size:12px;color:#c9d1d9;line-height:1.7'>"
    "🔴 <b>PRELIMINARY</b> &nbsp;N &lt; 10 — directional only; never override backtest<br>"
    "🟠 <b>SUGGESTIVE</b> &nbsp;&nbsp;N &lt; 20 — pattern emerging<br>"
    "🟡 <b>DEVELOPING</b> &nbsp;N &lt; 30 — close to actionable<br>"
    "🟢 <b>ADEQUATE</b> &nbsp;&nbsp;&nbsp;N ≥ 30 — directional pattern is real"
    "</div>",
    unsafe_allow_html=True,
)
