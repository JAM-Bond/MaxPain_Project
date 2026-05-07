"""Daily Alert — read-only mirror of the 4:45 PM cron output. STUB."""
from __future__ import annotations
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Daily Alert — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box  # noqa: E402

inject_css()
sidebar_banner()
page_header("📬 Daily Alert", "rendered alert mirror · history · search")

info_box(
    "Stub. Will tail <code>~/MaxPain_Project/logs/daily_alert_cron.log</code>, "
    "render the latest alert with section nav, plus a calendar grid of past alerts.",
    "info",
)
