#!/bin/bash
# deploy_launchd.sh — install + (re)load every generated LaunchAgent plist.
#
# Copies scripts/cron/launchd/*.plist into ~/Library/LaunchAgents/ (where
# launchd auto-loads them at login, so they survive reboot+login), backs up
# any plist it overwrites, then bootout+bootstrap each so it's active now.
#
# Idempotent: safe to re-run after regenerating plists.
# Rollback: restore from the backup dir printed below + `crontab <backup>`.
set -uo pipefail

SRC="$HOME/MaxPain_Project/scripts/cron/launchd"
DEST="$HOME/Library/LaunchAgents"
UID_="$(id -u)"
DOMAIN="gui/$UID_"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="$HOME/MaxPain_Project/data/shared/backups/launchd/$STAMP"

mkdir -p "$DEST" "$BACKUP"
echo "backup dir: $BACKUP"
echo "domain:     $DOMAIN"
echo ""

ok=0; fail=0
for src in "$SRC"/*.plist; do
    label="$(basename "$src" .plist)"
    dest="$DEST/$(basename "$src")"

    # Back up an existing target before overwriting.
    [ -f "$dest" ] && cp "$dest" "$BACKUP/"

    cp "$src" "$dest"

    # Reload: bootout (ignore "not loaded"), then bootstrap.
    launchctl bootout "$DOMAIN/$label" >/dev/null 2>&1
    if launchctl bootstrap "$DOMAIN" "$dest" 2>/tmp/bootstrap_err; then
        launchctl enable "$DOMAIN/$label" >/dev/null 2>&1
        echo "  ✓ $label"
        ok=$((ok+1))
    else
        echo "  ✗ $label — $(cat /tmp/bootstrap_err)"
        fail=$((fail+1))
    fi
done

echo ""
echo "loaded OK: $ok   failed: $fail"
rm -f /tmp/bootstrap_err
[ "$fail" -eq 0 ]
