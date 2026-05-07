"""Cohorts & Universe — what's in each cohort, gates, walk-forward stats. STUB."""
from __future__ import annotations
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Cohorts — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box  # noqa: E402

inject_css()
sidebar_banner()
page_header("📋 Cohorts & Universe", "bull_put · bear_call · IF · zebra · gates · walk-forward stats")

info_box(
    "Stub. Will surface gate_config.py cohort lists with per-name walk-forward stats "
    "(val_n, val_mean, recommended moneyness/wing), MA-bucket trip flags, and "
    "regime-gate state (bull_put gate, H1 gate, zebra trend).",
    "info",
)
