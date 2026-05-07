"""Bond Portfolio — Agent_Project CDs/T-Bills/Munis (read-only). STUB."""
from __future__ import annotations
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Bond Portfolio — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box  # noqa: E402

inject_css()
sidebar_banner()
page_header("🏦 Bond Portfolio", "CDs · T-Bills · munis · YTD revenue")

info_box(
    "Stub. Will read Agent_Project's ChromaDB collections "
    "(<code>cd_portfolio</code>, <code>tbill_portfolio</code>, etc.) and surface "
    "active holdings + maturity timeline + YTD revenue. Per the design memo, "
    "MaxPain reads — Agent_Project keeps scraping. Active dashboard at port 8501.",
    "info",
)
