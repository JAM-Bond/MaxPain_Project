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
    ("orats_health",            19, 40, f"cd {ROOT} && {PY} scripts/maintenance/orats_health_check.py"),  # after 19:00 ingest; 10:00 false-alarmed daily (ORATS is T+1)
    ("close_prices",            16, 16, f"cd {ROOT} && {PY} scripts/pipeline/update_close_prices.py"),
    ("mark_open_spreads",       16, 20, f"cd {ROOT} && {PY} scripts/pipeline/mark_open_spreads.py"),
    ("reconcile_qualifier",     16, 25, f"cd {ROOT} && {PY} scripts/postmortem/reconcile_qualifier_links.py"),
    ("refresh_breadth_ring",    16, 30, f"cd {ROOT} && {PY} scripts/pipeline/refresh_breadth_ring.py"),  # breadth_live + RSP/SPY ring, before the 16:45 alert reads it
    ("ev_enrich",               16, 35, f"cd {ROOT} && {PY} -m lib.ev_enrich"),  # persist EV-rank before the 16:45 alert reads it
    ("snapshot_ledger",         16, 40, f"cd {ROOT} && {PY} -m scripts.maintenance.snapshot_trade_ledger"),  # freeze entry-context for newly-placed trades (entry-date regime row exists by EOD)
    ("daily_alert",             16, 45, f"cd {ROOT} && {PY} scripts/monitor/daily_alert.py"),
    ("orats_daily",             19,  0, f"cd {ROOT} && bash scripts/orats/daily_pipeline.sh"),
    ("macro_refresh",           19, 30, f"cd {ROOT} && bash scripts/macro/daily_refresh.sh"),
    ("auto_promotion_liquidity",22, 30, f"cd {ROOT} && {PY} -m scripts.maintenance.auto_promotion_liquidity_scan"),
    ("auto_promotion_nightly",  22, 35, f"cd {ROOT} && {PY} -m scripts.maintenance.auto_promotion_nightly"),
    ("stop_profile_ensure",     22, 40, f"cd {ROOT} && {PY} -m lib.ticker_stop_profile --ensure-cohort"),  # fill stop profiles for newly-promoted cohort names
]

# Weekday jobs that fire at MULTIPLE times per day. (job_key, [(hh, mm), ...], command)
# ingest_schwab_fills runs intraday since 2026-06-12 (go-live audit F5): the
# HCA live trade ran dark 3 days because fills only landed at EOD and nothing
# matched them to the ledger. Read-only API + idempotent upsert + the
# fills→ledger matcher → frequency only shrinks the dark window (≤2h).
# Heartbeat no-show coverage keys on the LAST run of the day (16:22).
WEEKDAY_MULTI_JOBS = [
    ("ingest_schwab_fills", [(10, 0), (12, 0), (14, 0), (16, 22)],
     f"cd {ROOT} && {PY} -m scripts.maintenance.ingest_schwab_fills"),
]

# Quarterly: 5th of Jan/Apr/Jul/Oct at 06:00. (job_key, months, day, hour, minute, command)
QUARTERLY_JOBS = [
    ("quarterly_refresh", [1, 4, 7, 10], 5, 6, 0,
     f"cd {ROOT} && {PY} -m scripts.maintenance.quarterly_cohort_refresh --apply"),
    # Twice a year (Jan/Jul 6th, 06:30): full re-scan of every cohort name's breach-
    # recovery / stop profile, to confirm behavior hasn't drifted materially.
    ("stop_profile_refresh", [1, 7], 6, 6, 30,
     f"cd {ROOT} && {PY} scripts/backtest/per_ticker_stop_study.py"),
]

# Agent_Project-owned scrapers. They keep their com.agentproject.* labels (Agent
# owns its data layer per the migration decision) but are routed through
# run_cron.sh for monitoring, and consolidated to ONE weekday 09:00 run
# (was duplicated: Agent LaunchAgent @13:00 every day + MaxPain cron @09:00
# weekdays). 09:00 preserves the morning-fresh state MaxPain consumers relied on.
# (job_key, hour, minute, command, log_path)
AGENT_DIR = Path.home() / "Agent_Project"
AGENT_LOG = AGENT_DIR / "logs/scrapers"
# Staggered to avoid concurrent ChromaDB writes: fred 09:00 -> bls 09:02 ->
# yieldcurve 09:05 (yieldcurve also depends on fred's output, so it must run last).
AGENT_SCRAPERS = [
    ("agent_fred",       9, 0, f"cd {AGENT_DIR} && {PY} FRED/scraper.py",       f"{AGENT_LOG}/fred.log"),
    ("agent_bls",        9, 2, f"cd {AGENT_DIR} && {PY} BLS/scraper.py",        f"{AGENT_LOG}/bls.log"),
    ("agent_yieldcurve", 9, 5, f"cd {AGENT_DIR} && {PY} YieldCurve/scraper.py", f"{AGENT_LOG}/yieldcurve.log"),
    # FedWatch API ingester — was a standalone com.agentproject.fedwatch plist running
    # the ingester directly (no run_cron.sh → no status file → no heartbeat coverage).
    # Brought under run_cron.sh 2026-06-10; same com.agentproject.fedwatch label, log,
    # and weekday-06:15 schedule, now monitored.
    ("agent_fedwatch",   6, 15, f"cd {AGENT_DIR} && {PY} CME_FedWatch/api_ingester.py", f"{AGENT_LOG}/fedwatch.log"),
]

# Other Agent_Project scheduled agents — brought under run_cron.sh for
# monitoring. Schedules PRESERVED exactly from their original plists (most run
# EVERY day, not weekday-only; fomc is Wed/Thu). The `echo started/finished`
# wrappers are dropped (run_cron.sh adds its own banners). Regenerating also
# fixes raw-'&' XML that plutil rejected in several originals.
# (job_key, label_suffix, command, log_filename, intervals, extra_env)
AGENT_JOBS = [
    ("agent_backup",         "backup",         f"cd {AGENT_DIR} && {PY} backup_collections.py",            "backup.log",         [{"Hour": 3,  "Minute": 0}],  None),
    ("agent_order_sync",     "order_sync",     f"cd {AGENT_DIR} && {PY} Schwab/order_sync.py --days 7",    "order_sync.log",     [{"Hour": 9,  "Minute": 30}], None),
    ("agent_cd_maturity",    "cd.maturity",    f"cd {AGENT_DIR} && {PY} BrokeredCDs/maturity_processor.py","cd_maturity.log",    [{"Hour": 12, "Minute": 0}],  None),
    ("agent_tbill_maturity", "tbill.maturity", f"cd {AGENT_DIR} && {PY} TBills/maturity_processor.py",     "tbill_maturity.log", [{"Hour": 12, "Minute": 5}],  None),
    ("agent_fedrss",         "fedrss",         f"cd {AGENT_DIR} && {PY} FederalReserve/scraper.py",        "fedrss.log",         [{"Hour": 13, "Minute": 10}], None),
    ("agent_postmortem",     "postmortem",     f"cd {AGENT_DIR} && PYTHONPATH={AGENT_DIR} {PY} PostMortem/runner.py", "postmortem.log", [{"Hour": 14, "Minute": 0}], None),
    ("agent_fomc",           "fomc",           f"cd {AGENT_DIR} && {PY} FederalReserve/fomc_scraper.py",   "fomc.log",           [{"Weekday": 3, "Hour": 18, "Minute": 15}, {"Weekday": 4, "Hour": 18, "Minute": 15}], None),
]


def weekday_intervals(hour: int, minute: int) -> list[dict]:
    # launchd Weekday: 1=Mon … 5=Fri (0 and 7 are Sunday).
    return [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)]


def build(job: str, intervals: list[dict], command: str,
          *, label: str | None = None, log: str | None = None,
          workdir: str | None = None, env: dict | None = None) -> dict:
    log = log or f"{ML}/{_logname(job)}"
    envvars = {"PATH": PATH_ENV}
    if env:
        envvars.update(env)
    return {
        "Label": label or f"com.maxpain.{job}",
        "ProgramArguments": ["/bin/bash", RUN_CRON, job, log, command],
        "StartCalendarInterval": intervals,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "WorkingDirectory": workdir or str(ROOT),
        "EnvironmentVariables": envvars,
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
    for job, times, cmd in WEEKDAY_MULTI_JOBS:
        intervals = [iv for hh, mm in times for iv in weekday_intervals(hh, mm)]
        d = build(job, intervals, cmd)
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

    for job, suffix, cmd, logfile, intervals, env in AGENT_JOBS:
        label = f"com.agentproject.{suffix}"
        log = f"{AGENT_LOG}/{logfile}"
        d = build(job, intervals, cmd,
                  label=label, log=log, workdir=str(AGENT_DIR), env=env)
        p = OUT_DIR / f"{label}.plist"
        with open(p, "wb") as f:
            plistlib.dump(d, f)
        written.append(p.name)

    print(f"Wrote {len(written)} plists -> {OUT_DIR}")
    for name in written:
        print(f"  {name}")


if __name__ == "__main__":
    main()
