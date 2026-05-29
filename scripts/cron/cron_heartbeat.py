#!/usr/bin/env python3.11
"""
cron_heartbeat.py — morning check that every expected cron job actually ran in
its most recent scheduled window and succeeded.

run_cron.sh writes a status file per job at logs/cron_status/<job>.status
containing "<exit_code> <end_iso>". This script reads them against a manifest
of weekday jobs and emails a summary if any job is MISSING (no successful run
since its last scheduled window) or FAILED (non-zero exit).

This is the layer the per-job wrapper cannot provide: a job that never fired
(laptop asleep at its scheduled minute, crond not running) leaves no failure
to trap — only its ABSENCE reveals it.

Why a MORNING run + schedule-window check (not an end-of-day calendar match):
  - auto_promotion_nightly starts 22:35 and runs ~4h, finishing ~02:39 the
    NEXT calendar day. A same-night / same-date check would false-flag it.
  - Running at 07:30 (before the first 07:55 job) means every job's most
    recent scheduled occurrence is the PRIOR business day and has completed.
  - Comparing each job's status end-time against its own scheduled window
    (skipping weekends) handles midnight-crossing jobs and Mon-checks-Fri
    gaps without special cases.

Schedule: 07:30 ET weekdays, itself via run_cron.sh.

Caveat: if the machine is asleep at 07:30 the heartbeat won't run either — the
inherent limit of any on-box monitor. An external watchdog, or migrating to
launchd (which runs missed jobs on wake), would close that; noted as future work.

Usage:
    cron_heartbeat.py              # email only if something is MISSING/FAILED
    cron_heartbeat.py --verbose    # always email the full grid
    cron_heartbeat.py --no-email   # print the grid to stdout only
    cron_heartbeat.py --now 2026-05-29T07:30:00   # pretend "now" (testing)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.email_alert import send_html_alert  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"
STATUS_DIR = ROOT / "logs" / "cron_status"

# Jobs expected every weekday (Mon–Fri), ordered by schedule.
# (job_key, human label, HH, MM). job_key must match the name passed to
# run_cron.sh. Non-daily jobs (e.g. quarterly_cohort_refresh) are excluded.
EXPECTED_DAILY = [
    ("schwab_health_agent",      "Schwab health (Agent)",     7, 55),
    ("schwab_health",            "Schwab health (MaxPain)",   8,  0),
    ("backup_db",                "DB backup",                 8, 45),
    ("agent_fred",               "FRED scraper",              9,  0),
    ("agent_bls",                "BLS scraper",               9,  0),
    ("agent_yieldcurve",         "Yield-curve scraper",       9,  5),
    ("research_cohort",          "Research cohort snapshot",  9, 20),
    ("refresh_earnings",         "Earnings calendar refresh", 9, 22),
    ("qualifier",                "Cycle qualifier",           9, 25),
    ("pre_cycle_commentary",     "Pre-cycle commentary",      9, 30),
    ("orats_health",             "ORATS health check",       10,  0),
    ("close_prices",             "Close-price update",       16, 16),
    ("mark_open_spreads",        "Mark open spreads",        16, 20),
    ("reconcile_qualifier",      "Qualifier reconcile",      16, 25),
    ("daily_alert",              "Daily alert email",        16, 45),
    ("orats_daily",              "ORATS daily pipeline",     19,  0),
    ("macro_refresh",            "Macro-sensitivity refresh",19, 30),
    ("auto_promotion_liquidity", "Auto-promotion liquidity", 22, 30),
    ("auto_promotion_nightly",   "Auto-promotion nightly",   22, 35),
]


def read_status(job: str) -> tuple[int | None, datetime | None]:
    """Return (exit_code, end_dt) from the job's status file, or (None, None)."""
    f = STATUS_DIR / f"{job}.status"
    if not f.exists():
        return None, None
    parts = f.read_text().strip().split()
    if len(parts) < 2:
        return None, None
    try:
        return int(parts[0]), datetime.strptime(parts[1], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None, None


def most_recent_occurrence(hh: int, mm: int, now: datetime) -> datetime:
    """Most recent weekday (Mon–Fri) occurrence of HH:MM at or before `now`."""
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand > now:
        cand -= timedelta(days=1)
    while cand.weekday() >= 5:  # Sat=5, Sun=6 — step back to Friday
        cand -= timedelta(days=1)
    return cand


def evaluate(now: datetime):
    """Return list of (key, label, sched_str, state, detail) for each job."""
    rows = []
    for key, label, hh, mm in EXPECTED_DAILY:
        sched_str = f"{hh:02d}:{mm:02d}"
        due = most_recent_occurrence(hh, mm, now)
        code, end_dt = read_status(key)
        if code is None or end_dt is None:
            state, detail = "MISSING", "no status file"
        elif end_dt < due:
            state, detail = "MISSING", f"last ran {end_dt:%Y-%m-%d %H:%M} (< due {due:%Y-%m-%d %H:%M})"
        elif code != 0:
            state, detail = "FAILED", f"exit {code} at {end_dt:%Y-%m-%d %H:%M}"
        else:
            state, detail = "OK", f"{end_dt:%m-%d %H:%M}"
        rows.append((key, label, sched_str, state, detail))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true",
                    help="email the full grid even when all green")
    ap.add_argument("--no-email", action="store_true",
                    help="print the grid to stdout only; never send email")
    ap.add_argument("--now", default=None,
                    help="override 'now' as ISO (testing), e.g. 2026-05-29T07:30:00")
    args = ap.parse_args()

    now = datetime.strptime(args.now, "%Y-%m-%dT%H:%M:%S") if args.now else datetime.now()
    rows = evaluate(now)
    problems = [r for r in rows if r[3] != "OK"]

    icon = {"OK": "🟢", "FAILED": "🔴", "MISSING": "⚪"}
    n_ok = sum(1 for r in rows if r[3] == "OK")
    print(f"cron heartbeat @ {now:%Y-%m-%d %H:%M}: {n_ok}/{len(rows)} OK, {len(problems)} problem(s)")
    for key, label, sched, state, detail in rows:
        print(f"  {icon[state]} {state:7s} {sched} {label} — {detail}")

    if args.no_email:
        return 0
    if not problems and not args.verbose:
        return 0  # silent on a clean day; no alert fatigue

    sev = "🔴" if problems else "🟢"
    headline = (f"{len(problems)} cron job(s) did not complete"
                if problems else "all cron jobs completed")
    subject = f"{sev} Cron heartbeat: {headline}"

    def fmt_row(r):
        key, label, sched, state, detail = r
        return f"  {icon[state]} {state:7s} {sched}  {label:28s} {detail}"

    text_body = (
        f"Cron heartbeat @ {now:%Y-%m-%d %H:%M}\n"
        f"{n_ok}/{len(rows)} expected jobs completed in their last window.\n\n"
        + "\n".join(fmt_row(r) for r in rows) + "\n"
    )

    def html_row(r):
        key, label, sched, state, detail = r
        color = {"OK": "#27ae60", "FAILED": "#c0392b", "MISSING": "#7f8c8d"}[state]
        return (f"<tr><td>{icon[state]}</td>"
                f"<td style='color:{color}'><b>{state}</b></td>"
                f"<td>{sched}</td><td>{label}</td>"
                f"<td style='color:#888'>{detail}</td></tr>")

    html_body = (
        f"<h2 style='color:{'#c0392b' if problems else '#27ae60'}'>"
        f"Cron heartbeat — {headline}</h2>"
        f"<p style='color:#888;font-family:monospace'>checked {now:%Y-%m-%d %H:%M}</p>"
        f"<table style='font-family:monospace;font-size:13px;border-spacing:8px 2px'>"
        + "".join(html_row(r) for r in rows) + "</table>"
    )

    ok = send_html_alert(subject, text_body, html_body)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
