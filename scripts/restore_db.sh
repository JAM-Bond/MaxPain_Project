#!/bin/bash
# =============================================================================
# restore_db.sh — Guarded restore of the MaxPain SQLite DB from a backup.
#
# Validates the backup BEFORE touching anything, makes a safety copy of the
# current live DB, then (only with --apply) swaps it in. Default is dry-run.
#
#   bash scripts/restore_db.sh data/shared/backups/maxpain_20260610.db          # validate + plan
#   bash scripts/restore_db.sh data/shared/backups/maxpain_20260610.db --apply  # perform restore
#
# IMPORTANT: stop the writers first so nothing holds the DB open mid-swap:
#   launchctl bootout gui/$(id -u)/com.maxpain.dashboard    # stop the 8503 dashboard
#   (and run when no cron is mid-write — outside the :16/:20/:22/:25/:35/:40/:45 ticks)
# After a successful restore, re-apply any migrations newer than the backup and
# restart the dashboard:
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.maxpain.dashboard.plist
# =============================================================================
set -euo pipefail

DB="$HOME/MaxPain_Project/data/shared/maxpain.db"
BACKUP_DIR="$HOME/MaxPain_Project/data/shared/backups"
TS=$(date +%Y%m%d_%H%M%S)
LOG="[$(date '+%Y-%m-%d %H:%M:%S')]"

BACKUP="${1:-}"
APPLY="${2:-}"

if [ -z "$BACKUP" ] || [ ! -f "$BACKUP" ]; then
    echo "$LOG ERROR: pass a backup file. Available:"
    ls -1t "$BACKUP_DIR"/maxpain_*.db 2>/dev/null | grep -v SUSPECT | head -10
    exit 1
fi

echo "$LOG ── DB Restore (${APPLY:-DRY-RUN}) ─────────────────────────"
echo "$LOG Source backup : $BACKUP"
echo "$LOG Target live DB : $DB"

# ── Step 1: validate the backup BEFORE touching live ──────────────────────────
echo "$LOG Validating backup integrity..."
INTEGRITY=$(sqlite3 "$BACKUP" "PRAGMA integrity_check;" 2>&1)
if [ "$INTEGRITY" != "ok" ]; then
    echo "$LOG ABORT: backup failed integrity_check — $INTEGRITY"
    exit 1
fi
TRADES=$(sqlite3 "$BACKUP" "SELECT COUNT(*) FROM spread_score_trades;" 2>&1)
if [ "$TRADES" -lt 20 ] 2>/dev/null; then
    echo "$LOG ABORT: backup has only $TRADES trades — suspiciously low."
    exit 1
fi
echo "$LOG Backup OK (integrity ok, $TRADES trades)."

if [ "$APPLY" != "--apply" ]; then
    echo "$LOG DRY-RUN — would: (1) safety-copy current live DB, (2) swap backup -> live."
    echo "$LOG Re-run with --apply (after stopping the dashboard) to perform the restore."
    exit 0
fi

# ── Step 2: safety-copy the CURRENT live DB (consistent snapshot) ─────────────
SAFETY="$BACKUP_DIR/maxpain_pre_restore_$TS.db"
echo "$LOG Safety-copying current live DB -> $SAFETY"
sqlite3 "$DB" ".backup '$SAFETY'"
echo "$LOG Safety copy written (so this restore is itself reversible)."

# ── Step 3: swap in the backup, clearing stale WAL/SHM of the old live DB ─────
echo "$LOG Swapping backup -> live..."
rm -f "$DB-wal" "$DB-shm"
cp "$BACKUP" "$DB"

# ── Step 4: verify the new live DB ────────────────────────────────────────────
NEW_INTEGRITY=$(sqlite3 "$DB" "PRAGMA integrity_check;" 2>&1)
if [ "$NEW_INTEGRITY" != "ok" ]; then
    echo "$LOG ERROR: restored live DB failed integrity_check — $NEW_INTEGRITY"
    echo "$LOG Recover with: cp '$SAFETY' '$DB'"
    exit 1
fi
echo "$LOG Restore complete. Live DB integrity: ok."
echo "$LOG NEXT: re-apply any migrations newer than the backup, then restart the dashboard."
