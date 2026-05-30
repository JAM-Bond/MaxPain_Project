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
#   3. build_splits_ledger.py --alert
#      → refreshes the feed-reconciled split ledger (config/splits_ledger.csv)
#        now that by_ticker changed; emails if a new split / live-name flag appears
set -euo pipefail

cd "$HOME/MaxPain_Project"

YEAR=$(date +%Y)
PYTHON=/opt/homebrew/bin/python3.11

echo "=========================================================="
echo "ORATS daily pipeline — $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================================="

echo ""
echo "[1/3] ingest.py all --year $YEAR --cleanup"
$PYTHON scripts/orats/ingest.py all --year "$YEAR" --cleanup

echo ""
echo "[2/3] daily_extract.py"
$PYTHON scripts/orats/daily_extract.py

echo ""
echo "[3/3] refresh split ledger + alert on new splits"
# Rebuild the feed-reconciled split ledger now that by_ticker has new rows. The
# heuristic flags any new price discontinuity same-day (the feed confirms within
# its cache TTL); --alert emails only when a split or live-name flag appears.
# Non-fatal: a ledger hiccup must never fail the data pipeline.
$PYTHON -m scripts.maintenance.build_splits_ledger --alert --quiet \
    || echo "WARNING: split-ledger refresh failed (non-fatal)"

echo ""
echo "ORATS daily pipeline complete — $(date '+%Y-%m-%d %H:%M:%S')"
