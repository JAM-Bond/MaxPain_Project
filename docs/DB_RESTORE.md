# DB Restore Runbook

How to restore the MaxPain SQLite DB from a backup. **Tested 2026-06-10** (restore to
scratch verified: integrity ok, all tables present, fully queryable).

## What backups exist
- **Daily, automatic:** `scripts/backup_db.sh` runs 08:45 ET (launchd `com.maxpain.backup_db`),
  writes `data/shared/backups/maxpain_YYYYMMDD.db` via `sqlite3 .backup` (a consistent
  snapshot — safe even though the live DB is WAL mode + has concurrent writers),
  verifies `integrity_check` + row sanity, then prunes >7-day-old backups (only after a
  verified-good new one). Suspect backups are renamed `*_SUSPECT.db` and never pruned.
- **Ad-hoc, before risky changes:** migrations/this assistant write `maxpain_pre_*.db`
  snapshots (also via `.backup`). These are not auto-pruned by date the same way; keep an eye.

## Key facts
- Backups are **point-in-time**. Restoring an older backup **loses everything written
  since** it was taken — including market-data writes (snapshots, qualifier runs) and any
  **migrations** applied after the backup. Migrations are re-runnable; intraday market data
  for the lost window is not (mostly re-derivable on the next cron, or simply a gap).
- The live DB is **WAL mode**, so it has `maxpain.db-wal` / `-shm` companions. A restore
  must clear those stale companions (the restore script does this).

## Restore procedure (guarded script)

1. **Stop the writers** so nothing holds the DB open mid-swap:
   ```
   launchctl bootout gui/$(id -u)/com.maxpain.dashboard
   ```
   Do the swap outside the cron write ticks (avoid :16/:20/:22/:25/:35/:40/:45 ET).

2. **Validate the chosen backup (dry-run — changes nothing):**
   ```
   bash scripts/restore_db.sh data/shared/backups/maxpain_YYYYMMDD.db
   ```
   It runs `integrity_check` + a trades-row sanity guard and prints the plan.

3. **Perform the restore:**
   ```
   bash scripts/restore_db.sh data/shared/backups/maxpain_YYYYMMDD.db --apply
   ```
   This (a) safety-copies the CURRENT live DB to `maxpain_pre_restore_<ts>.db` (so the
   restore is itself reversible), (b) clears stale `-wal`/`-shm`, (c) swaps the backup in,
   (d) re-verifies `integrity_check` on the new live DB.

4. **Re-apply migrations newer than the backup** (check `scripts/migrations/` against the
   backup date; e.g. a pre-2026-06-10 backup is missing 008/009):
   ```
   python3.11 -m scripts.migrations.00X_name --apply
   ```

5. **Restart the dashboard + verify:**
   ```
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.maxpain.dashboard.plist
   ```

## If something goes wrong
The pre-restore safety copy is the undo button:
```
cp data/shared/backups/maxpain_pre_restore_<ts>.db data/shared/maxpain.db
rm -f data/shared/maxpain.db-wal data/shared/maxpain.db-shm
```

## Verifying a backup without restoring (the test we run)
```
cp data/shared/backups/maxpain_YYYYMMDD.db /tmp/t.db
sqlite3 /tmp/t.db "PRAGMA integrity_check;"          # expect: ok
sqlite3 /tmp/t.db "SELECT COUNT(*) FROM spread_score_trades;"
```
