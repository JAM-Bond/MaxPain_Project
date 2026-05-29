#!/usr/bin/env python3.11
"""Cycle-open email preview — renders a daily_alert email as it WOULD look
on a 45-DTE entry day, with forced construction blocks for SPY bull_put,
QQQ bear_call, USO inverted_fly_single, JPM zebra_tier2.

Format-only test: not for normal use. No DB writes, no SMTP.

Output:
- /tmp/cycle_open_preview.txt
- /tmp/cycle_open_preview.html  (auto-opens in default browser)

Usage:
    python3.11 scripts/preview/cycle_open_preview.py [--expiry YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from scripts.monitor.daily_alert import build_email_html, derive_subject  # noqa: E402
from scripts.monitor.trade_construction import (  # noqa: E402
    build_construction_block,
    build_zebra_with_overlay_block,
)
from scripts.monitor.zebra_overlay_rule import regime_overlay_rule  # noqa: E402
from scripts.qualifier.gate_config import COHORT_ZEBRA_OVERLAY_AUTO  # noqa: E402


FORCED = [
    ("SPY", "bull_put"),
    ("QQQ", "bear_call"),
    ("USO", "inverted_fly_single"),
    ("JPM", "zebra_tier2"),
]


def _base_text_body() -> str:
    """Run daily_alert --dry-run and capture stdout."""
    proc = subprocess.run(
        [
            "/opt/homebrew/bin/python3.11",
            "scripts/monitor/daily_alert.py",
            "--dry-run",
            "--verbose",
        ],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    return proc.stdout


def _render_constructions(expiry: str) -> tuple[list[str], list[str]]:
    """Render TEXT and HTML construction parts for the 4 forced candidates."""
    text_parts: list[str] = []
    html_parts: list[str] = []
    overlay_rule = None

    for symbol, structure in FORCED:
        print(f"  Rendering {symbol} {structure}…")
        result = build_construction_block(symbol, structure, expiry)
        if not result["ok"]:
            text_parts.append(f"  ⚠ {symbol} {structure}: {result['error']}")
            text_parts.append("")
            continue
        text_parts.append(result["text"])
        text_parts.append("")
        html_parts.append(result["html"])

        if structure.startswith("zebra"):
            if symbol in COHORT_ZEBRA_OVERLAY_AUTO:
                if overlay_rule is None:
                    overlay_rule = regime_overlay_rule()
                ovl = build_zebra_with_overlay_block(symbol, expiry, overlay_rule)
                if ovl["ok"]:
                    text_parts.append(ovl["text"])
                    text_parts.append("")
                    html_parts.append(ovl["html"])
            else:
                note = (
                    f"  ℹ {symbol} long-put overlay: discretionary only "
                    f"(not in COHORT_ZEBRA_OVERLAY_AUTO)."
                )
                text_parts.append(note)
                text_parts.append("")
                html_parts.append(
                    f"<div style='font-size:12px;color:#586069;margin:4px 0 12px 0;"
                    f"padding:6px 10px;background:#f6f8fa;border-left:3px solid #586069'>"
                    f"<b>{symbol}</b> long-put overlay: discretionary only "
                    f"(not in <code>COHORT_ZEBRA_OVERLAY_AUTO</code>).</div>"
                )
    return text_parts, html_parts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--expiry", default="2026-07-17",
                   help="OpEx date for construction (default: JUL 2026 OpEx)")
    p.add_argument("--no-open", action="store_true",
                   help="Skip auto-open in browser")
    args = p.parse_args()

    print(f"Rendering cycle-open preview for OpEx {args.expiry}")
    text_body = _base_text_body()
    print(f"Base alert text: {len(text_body)} chars")

    text_parts, html_parts = _render_constructions(args.expiry)
    construction_text = "\n".join(text_parts)

    # Match daily_alert's wrapping of the construction block at end of text body.
    if construction_text:
        text_body = (
            text_body.rstrip("\n")
            + "\n\n  TRADE CONSTRUCTIONS  (forced preview — SPY/QQQ/USO/JPM)"
            + f"\n  {'-' * 68}\n"
            + construction_text
        )

    subject = derive_subject(text_body, len(html_parts))
    html_body = build_email_html(text_body, html_parts)

    text_path = Path("/tmp/cycle_open_preview.txt")
    html_path = Path("/tmp/cycle_open_preview.html")
    text_path.write_text(text_body)
    html_path.write_text(html_body)

    print(f"\nSubject: {subject}")
    print(f"Text:    {text_path}  ({len(text_body):,} chars)")
    print(f"HTML:    {html_path}  ({len(html_body):,} chars)")
    print(f"Construction blocks rendered: {len(html_parts)} / {len(FORCED)}")

    if not args.no_open:
        webbrowser.open(f"file://{html_path}")


if __name__ == "__main__":
    main()
