#!/bin/bash
# =============================================================================
# backup_db.sh — Daily SQLite backup for the MaxPain DB
# MaxPain Project · 2026-05-02 (relocated/renamed 2026-05-17)
#
# Cron schedule (daily at 8:45 AM ET — before the 9:20 AM snapshot cron):
#   45 8 * * 1-5 cd ~/MaxPain_Project && bash scripts/backup_db.sh >> logs/backup_cron.log 2>&1
#
# Logic:
#   1. Copy maxpain.db to a dated backup file (sqlite3 .backup)
#   2. Verify the backup is valid (PRAGMA integrity_check + row sanity)
#   3. ONLY if verification passes, delete backups older than KEEP_DAYS
#   4. Log all steps with timestamps
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH="$HOME/MaxPain_Project/data/shared/maxpain.db"
BACKUP_DIR="$HOME/MaxPain_Project/data/shared/backups"
KEEP_DAYS=7
DATE=$(date +%Y%m%d)
BACKUP_FILE="$BACKUP_DIR/maxpain_$DATE.db"
LOG_PREFIX="[$(date '+%Y-%m-%d %H:%M:%S')]"

# ── Setup ─────────────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

echo "$LOG_PREFIX ── DB Backup Start ──────────────────────────────"

# ── Check source DB exists ────────────────────────────────────────────────────
if [ ! -f "$DB_PATH" ]; then
    echo "$LOG_PREFIX ERROR: Source DB not found at $DB_PATH"
    exit 1
fi

SOURCE_SIZE=$(du -h "$DB_PATH" | cut -f1)
echo "$LOG_PREFIX Source: $DB_PATH ($SOURCE_SIZE)"

# ── Step 1: Copy ──────────────────────────────────────────────────────────────
if sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "$LOG_PREFIX Backup written: $BACKUP_FILE ($BACKUP_SIZE)"
else
    echo "$LOG_PREFIX ERROR: Backup copy failed"
    exit 1
fi

# ── Step 2: Verify ────────────────────────────────────────────────────────────
echo "$LOG_PREFIX Verifying backup integrity..."

INTEGRITY=$(sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;" 2>&1)

if [ "$INTEGRITY" = "ok" ]; then
    echo "$LOG_PREFIX Integrity check: PASSED"
else
    echo "$LOG_PREFIX ERROR: Integrity check FAILED — $INTEGRITY"
    echo "$LOG_PREFIX Backup file retained but marked suspect. NOT deleting old backups."
    mv "$BACKUP_FILE" "${BACKUP_FILE%.db}_SUSPECT.db"
    exit 1
fi

# Row-count sanity (key tables — adjusted to MaxPain's active set)
ROW_CHECK=$(sqlite3 "$BACKUP_FILE" "
    SELECT
        (SELECT COUNT(*) FROM live_snapshots)        AS live_snapshots,
        (SELECT COUNT(*) FROM regime_state)          AS regime_state,
        (SELECT COUNT(*) FROM cycle_qualifier_runs)  AS qualifier_runs,
        (SELECT COUNT(*) FROM spread_score_trades)   AS trades;
" 2>&1)

echo "$LOG_PREFIX Row counts (live_snapshots|regime_state|qualifier_runs|trades): $ROW_CHECK"

# Sanity guard — trades table should have at least 20 rows by 2026-05-02
TRADES_COUNT=$(sqlite3 "$BACKUP_FILE" "SELECT COUNT(*) FROM spread_score_trades;" 2>&1)
if [ "$TRADES_COUNT" -lt 20 ] 2>/dev/null; then
    echo "$LOG_PREFIX ERROR: spread_score_trades has only $TRADES_COUNT rows — suspiciously low."
    mv "$BACKUP_FILE" "${BACKUP_FILE%.db}_SUSPECT.db"
    exit 1
fi

echo "$LOG_PREFIX Verification: PASSED ($TRADES_COUNT trades confirmed)"

# ── Step 3: Prune old backups (ONLY after verified good backup exists) ────────
echo "$LOG_PREFIX Pruning backups older than $KEEP_DAYS days..."

DELETED=0
while IFS= read -r old_file; do
    if [[ "$old_file" == *"SUSPECT"* ]]; then
        echo "$LOG_PREFIX Skipping suspect file: $old_file"
        continue
    fi
    rm "$old_file"
    echo "$LOG_PREFIX Deleted: $old_file"
    ((DELETED++)) || true
done < <(find "$BACKUP_DIR" -name "maxpain_*.db" -mtime +$KEEP_DAYS)

if [ "$DELETED" -eq 0 ]; then
    echo "$LOG_PREFIX No old backups to prune."
else
    echo "$LOG_PREFIX Pruned $DELETED old backup(s)."
fi

# ── Summary ───────────────────────────────────────────────────────────────────
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "maxpain_*.db" | wc -l | tr -d ' ')
echo "$LOG_PREFIX Backup complete. $BACKUP_COUNT backup(s) retained in $BACKUP_DIR"
echo "$LOG_PREFIX ── DB Backup End ────────────────────────────────"
