#!/bin/bash
# Daily ORATS update — pulls new EOD files from SFTP and merges into by_ticker.
#
# Designed for cron at ~7 PM ET on weekdays (after ORATS posts EOD).
# Idempotent: safe to re-run; ingest.py and daily_extract.py both no-op
# when there's nothing new.
#
# Stages:
#   1. ingest.py all --year YYYY --cleanup
#      → SFTP pull, unzip, daily parquet at data/orats/parquet/year=...
#      → --cleanup deletes zips and CSVs after successful parquet write
#   2. daily_extract.py
#      → appends new daily rows to data/orats/by_ticker/{TICKER}.parquet
set -euo pipefail

cd "$HOME/MaxPain_Project"

YEAR=$(date +%Y)
PYTHON=/opt/homebrew/bin/python3.11

echo "=========================================================="
echo "ORATS daily pipeline — $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================================="

echo ""
echo "[1/2] ingest.py all --year $YEAR --cleanup"
$PYTHON scripts/orats/ingest.py all --year "$YEAR" --cleanup

echo ""
echo "[2/2] daily_extract.py"
$PYTHON scripts/orats/daily_extract.py

echo ""
echo "ORATS daily pipeline complete — $(date '+%Y-%m-%d %H:%M:%S')"
