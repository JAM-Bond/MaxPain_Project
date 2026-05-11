"""Daily Alert — browseable archive of every 4:45 PM cron run.

Each run is one row in `daily_alert_runs` (text + html bodies + severity).
Calendar picker queries by date; gracefully handles future dates (alert
not yet created) and past dates with no archived alert.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Daily Alert — MaxPain", layout="wide", initial_sidebar_state="expanded")

from components.style import inject_css, sidebar_banner, page_header, info_box, section_header  # noqa: E402
from queries.daily_alert import list_runs, get_run  # noqa: E402


_SEVERITY_COLOR = {
    "RED":    "#ff6a69",
    "YELLOW": "#f0c674",
    "ACTION": "#79c0ff",
    "INFO":   "#adbac7",
}


inject_css()
sidebar_banner()
page_header(
    "📬 Daily Alert",
    "browseable archive of 4:45 PM cron runs · sorted newest first",
)

runs = list_runs()
if runs.empty:
    info_box(
        "No archived runs yet. The first row will land after the next 4:45 PM "
        "<code>daily_alert</code> cron run (or after a manual <code>python3.11 -m "
        "scripts.monitor.daily_alert --no-email</code>).",
        "info",
    )
    st.stop()

# ── Top metric strip ─────────────────────────────────────────────────────
total = len(runs)
red = (runs["severity"] == "RED").sum()
yellow = (runs["severity"] == "YELLOW").sum()
action_runs = (runs["severity"] == "ACTION").sum()
m1, m2, m3, m4 = st.columns(4)
m1.metric("Archived runs", f"{total}")
m2.metric("RED days", f"{red}")
m3.metric("YELLOW days", f"{yellow}")
m4.metric("ACTION days", f"{action_runs}")

# ── Calendar picker ──────────────────────────────────────────────────────
archived_dates = set(
    datetime.strptime(d, "%Y-%m-%d").date() for d in runs["run_date"].tolist()
)
latest_date = max(archived_dates)
oldest_date = min(archived_dates)
today = date.today()
# Allow picking up to 7 days in the future so user can verify the "not yet
# created" path before tomorrow's cron fires.
max_pick = max(today + timedelta(days=7), latest_date)
min_pick = oldest_date - timedelta(days=30)  # generous past window

c_pick, c_info = st.columns([1, 3])
with c_pick:
    sel_date_obj = st.date_input(
        "Select alert date",
        value=latest_date,
        min_value=min_pick,
        max_value=max_pick,
    )
with c_info:
    st.markdown(
        f"<div style='padding-top:28px;color:#c9d1d9;font-size:12px'>"
        f"Archive range: <b>{oldest_date} → {latest_date}</b>  "
        f"·  {total} day{'s' if total != 1 else ''} archived</div>",
        unsafe_allow_html=True,
    )

sel_date = sel_date_obj.strftime("%Y-%m-%d")
run = get_run(sel_date)

# ── Graceful handling: no run for the selected date ──────────────────────
if run is None:
    if sel_date_obj > today:
        days_out = (sel_date_obj - today).days
        info_box(
            f"<b>{sel_date}</b> is {days_out} day{'s' if days_out != 1 else ''} in the future. "
            "The alert for that date hasn't been created yet — it will be archived after "
            "the 4:45 PM ET cron run on that day.",
            "info",
        )
    else:
        # Past date with no archived run. Find the closest archived dates
        # before/after to help the user navigate.
        before = max((d for d in archived_dates if d < sel_date_obj), default=None)
        after = min((d for d in archived_dates if d > sel_date_obj), default=None)
        nav_bits = []
        if before:
            nav_bits.append(f"closest earlier: <b>{before}</b>")
        if after:
            nav_bits.append(f"closest later: <b>{after}</b>")
        nav = "  ·  ".join(nav_bits) if nav_bits else "no archived runs nearby"
        info_box(
            f"No alert was archived for <b>{sel_date}</b>. "
            f"This is normal for dates before the archive started "
            f"({oldest_date}) or for any day the cron didn't run.<br>"
            f"<span style='color:#c9d1d9'>{nav}</span>",
            "warning",
        )
    st.stop()

sev = run.get("severity") or "INFO"
sev_color = _SEVERITY_COLOR.get(sev, "#adbac7")

st.markdown(
    f"<div style='padding:10px 14px;border-left:4px solid {sev_color};"
    f"background:#1c2128;margin:8px 0 14px 0;'>"
    f"<div style='font-size:12px;color:#8b949e;margin-bottom:2px'>"
    f"{run.get('run_timestamp', '')}</div>"
    f"<div style='font-size:15px;color:#f0f6fc;font-weight:600'>"
    f"{run.get('subject', '(no subject)')}</div>"
    f"<div style='font-size:12px;color:{sev_color};margin-top:4px;font-weight:600'>"
    f"{sev}  ·  {run.get('n_constructions', 0) or 0} construction{'' if (run.get('n_constructions') or 0) == 1 else 's'}"
    f"  ·  events: {'yes' if run.get('has_events') else 'no'}</div>"
    f"</div>",
    unsafe_allow_html=True,
)

# ── Body ─────────────────────────────────────────────────────────────────
text_body = run.get("text_body") or ""
if text_body.strip():
    st.code(text_body, language=None)
else:
    info_box("No text body archived for this run.", "info")

# ── Past runs index ──────────────────────────────────────────────────────
section_header("Past runs")
display = runs.copy()
display = display.rename(columns={
    "run_date": "Date", "run_timestamp": "Run at",
    "subject": "Subject", "severity": "Sev",
    "n_constructions": "Constructions", "has_events": "Events",
    "text_len": "Text size",
})
display["Events"] = display["Events"].map({0: "—", 1: "✓"})
st.dataframe(
    display,
    hide_index=True,
    use_container_width=True,
    column_config={
        "Constructions": st.column_config.NumberColumn(format="%d"),
        "Text size": st.column_config.NumberColumn(format="%d B"),
    },
)
