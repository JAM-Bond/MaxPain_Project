#!/usr/bin/env python3.11
"""
generate_launchd_plists.py — emit launchd LaunchAgent plists for every MaxPain
scheduled job, each routed through run_cron.sh (so failure-trapping, the
heartbeat status file, and email alerting all carry over from the cron setup).

This is the version-controlled source of truth for the schedule (it replaces
the old crontab.txt). Re-run it after changing the manifest, then deploy with
deploy_launchd.sh.

Why a generator: every weekday job needs a 5-element StartCalendarInterval
array (launchd has no "1-5" range like cron), which is far too error-prone to
hand-write across ~18 jobs. plistlib guarantees valid output.

Output: scripts/cron/launchd/com.maxpain.<job>.plist

Notes:
 - RunAtLoad is intentionally OMITTED (defaults false): we must NOT fire all
   jobs at every login/boot. launchd still runs a job ONCE on load if its
   scheduled time passed while the machine was off/asleep — that's the
   reboot/sleep catch-up we want, and it does not require RunAtLoad.
 - Local time is ET on this machine, so Hour/Minute are ET directly.
 - The 3 scrapers (agent_fred/bls/yieldcurve) are NOT generated here — they
   stay Agent_Project-owned LaunchAgents, edited in place to route through
   run_cron.sh. See the migration notes / memory.
"""
from __future__ import annotations

import plistlib
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
RUN_CRON = str(ROOT / "scripts/cron/run_cron.sh")
OUT_DIR = ROOT / "scripts/cron/launchd"
ML = str(ROOT / "logs")
PY = "/opt/homebrew/bin/python3.11"
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# (job_key, hour, minute, command). Weekday Mon–Fri unless in QUARTERLY.
# job_key must match run_cron.sh status files + cron_heartbeat EXPECTED_DAILY.
WEEKDAY_JOBS = [
    ("heartbeat",                7, 30, f"cd {ROOT} && {PY} scripts/cron/cron_heartbeat.py"),
    ("schwab_health_agent",      7, 55, f"cd {ROOT} && {PY} scripts/monitor/schwab_health_check.py --project agent"),
    ("schwab_health",            8,  0, f"cd {ROOT} && {PY} scripts/monitor/schwab_health_check.py"),
    ("backup_db",                8, 45, f"cd {ROOT} && bash scripts/backup_db.sh"),
    ("research_cohort",          9, 20, f"cd {ROOT} && {PY} scripts/pipeline/research_cohort_snapshot.py"),
    ("refresh_earnings",         9, 22, f"cd {ROOT} && {PY} scripts/pipeline/refresh_earnings_calendar.py"),
    ("qualifier",                9, 25, f"cd {ROOT} && {PY} scripts/qualifier/cycle_qualifier.py"),
    ("pre_cycle_commentary",     9, 30, f"cd {ROOT} && {PY} scripts/monitor/pre_cycle_commentary.py"),
    ("orats_health",            10,  0, f"cd {ROOT} && {PY} scripts/maintenance/orats_health_check.py"),
    ("close_prices",            16, 16, f"cd {ROOT} && {PY} scripts/pipeline/update_close_prices.py"),
    ("mark_open_spreads",       16, 20, f"cd {ROOT} && {PY} scripts/pipeline/mark_open_spreads.py"),
    ("reconcile_qualifier",     16, 25, f"cd {ROOT} && {PY} scripts/postmortem/reconcile_qualifier_links.py"),
    ("daily_alert",             16, 45, f"cd {ROOT} && {PY} scripts/monitor/daily_alert.py"),
    ("orats_daily",             19,  0, f"cd {ROOT} && bash scripts/orats/daily_pipeline.sh"),
    ("macro_refresh",           19, 30, f"cd {ROOT} && bash scripts/macro/daily_refresh.sh"),
    ("auto_promotion_liquidity",22, 30, f"cd {ROOT} && {PY} -m scripts.maintenance.auto_promotion_liquidity_scan"),
    ("auto_promotion_nightly",  22, 35, f"cd {ROOT} && {PY} -m scripts.maintenance.auto_promotion_nightly"),
]

# Quarterly: 5th of Jan/Apr/Jul/Oct at 06:00. (job_key, months, day, hour, minute, command)
QUARTERLY_JOBS = [
    ("quarterly_refresh", [1, 4, 7, 10], 5, 6, 0,
     f"cd {ROOT} && {PY} -m scripts.maintenance.quarterly_cohort_refresh --apply"),
]

# Agent_Project-owned scrapers. They keep their com.agentproject.* labels (Agent
# owns its data layer per the migration decision) but are routed through
# run_cron.sh for monitoring, and consolidated to ONE weekday 09:00 run
# (was duplicated: Agent LaunchAgent @13:00 every day + MaxPain cron @09:00
# weekdays). 09:00 preserves the morning-fresh state MaxPain consumers relied on.
# (job_key, hour, minute, command, log_path)
AGENT_DIR = Path.home() / "Agent_Project"
AGENT_LOG = AGENT_DIR / "logs/scrapers"
AGENT_SCRAPERS = [
    ("agent_fred",       9, 0, f"cd {AGENT_DIR} && {PY} FRED/scraper.py",       f"{AGENT_LOG}/fred.log"),
    ("agent_bls",        9, 0, f"cd {AGENT_DIR} && {PY} BLS/scraper.py",        f"{AGENT_LOG}/bls.log"),
    ("agent_yieldcurve", 9, 5, f"cd {AGENT_DIR} && {PY} YieldCurve/scraper.py", f"{AGENT_LOG}/yieldcurve.log"),
]


def weekday_intervals(hour: int, minute: int) -> list[dict]:
    # launchd Weekday: 1=Mon … 5=Fri (0 and 7 are Sunday).
    return [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)]


def build(job: str, intervals: list[dict], command: str,
          *, label: str | None = None, log: str | None = None,
          workdir: str | None = None) -> dict:
    log = log or f"{ML}/{_logname(job)}"
    return {
        "Label": label or f"com.maxpain.{job}",
        "ProgramArguments": ["/bin/bash", RUN_CRON, job, log, command],
        "StartCalendarInterval": intervals,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "WorkingDirectory": workdir or str(ROOT),
        "EnvironmentVariables": {"PATH": PATH_ENV},
    }


# Map job_key -> existing canonical log filename (matches the old crontab logs).
_LOG_OVERRIDES = {
    "close_prices": "close_price_cron.log",
    "orats_health": "orats_health_check.log",
}


def _logname(job: str) -> str:
    return _LOG_OVERRIDES.get(job, f"{job}_cron.log")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for job, hh, mm, cmd in WEEKDAY_JOBS:
        d = build(job, weekday_intervals(hh, mm), cmd)
        p = OUT_DIR / f"com.maxpain.{job}.plist"
        with open(p, "wb") as f:
            plistlib.dump(d, f)
        written.append(p.name)
    for job, months, day, hh, mm, cmd in QUARTERLY_JOBS:
        intervals = [{"Month": m, "Day": day, "Hour": hh, "Minute": mm} for m in months]
        d = build(job, intervals, cmd)
        p = OUT_DIR / f"com.maxpain.{job}.plist"
        with open(p, "wb") as f:
            plistlib.dump(d, f)
        written.append(p.name)

    for job, hh, mm, cmd, log in AGENT_SCRAPERS:
        label = f"com.agentproject.{job.removeprefix('agent_')}"
        d = build(job, weekday_intervals(hh, mm), cmd,
                  label=label, log=log, workdir=str(AGENT_DIR))
        p = OUT_DIR / f"{label}.plist"
        with open(p, "wb") as f:
            plistlib.dump(d, f)
        written.append(p.name)

    print(f"Wrote {len(written)} plists -> {OUT_DIR}")
    for name in written:
        print(f"  {name}")


if __name__ == "__main__":
    main()
