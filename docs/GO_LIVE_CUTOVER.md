# Go-Live Cutover Checklist (H3)

_The literal steps for cutover day (~2026-08-19, after the final paper OpEx).
Companion to `docs/GO_LIVE_READINESS.md` (the audit scorecard, esp. §E) and
`docs/PROJECT_OVERVIEW.md` Part II (the operating guide). Written 2026-06-12._

> **The system stays advisory after go-live.** Nothing here enables order
> placement — "go-live" means the USER starts trading the recommendations
> manually with real money, and the paper book is purged so the ledger
> reflects only real trades.

---

## T-7 to T-1 (the week before)

- [ ] **Readiness gate check**: every T0 ✅ and every T1 ✅ or explicitly-accepted 🟡
      in `GO_LIVE_READINESS.md`. No open ⬜ on T1 rows.
- [ ] **Final paper post-mortem** after the last paper OpEx closes
      (`cycle_postmortem_qualifier --opex <date>`), plus the exit-timing
      counterfactual. Capture/archive results BEFORE the purge.
- [ ] **Schwab re-auth fresh**: `python3.11 ~/MaxPain_Project/Schwab/auth.py
      --force-reauth` so the 7-day token clock starts near zero on cutover day.
- [ ] **Confirm live trades are tagged**: every real-money row in
      `spread_score_trades` has `account='live'`
      (`SELECT id, symbol FROM spread_score_trades WHERE account='live'`).
      Anything placed live but missing → fix BEFORE the purge (the purge keeps
      only `account='live'`).
- [ ] **Verify backups healthy**: yesterday's 08:45 `backup_db.sh` ran clean
      (integrity-checked file in `data/shared/backups/`).

## Cutover day — morning (before 09:20)

- [ ] Nothing required — let the normal 9:20/9:25 crons run. The qualifier and
      alert behave identically before/after the purge (they read cohorts +
      market data, not the trade book).

## Cutover day — after the close (16:50+, after the final 16:45 paper alert)

1. - [ ] **Backup now** (don't rely on the morning one):
         `bash ~/MaxPain_Project/scripts/backup_db.sh`
2. - [ ] **Dry-run the purge** and READ the output:
         `python3.11 -m scripts.migrations.010_purge_paper_book`
         - paper-row counts look right (~troughly the table in §E)
         - "keeps N LIVE row(s)" matches the live-trade count from T-1 check
         - KEEP tables listed: `order_legs`, `schwab_fills`
3. - [ ] **Apply**:
         `python3.11 -m scripts.migrations.010_purge_paper_book --apply --yes-purge-paper-book`
         (it takes its own pre-purge safety backup first)
4. - [ ] **Verify post-purge** (all three):
         - live rows survived: `SELECT id, symbol, status FROM spread_score_trades`
           → ONLY `account='live'` rows remain
         - real tables untouched (the migration asserts this; read its output)
         - `schwab_fills.ledger_trade_id` links on live rows intact
5. - [ ] **Wire the order-reconciler cron** (deferred during paper by design):
         - add `("reconcile_orders", 16, 24, f"cd {ROOT} && {PY} -m
           scripts.maintenance.reconcile_orders --apply")` to `WEEKDAY_JOBS`
           in `scripts/cron/generate_launchd_plists.py`
         - add it to `EXPECTED_DAILY` in `scripts/cron/cron_heartbeat.py`
         - `python3.11 scripts/cron/generate_launchd_plists.py` then
           `bash scripts/cron/deploy_launchd.sh`
         - note: the fills→ledger matcher (10/12/14/16:22) already covers
           open/close recording; the reconciler is the leg-level mirror +
           second opinion. Both are read-only against Schwab.
6. - [ ] **Dashboard sanity (B5)**: open the 8503 dashboard — Filled Book shows
         the real trades; paper-fed pages render empty without erroring.
7. - [ ] **Update `GO_LIVE_READINESS.md`**: §E executed (date+counts), B5
         verified, flip the "Execute LAST" banner to DONE.

## First live week (watch list)

- [ ] First live entry flows end-to-end: fill at broker → matcher creates the
      ledger row by the next ingest (≤2h) → 16:20 marks → breach/stop/T-21
      lines in the 16:45 alert → LIVE BOOK RECONCILIATION stays clean.
- [ ] First live close: matcher records exit + fees (`fees_total`); F4 check —
      net vs gross P/L convention reviewed on the first real close.
- [ ] Heartbeat + dead-man stay green all week (cron_status files, healthchecks.io).
- [ ] Psych-gap log (G3): start logging "would I hold in live?" — now it IS live.
- [ ] **Early Sept**: crash-hedge sizing review (F2, scheduled).

## Rollback

Any step wrong → restore the pre-purge safety backup the migration printed:
`bash scripts/restore_db.sh <backup-file> --apply` (validate-first; see
`docs/DB_RESTORE.md`). Restoring loses post-backup writes (re-run migrations).
