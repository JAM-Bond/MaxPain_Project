#!/bin/bash
# run_cron.sh — universal cron wrapper for MaxPain / Agent_Project jobs.
#
# Every crontab line routes through this so that a job failure is never
# silent. It runs the real command, appends combined stdout+stderr to the
# job's existing log file (behaviour unchanged), records a heartbeat status
# file, and on a NON-ZERO exit emails an alert with the tail of the log.
#
# This closes the gap behind the 2026-05-28 macro-refresh incident: the job
# exited non-zero correctly, but cron threw the exit code away into a log
# nobody reads. Now a non-zero exit pages the operator by email.
#
# Usage (from crontab):
#   bash ~/MaxPain_Project/scripts/cron/run_cron.sh <job_name> <log_path> '<command>'
#
#   <job_name>  short stable key, also used by cron_heartbeat.py (e.g. macro_refresh)
#   <log_path>  absolute path to the job's persistent log (preserves existing logs)
#   <command>   the full shell command, run via `bash -c` (quote it)
#
# Notes:
#  - We intentionally do NOT use `set -e`: we must capture the child's exit
#    code rather than abort on it. `set -uo pipefail` still catches our own
#    bugs and masked pipe failures.
#  - The wrapper always exits with the child's exit code, so a human running
#    it by hand still sees the true status.
set -uo pipefail

if [ "$#" -lt 3 ]; then
    echo "usage: run_cron.sh <job_name> <log_path> <command>" >&2
    exit 64
fi

JOB="$1"
LOG="$2"
CMD="$3"

ROOT="$HOME/MaxPain_Project"
PYTHON="/opt/homebrew/bin/python3.11"
STATUS_DIR="$ROOT/logs/cron_status"
ALERTER="$ROOT/scripts/cron/cron_alert.py"

mkdir -p "$STATUS_DIR" "$(dirname "$LOG")"

START="$(date '+%Y-%m-%dT%H:%M:%S')"
{
    echo ""
    echo "========== run_cron[$JOB] start $START =========="
} >> "$LOG" 2>&1

# Run the real job; combined output appended to the persistent log.
bash -c "$CMD" >> "$LOG" 2>&1
CODE=$?

END="$(date '+%Y-%m-%dT%H:%M:%S')"
echo "========== run_cron[$JOB] end $END exit=$CODE ==========" >> "$LOG" 2>&1

# Heartbeat: "<exit_code> <end_iso>" — read by cron_heartbeat.py.
echo "$CODE $END" > "$STATUS_DIR/${JOB}.status"

# Alert on failure. The alerter tails $LOG itself (avoids fragile arg passing).
if [ "$CODE" -ne 0 ]; then
    "$PYTHON" "$ALERTER" --job "$JOB" --code "$CODE" \
        --log "$LOG" --start "$START" --end "$END" \
        >> "$LOG" 2>&1 || echo "run_cron[$JOB]: alerter itself failed" >> "$LOG" 2>&1
fi

exit "$CODE"
