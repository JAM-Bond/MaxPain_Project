#!/usr/bin/env python3.11
"""
cron_alert.py — send a failure email for a cron job that exited non-zero.

Invoked by run_cron.sh on a non-zero exit. Tails the job's log and emails
the operator via the shared lib/email_alert.send_html_alert path. Kept
deliberately tiny and dependency-light so it can run even when the failing
job's own environment is broken.

Usage:
    cron_alert.py --job macro_refresh --code 1 \
        --log ~/MaxPain_Project/logs/macro_refresh_cron.log \
        --start 2026-05-28T19:30:01 --end 2026-05-28T19:38:12
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.email_alert import send_html_alert  # noqa: E402

TAIL_LINES = 40


def tail(path: str, n: int) -> str:
    p = Path(path)
    if not p.exists():
        return f"(log file not found: {path})"
    try:
        lines = p.read_text(errors="replace").splitlines()
    except Exception as e:  # pragma: no cover — log unreadable
        return f"(could not read log {path}: {e})"
    return "\n".join(lines[-n:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--code", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--start", default="?")
    ap.add_argument("--end", default="?")
    args = ap.parse_args()

    log_tail = tail(args.log, TAIL_LINES)
    subject = f"🔴 CRON FAILED: {args.job} (exit {args.code})"

    text_body = (
        f"Cron job FAILED — exit code {args.code}\n"
        f"\n"
        f"  job:    {args.job}\n"
        f"  start:  {args.start}\n"
        f"  end:    {args.end}\n"
        f"  log:    {args.log}\n"
        f"\n"
        f"--- last {TAIL_LINES} log lines ---\n"
        f"{log_tail}\n"
    )

    html_body = (
        f"<h2 style='color:#c0392b'>🔴 CRON FAILED: {args.job}</h2>"
        f"<table style='font-family:monospace;font-size:13px'>"
        f"<tr><td><b>exit code</b></td><td>{args.code}</td></tr>"
        f"<tr><td><b>start</b></td><td>{args.start}</td></tr>"
        f"<tr><td><b>end</b></td><td>{args.end}</td></tr>"
        f"<tr><td><b>log</b></td><td>{args.log}</td></tr>"
        f"</table>"
        f"<h3>last {TAIL_LINES} log lines</h3>"
        f"<pre style='background:#1e1e1e;color:#ddd;padding:12px;"
        f"border-radius:6px;font-size:12px;overflow-x:auto'>"
        f"{_escape(log_tail)}</pre>"
    )

    ok = send_html_alert(subject, text_body, html_body)
    # If the alert email itself failed, exit non-zero so the wrapper logs it.
    return 0 if ok else 1


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    sys.exit(main())
