"""Shared CSS + helpers for the MaxPain dashboard.

Visual language matches Agent_Project's 8501 dashboard:
- GitHub dark palette (#0d1117 app, #161b22 cards, #58a6ff accent blue,
  #3fb950 success/up, #f85149 danger/down)
- IBM Plex Sans (body) + IBM Plex Mono (numbers, labels, code)
- Multi-page Streamlit, sidebar gradient banner, delta-card pattern
"""
from __future__ import annotations
import streamlit as st


def inject_css() -> None:
    """Inject the global CSS once per page. Mirrors Agent_Project/Dashboard/1_Main_Dashboard.py."""
    st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');
  html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
  .stApp { background: #0d1117; color: #e6edf3; }

  .page-header { border-bottom: 1px solid #21262d; padding-bottom: 1rem; margin-bottom: 1.5rem; }
  .page-title  { font-family: 'IBM Plex Mono', monospace; font-size: 1.6rem; font-weight: 600; color: #58a6ff; letter-spacing: -0.02em; margin: 0; }
  .page-sub    { font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem; color: #adbac7; margin-top: 0.25rem; }

  .stats-bar {
      display: flex; gap: 1.5rem; flex-wrap: wrap;
      background: #161b22; border: 1px solid #21262d;
      border-radius: 8px; padding: 0.7rem 1.1rem; margin-bottom: 1.5rem;
      font-family: 'IBM Plex Mono', monospace; font-size: 0.72rem;
  }
  .stat-item   { color: #adbac7; }
  .stat-value  { color: #3fb950; font-weight: 600; }
  .stat-warn   { color: #e3b341; font-weight: 600; }
  .stat-danger { color: #f85149; font-weight: 600; }

  .section-header {
      font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem; font-weight: 600;
      color: #adbac7; text-transform: uppercase; letter-spacing: 0.1em;
      border-bottom: 1px solid #21262d; padding-bottom: 0.4rem; margin: 1.5rem 0 1rem 0;
  }

  .info-box  { background: #161b22; border: 1px solid #30363d; border-left: 3px solid #58a6ff; border-radius: 4px; padding: 0.8rem 1rem; font-size: 0.82rem; color: #adbac7; margin: 0.75rem 0; line-height: 1.6; }
  .news-box  { background: #161b22; border: 1px solid #30363d; border-left: 3px solid #3fb950; border-radius: 4px; padding: 0.9rem 1rem; font-size: 0.85rem; color: #c9d1d9; margin: 0.75rem 0; line-height: 1.6; }
  .warn-box  { background: #161b22; border: 1px solid #30363d; border-left: 3px solid #e3b341; border-radius: 4px; padding: 0.8rem 1rem; font-size: 0.82rem; color: #c9d1d9; margin: 0.75rem 0; line-height: 1.6; }
  .alert-box { background: #161b22; border: 1px solid #30363d; border-left: 3px solid #f85149; border-radius: 4px; padding: 0.8rem 1rem; font-size: 0.82rem; color: #c9d1d9; margin: 0.75rem 0; line-height: 1.6; }

  .delta-card  { background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 0.7rem 0.9rem; text-align: center; }
  .delta-label { font-family: 'IBM Plex Mono', monospace; font-size: 0.62rem; color: #c9d1d9; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.3rem; }
  .delta-value { font-family: 'IBM Plex Mono', monospace; font-size: 1.1rem; font-weight: 600; color: #f0f6fc; }
  .delta-up    { color: #56d364 !important; }
  .delta-down  { color: #ff6a69 !important; }
  .delta-warn  { color: #f0c674 !important; }
  .delta-info  { color: #79c0ff !important; }

  .ring-card  { background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 1rem; }
  .ring-name  { font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; color: #adbac7; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 0.5rem; }
  .ring-state { font-family: 'IBM Plex Mono', monospace; font-size: 1.4rem; font-weight: 600; }
  .ring-detail { font-family: 'IBM Plex Mono', monospace; font-size: 0.7rem; color: #adbac7; margin-top: 0.4rem; }

  .stButton > button {
      background: #161b22 !important; color: #c9d1d9 !important;
      border: 1px solid #30363d !important; border-radius: 6px !important;
      font-family: 'IBM Plex Mono', monospace !important; font-size: 0.78rem !important;
      font-weight: 400 !important; padding: 0.5rem 0.8rem !important;
      text-align: left !important; transition: all 0.15s !important;
  }
  .stButton > button:hover { border-color: #58a6ff !important; color: #58a6ff !important; background: #161b22 !important; }

  .stTabs [data-baseweb="tab-list"] { background: #161b22; border-radius: 8px; padding: 4px; gap: 4px; }
  .stTabs [data-baseweb="tab"] { background: transparent; color: #adbac7; font-family: 'IBM Plex Mono', monospace; font-size: 0.8rem; border-radius: 6px; padding: 0.4rem 1rem; }
  .stTabs [aria-selected="true"] { background: #21262d !important; color: #e6edf3 !important; }

  #MainMenu, footer, header { visibility: hidden; }
  .block-container { padding-top: 1.5rem !important; }
  hr { border-color: #21262d !important; margin: 1.2rem 0 !important; }

  /* (was hiding first sidebar nav item — removed so the Home/Landing
     page link is reachable from sub-pages) */

  div[data-testid="stMetricValue"] {
      font-family: 'IBM Plex Mono', monospace !important;
      color: #f0f6fc !important;
      font-weight: 600 !important;
  }
  div[data-testid="stMetricValue"] * { color: #f0f6fc !important; }
  div[data-testid="stMetricLabel"] {
      font-family: 'IBM Plex Mono', monospace !important;
      font-size: 0.72rem !important;
      color: #c9d1d9 !important;
  }
  div[data-testid="stMetricDelta"] { font-family: 'IBM Plex Mono', monospace !important; }

  /* Dataframe cells — make numbers bright too */
  .stDataFrame [role="cell"], .stDataFrame [role="gridcell"],
  .stDataFrame .glide-cell { color: #f0f6fc !important; }
  .stDataFrame [role="columnheader"] { color: #c9d1d9 !important; }

  /* Streamlit dataframe — dark theme */
  .stDataFrame { background: #161b22 !important; }

  /* Sidebar — force always-open, hide the collapse control entirely.
     User specifically wants the sidebar non-closable. Hides every variant
     of the collapse / expand button across Streamlit versions. */
  [data-testid="stSidebarCollapseButton"],
  [data-testid="collapsedControl"],
  [data-testid="stSidebarCollapsedControl"],
  button[kind="header"],
  button[kind="headerNoPadding"],
  [data-testid="stSidebar"] button[aria-label*="ollapse"],
  [data-testid="stSidebar"] button[aria-label*="idebar"],
  [data-testid="stSidebar"] [data-testid*="ollapse"] {
      display: none !important;
      visibility: hidden !important;
      pointer-events: none !important;
  }

  /* Force sidebar to stay visible + at full width regardless of session state */
  [data-testid="stSidebar"] {
      display: block !important;
      visibility: visible !important;
      transform: none !important;
      margin-left: 0 !important;
      min-width: 244px !important;
      width: 244px !important;
  }
  [data-testid="stSidebar"] > div:first-child {
      width: 244px !important;
      transform: none !important;
  }
  [data-testid="stSidebar"][aria-expanded="false"] {
      transform: none !important;
      margin-left: 0 !important;
      width: 244px !important;
      min-width: 244px !important;
  }

  /* Brighten muted text everywhere — old #adbac7 was too dim on the
     dark surface for readability */
  .page-sub, .stat-item, .delta-label, .ring-name, .ring-detail,
  .info-box, .section-header, .fedwatch-label,
  div[data-testid="stMetricLabel"],
  .stCaption, [data-testid="stCaptionContainer"] {
      color: #c9d1d9 !important;
  }
  .stMarkdown p, .stMarkdown li, .stMarkdown span { color: #e6edf3 !important; }

  /* Selectbox / dropdown text legibility */
  .stSelectbox label, .stTextInput label, .stTabs label {
      color: #e6edf3 !important;
  }
</style>
""", unsafe_allow_html=True)


def sidebar_banner() -> None:
    """Render the gradient banner + explicit Home link in the sidebar."""
    st.sidebar.markdown("""
<div style="background: linear-gradient(135deg, #1f6feb 0%, #388bfd 100%);
            color: white; padding: 1.5rem 1rem; margin: -1rem -1rem 1rem -1rem;
            text-align: center; font-family: 'IBM Plex Mono', monospace;
            font-size: 0.95rem; font-weight: 700; letter-spacing: 2px;
            border-radius: 0 0 10px 10px; line-height: 1.6;">
  MAXPAIN<br/>DASHBOARD
</div>
""", unsafe_allow_html=True)
    # Explicit Home link — works from any sub-page. Streamlit auto-discovery
    # also shows the main script in the nav, but auto-labels it as "app";
    # this gives a clean "🎯 Home" entry that always works.
    try:
        st.sidebar.page_link("app.py", label="🎯 Home", icon=None)
    except Exception:
        # Older Streamlit versions: fall back to a plain link
        st.sidebar.markdown(
            "<a href='/' style='color:#79c0ff;text-decoration:none;"
            "font-family:IBM Plex Mono,monospace;font-size:0.9rem;"
            "padding:0.4rem 0.75rem;display:block'>🎯 Home</a>",
            unsafe_allow_html=True,
        )


def page_header(title: str, sub: str) -> None:
    """Top-of-page header with title + subtitle."""
    st.markdown(f"""
<div class="page-header">
  <p class="page-title">{title}</p>
  <p class="page-sub">{sub}</p>
</div>
""", unsafe_allow_html=True)


def section_header(text: str) -> None:
    st.markdown(f'<div class="section-header">{text}</div>', unsafe_allow_html=True)


def delta_card(label: str, value: str, tone: str = "neutral") -> str:
    """HTML for a delta card. tone: 'up' | 'down' | 'warn' | 'info' | 'neutral'."""
    cls = {"up": "delta-up", "down": "delta-down", "warn": "delta-warn",
           "info": "delta-info", "neutral": ""}.get(tone, "")
    return f"""<div class="delta-card">
  <div class="delta-label">{label}</div>
  <div class="delta-value {cls}">{value}</div>
</div>"""


def info_box(text: str, kind: str = "info") -> None:
    cls = {"info": "info-box", "news": "news-box", "warn": "warn-box", "alert": "alert-box"}.get(kind, "info-box")
    st.markdown(f'<div class="{cls}">{text}</div>', unsafe_allow_html=True)
