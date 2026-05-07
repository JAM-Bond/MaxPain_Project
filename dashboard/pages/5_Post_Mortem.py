"""Post-Mortem — single page per OpEx with realized vs predicted comparison. STUB."""
from __future__ import annotations
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Post-Mortem — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box  # noqa: E402

inject_css()
sidebar_banner()
page_header("📝 Post-Mortem", "per-OpEx · realized vs predicted · qualifier outcome link")

info_box(
    "Stub. Will surface <code>cycle_postmortem_qualifier</code> output by OpEx, "
    "with the qualifier_run_date link from the trade ledger and signal-validation "
    "v2 scorecard rendering.",
    "info",
)
