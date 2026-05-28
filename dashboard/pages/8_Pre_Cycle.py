"""Pre-Cycle Commentary — Phase 2 of the AI advisory layer.

Cron-fired (9:30 ET weekdays, gated on GO/DOWNSIZE presence). This page
displays the latest cached commentary plus today's underlying bundle.
There is intentionally NO Generate button: per locked design decision
(2026-05-17), Phase 2 runs cron-only — manual triggers happen via the CLI
(`python3.11 scripts/monitor/pre_cycle_commentary.py --force`).

Read-only. Does not influence the qualifier or alert pipeline.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

st.set_page_config(page_title="Pre-Cycle — MaxPain", layout="wide",
                   initial_sidebar_state="expanded")

from components.style import (  # noqa: E402
    inject_css, sidebar_banner, page_header, section_header,
)
from lib.ai_pre_cycle_commentary import get_latest_cached, VERSION  # noqa: E402
from dashboard.queries.pre_cycle_bundle import (  # noqa: E402
    has_decision_relevant_verdicts,
    compose_bundle,
)

inject_css()
sidebar_banner()
page_header(
    "🧭 Pre-Cycle Commentary",
    f"Cron-fired (9:30 ET weekdays · gated on GO/DOWNSIZE) · prompt {VERSION}",
)

# ── Today's gate state ─────────────────────────────────────────────────────
today_iso = date.today().isoformat()
has_decisions, n_go, n_ds = has_decision_relevant_verdicts(today_iso)

c1, c2, c3 = st.columns(3)
c1.metric("Today's qualifier — GO", n_go)
c2.metric("Today's qualifier — DOWNSIZE", n_ds)
c3.metric("Gate state", "OPEN — would fire" if has_decisions else "CLOSED — would skip")

if not has_decisions:
    st.caption(
        "_No GO/DOWNSIZE today → cron will skip the API call. The latest cached "
        "commentary (from a prior decision-day) is shown below._"
    )

# ── Latest cached commentary ───────────────────────────────────────────────
section_header("Latest cached commentary")
latest = get_latest_cached()

if latest is None:
    st.info(
        "No cached commentary yet. The cron entrypoint will populate this "
        "after the next weekday with decision-relevant verdicts. To force a "
        "run from the CLI: "
        "`python3.11 scripts/monitor/pre_cycle_commentary.py --force`"
    )
else:
    st.caption(
        f"run_date **{latest['run_date']}**  ·  generated {latest['generated_at']}  ·  "
        f"prompt {latest.get('prompt_version', '?')}  ·  model {latest['model']}  ·  "
        f"in={latest['input_tokens']:,} out={latest['output_tokens']:,} "
        f"cache_read={latest['cache_read_tokens']:,}  "
        f"elapsed={(latest['elapsed_seconds'] or 0):.1f}s"
    )
    st.markdown(latest["response_text"])

# ── Today's bundle preview ─────────────────────────────────────────────────
section_header("Today's bundle (what the AI would see if it fires)")
with st.expander("View bundle", expanded=False):
    bundle = compose_bundle(today_iso)
    st.code(bundle, language="markdown")
