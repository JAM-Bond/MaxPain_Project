"""Analytics — trade ledger queries (per-name, per-structure, exit_type, regime). STUB."""
from __future__ import annotations
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Analytics — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box  # noqa: E402

inject_css()
sidebar_banner()
page_header("📊 Analytics", "trade ledger · per-name · per-structure · exit_type · regime")

info_box(
    "Stub. Will render the 8 queries from <code>lib/trade_analytics.py</code> with "
    "N + adequacy flags. CLI for now: "
    "<code>python3.11 -m lib.trade_analytics</code> from <code>~/MaxPain_Project</code>.",
    "info",
)
