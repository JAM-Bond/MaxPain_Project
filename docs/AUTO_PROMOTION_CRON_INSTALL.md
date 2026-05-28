# Auto-promotion pipeline — cron install instructions

**Status:** built + smoke-tested 2026-05-18. **Crons not yet installed** — user review pending.

## What needs to change in crontab

### ADD (new auto-promotion pipeline)

```cron
# Auto-promotion pipeline — Stage 1 (liquidity scan), 22:30 ET weekdays
30 22 * * 1-5 cd ~/MaxPain_Project && /opt/homebrew/bin/python3.11 -m scripts.maintenance.auto_promotion_liquidity_scan >> ~/MaxPain_Project/logs/auto_promotion_liquidity_cron.log 2>&1

# Auto-promotion pipeline — Stages 2-5 (batch + extract + walkforward + writer + email), 22:35 ET weekdays
35 22 * * 1-5 cd ~/MaxPain_Project && /opt/homebrew/bin/python3.11 -m scripts.maintenance.auto_promotion_nightly >> ~/MaxPain_Project/logs/auto_promotion_nightly_cron.log 2>&1
```

### REMOVE (retired by pre-reg §6)

```cron
# Quarterly cohort refresh — RETIRED, replaced by auto-promotion pipeline
0 6 5 1,4,7,10 * cd ~/MaxPain_Project && /opt/homebrew/bin/python3.11 -m scripts.maintenance.quarterly_cohort_refresh --apply >> ~/MaxPain_Project/logs/quarterly_refresh_cron.log 2>&1
```

## Recommended rollout sequence

1. **Manual dry-run on a curated batch** (already done; see this session's transcript). Confirm decisions match expectations.
2. **First live run with `--batch-size 5 --tickers <list>`** — run manually, inspect email + `data/profile/auto_promotion/changes_YYYY-MM-DD.parquet`, confirm `gate_config.py` diff before merging.
3. **Add the two ADD cron lines above** to crontab.
4. **Watch the first 1-2 nights of nightly emails** (full 500-batch). Confirm safety thresholds aren't tripping.
5. **Remove the quarterly refresh cron line** above. The auto-promotion pipeline supersedes it.

## What the writer changes when it fires

The AST writer rewrites the targeted `COHORT_*` list literal in `scripts/qualifier/gate_config.py`. Per the sealed structure-to-cohort mapping:

| Structure       | Cohort modified              |
|---|---|
| `bull_put`      | `COHORT_BULL_PUT`            |
| `bear_call`     | `COHORT_BEAR_CALL`           |
| `inverted_fly`  | `COHORT_INVERTED_FLY_SINGLE` |
| `zebra`         | `COHORT_ZEBRA_TIER2`         |

The other cohorts (`COHORT_INVERTED_FLY_PAIR`, `COHORT_ZEBRA_TIER1`, `COHORT_ZEBRA_OVERLAY_AUTO`, `COHORT_ANTI_ZEBRA_TIER1`, earnings cohorts) remain curated — the writer never touches them.

The rewrite preserves the source's surrounding structure (other constants, blank lines, comments OUTSIDE the list literal). Comments INSIDE the list literal (e.g., `# v2 expansion 2026-05-02` interleaved between members) are not preserved — provenance migrates to the `changes_YYYY-MM-DD.parquet` audit log + the per-rewrite trailing `# auto-promotion update YYYY-MM-DD` comment.

## Failure modes that pause writes

Per pre-reg §8 safety:

- > 50 promotions in one night → HALT, email subject = `HALTED (safety)`
- > 5 demotions in one night → HALT
- Any cohort > 200 after writes → HALT
- Source rewritten output fails `ast.parse()` → HALT (atomic — original file untouched)
- Snapshot missing → email `FAILED`
- Any uncaught exception in the driver → email `FAILED`, exit 0 so cron doesn't retry

## Quick reference — manual invocations

```bash
# Stage 1 only (one-shot liquidity scan)
python3.11 -m scripts.maintenance.auto_promotion_liquidity_scan

# Nightly driver, dry-run on explicit batch, no email
python3.11 -m scripts.maintenance.auto_promotion_nightly \
    --dry-run --no-email --batch-size 5 \
    --tickers AAPL COST CRM XOM GLD \
    --structures bull_put bear_call \
    --skip-extract

# Full nightly (what cron runs)
python3.11 -m scripts.maintenance.auto_promotion_nightly
```
