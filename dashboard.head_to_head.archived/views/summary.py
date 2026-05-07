"""Executive summary viewer — renders MaxPain_Project/docs/EXECUTIVE_SUMMARY.md."""
from __future__ import annotations

from pathlib import Path

import streamlit as st


_SUMMARY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "docs" / "EXECUTIVE_SUMMARY.md"
)


def render_summary() -> None:
    if not _SUMMARY_PATH.exists():
        st.error(f"Executive summary not found at {_SUMMARY_PATH}")
        return
    st.markdown(_SUMMARY_PATH.read_text())
