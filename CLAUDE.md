# CLAUDE.md — MaxPain_Project

Context for Claude Code working in this directory.

> ## ⛔ ABSOLUTE RULE — NEVER EXECUTE TRADES
> The system is **advisory only**. NEVER place, submit, modify, replace, or cancel any
> order/trade — by API, script, cron, or any other means — at any time, under any
> circumstances. No "test" or unfillable orders either. Do not build, keep, wire, or
> schedule any order-execution code. Read-only Schwab access (data) is fine. The **user
> places every trade manually**. This rule is always in effect and must never be
> violated. See auto-memory `feedback_never_execute_trades.md`.

## What this is

The active home of the max pain trading system (v2.4+). Live infrastructure: 9:20/9:25/4:16/4:20/4:25/4:45 ET crons, the 8503 Streamlit dashboard (`com.maxpain.dashboard` launchd), the daily alert + cycle qualifier + post-mortem stack, the trade ledger, and the AI advisor. Paper-test window through ~2026-08-19, then live.

`~/Metal_Project/` is being deleted. The DB, Schwab/auth module, and config have all been ported (2026-05-15→05-17). Acceptance-test wait period is in progress before final delete — see auto-memory `project_post_dbmove_observation_window.md`.

## Key paths

- **DB:** `~/MaxPain_Project/data/shared/maxpain.db` — single source of truth via `lib/db.py:DB_PATH`. Never hardcode the path in new code; always `from lib.db import DB_PATH`.
- **Trading plan:** `docs/TRADING_PLAN.rtf` (canonical mechanics + gates; no per-name cohort lists — those live in `scripts/qualifier/gate_config.py`)
- **Backups:** `data/shared/backups/` (rolling 7-day, via `scripts/backup_db.sh` daily 8:45 ET cron)
- **Schwab auth:** `Schwab/auth.py` + `config/api_keys.env` (file-locked refresh, ported 5/15)
- **Python:** `/opt/homebrew/bin/python3.11` (has pandas, yfinance, anthropic SDK)

## Conventions

- Cron jobs all `cd ~/MaxPain_Project` first, then run module-style or script-style. Most use `sys.path.insert(0, str(Path.home() / "MaxPain_Project"))` followed by `from lib.X import Y  # noqa: E402`.
- Imports from `lib/` are always absolute (`from lib.db import DB_PATH`), never relative — except inside `lib/` itself where intra-package relative imports are fine (`from .db import DB_PATH`).
- Backtest results live as parquet under `data/profile/`. DB tables are operational (live state); parquet artifacts are research evidence (frozen).
- Trade closes: the user reports close → UPDATE `spread_score_trades` via SELECT-confirm-UPDATE pattern. Stock rows use `spread_type='stock'` with 0-sentinel for strike fields.

## What NOT to do

- Don't hardcode the DB path — always import from `lib/db.py`.
- Don't add new code that imports from `~/Metal_Project/` anything.
- Don't add per-name cohort decisions to `TRADING_PLAN.rtf` — those go in `gate_config.py`.
- Don't reactivate the disabled Metal cron entries (already deleted from crontab 2026-05-17).

## Authoritative references

- Trading plan: `docs/TRADING_PLAN.rtf`
- Soul / AI advisor anchor: `config/SOUL.md`
- Auto-memory index: `~/.claude/projects/-Users-josephmorris/memory/MEMORY.md` (loaded automatically)
