# MaxPain — Go-Live Readiness

_Working audit document. Target go-live: **~2026-08-19** (paper-test window end).
Scaffolded 2026-06-09. This is the live checklist for the go-live audit; update
status + evidence as each item is worked._

See also: [[project_go_live_plan]] (the month plan), `docs/PROJECT_OVERVIEW.md`
(the explanatory text / user's guide, item 2), `docs/TRADING_PLAN.rtf` (mechanics + gates).

> **ABSOLUTE RULE — the system never executes trades.** It is **advisory only**: it
> recommends, the **user places every order manually**. No code may place / modify /
> cancel an order, ever. "Go-live" = the user starts trading the recommendations by
> hand with real money. See [[feedback_never_execute_trades]]. This voids any
> "order-path" work and removes the live-execution blockers below.

---

## How this is scored

Each checklist item carries a **Tier** (importance / go-live weight, adapted from the
original AUDIT.md 5-tier scorecard) and a **Status** (current readiness).

**Tiers (importance):**
| Tier | Meaning |
|---|---|
| **T0 — BLOCKER** | Go/no-go. Live money cannot flow until this is ✅. Non-negotiable. |
| **T1 — CRITICAL** | Required for *safe* operation. Should be ✅ at go-live. |
| **T2 — IMPORTANT** | Strongly recommended pre-live; a known, accepted gap is tolerable. |
| **T3 — NICE-TO-HAVE** | Can land after go-live. |
| **T4 — DEFERRED / N-A** | Out of scope for go-live; recorded so it isn't re-litigated. |

**Status (readiness):** 🔴 not started · 🟡 in progress / partial · ✅ ready (evidence linked) · ⬜ not yet assessed

**Go-live gate:** every **T0 = ✅** and every **T1 = ✅ or an explicitly accepted 🟡**.

---

## A. Execution & brokerage

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| A1 | ~~Live order placement path~~ — **VOID by absolute rule** | — | ✅ | **2026-06-10: the system NEVER executes/places/modifies/cancels orders — absolute, always-in-effect rule ([[feedback_never_execute_trades]]).** There is no programmatic order path by design. Order-execution code created earlier this session (`lib/schwab_orders.py`) was DELETED. "Go-live" = the USER starts placing real trades MANUALLY from the recommendations. |
| A1b | No order-execution capability exists in the codebase | T0 | ✅ | Verified 2026-06-10: `lib/schwab_orders.py` removed; no module can place/cancel. Note: the read-only token technically *could* trade (Schwab ignores `scope=readonly`), which is exactly why no execution code may exist — the guard is "no such code," not the scope. Keep it that way. |
| A2 | Schwab OAuth refresh robust under cron (file-locked, no interactive prompt) | T1 | 🟡 | **Root cause found + fixed 2026-06-10.** The silent 6/9 mid-day death was a MASKING BUG: `_post_token` bumps `received_at` on every ~30-min access refresh, and the 7-day refresh-token TTL was computed from `received_at` → it perpetually read ~7d and the 2-day warning NEVER fired; the token died at its true 7-day mark (from last browser re-auth) with no warning. Verified Schwab returns the SAME refresh token on a refresh grant (not rotated). **Fix:** track `refresh_token_issued_at` (stamped only at browser re-auth, carried forward on refresh); TTL + health check now anchor on it. Applied to MaxPain + Agent auth + shared health check; MaxPain token backfilled to yesterday's re-auth (TTL now reads 5.98d correctly). **Remaining:** Schwab's 7-day re-auth is a hard limit (interactive, can't be automated) → the proactive 2-day warning (now working) + a weekly manual re-auth is the standing process. Optional: intraday keepalive/probe so an unexpected death is caught within minutes, not next morning. Agent token: re-auth once to set its exact issue time. |
| A3 | Schwab **fills ingestion** correct for live option spreads | T1 | ✅ | **Live-validated on a real option round trip 2026-06-12**: HCA bull_put open (6/09, 2 legs, prices+fees exact) AND close (6/12, 2 legs) ingested correctly; fills→ledger matcher linked all 4 legs to trade id 193 with no duplicates. Runs intraday 10/12/14/16:22 since 6/12 (F5). |
| A4 | **Daily order reconciler** (read-only Schwab → leg mirror + derived book) | T1 | 🟡 | **BUILT 2026-06-10.** Two layers: (1) `order_legs` = faithful LEG-LEVEL Schwab mirror, **PK (order_id, leg_id)** → dup-proof, stores each leg's fill_price + fees (migration 009); (2) reconciler derives `spread_score_trades` (migration 008 = open/close_order_id + fees_total). Net P/L = Σ signed leg cash flows (SELL +, BUY −) × 100 × shares − fees → exact and **works for 3+ leg structures** (zebra/IF now recordable, no manual flag). Idempotent at leg + position level; rolls/unknown/ambiguous closes flagged. Dry-run verified on real HCA (mirrors 2 legs, derives bull_put 370/365 +2.37, fees 1.37); 8/8 unit tests. **Close path LIVE-TESTED 2026-06-12** on the real HCA close: `--apply --mirror-only` mirrored both closing legs (BTC 370P @ 17.12 / STC 365P @ 15.23, fees exact) into `order_legs` (full 4-leg round trip now mirrored) and both orders correctly skipped as already-recorded against trade id 193. Remaining: wire the 16:24 cron at cutover — now a literal step in `docs/GO_LIVE_CUTOVER.md` (H3). Cosmetic: dry-run "already recorded" line prints "(trade None)" instead of the id. |
| A5 | Paper vs live is unambiguous | T1 | ✅ | Moot under the no-execution rule — the system never sends an order, so it can't send a "live" one by mistake. "Going live" is purely the user's manual decision to start trading the recs with real money. |

## B. Infrastructure & reliability

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| B1 | All crons/launchd agents enumerated, loaded, and firing | T1 | ✅ | Verified 2026-06-10: all 21 MaxPain jobs in `generate_launchd_plists.py` are loaded (last exit 0) + the 8503 dashboard agent. **Gap:** the new order reconciler (`reconcile_orders`) is NOT yet a job — add at go-live (~16:25) to both the plist generator AND the heartbeat manifest. |
| B2 | Failure trapping + dead-man alert (healthchecks.io) covers every job | T1 | 🟡 | Verified: every daily job runs via `run_cron.sh` (per-run failure email) and is in the `cron_heartbeat.py` no-show manifest, with a healthchecks.io dead-man backstop. **Found + fixed 2026-06-10:** the FedWatch ingester (`com.agentproject.fedwatch`) was a standalone plist running the ingester directly — no `run_cron.sh`, no status file, NOT heartbeat-covered (a silent stop would've gone unnoticed). Brought under `run_cron.sh` (AGENT_SCRAPERS) + added to the heartbeat manifest; regenerated + reloaded; verified it writes `cron_status/agent_fedwatch.status`. **Remaining gaps:** `quarterly_refresh` has no no-show check (acceptable); `reconcile_orders` to add at go-live. |
| B3 | DB backup cadence + tested restore | T1 | ✅ | Verified 2026-06-10. Backup design is sound: `backup_db.sh` (08:45 ET) uses `sqlite3 .backup` (consistent snapshot, WAL-safe with concurrent writers), verifies `integrity_check` + row sanity, prunes >7d only after a verified-good new backup, marks suspect files. **Restore tested** (to scratch): integrity ok, all tables present (only `order_legs` absent — postdates the backup), fully queryable. Added a guarded `scripts/restore_db.sh` (validate-first, safety-copies live, --apply to swap) + runbook `docs/DB_RESTORE.md`. Caveat documented: restoring loses post-backup writes incl. migrations (re-runnable). |
| B4 | Mark daemon health (profit-target alerts can fire) | T2 | ✅ | Investigated 2026-06-10. Daemon is HEALTHY: `mark_open_spreads` runs clean (marks through 6/9, all 7 open verticals covered, 0 failed, exit 0). The "mark daemon disabled?" alarm had two causes, both already resolved: (1) FALSE alarm from KO iron_condor — fixed in G1 (iron_condor excluded from the profit-target check; it's correctly not marked); (2) a REAL one-time crash 2026-05-29 (`if trades` on a DataFrame → ValueError, exit 1) — already fixed (now `len(trades)`); clean since 6/1. Non-verticals (long_put/zebra/IC) aren't marked by design. Also reworded the alarmist "(mark daemon disabled?)" alert text → accurate (a true outage trips the cron-failure + heartbeat alerts instead). |
| B5 | Dashboard (8503) uptime + correctness post-purge | T2 | ⬜ | |

## C. Strategy / decision correctness

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| C1 | `gate_config.py` ↔ `TRADING_PLAN.rtf` consistency (mechanics + gates match) | T1 | ✅ | **All 4 code divergences FIXED + verified 2026-06-13** (C1a–C1d in the running summary; unit + dry-run tested): soft-downsize trigger now SPY-within-2%-AND-5d-return<0; bull_put 50% in Stage 2; new ZEBRA skipped at stage ≥1 (anti_zebra exempt); overlay rendered on every zebra (validated vs plan-mandated labels). Code now does what the plan intends. **Doc-only remainder (NOT a live-safety blocker):** the `TRADING_PLAN.rtf` v2.5 text redraft — fold in the PLAN-STALE cluster below so the document matches the code. Original audit ↓. **Audited 2026-06-12** (full plan read vs gate_config + cycle_qualifier + regime writer). **Core gates match exactly:** bull_put signal (contango+VRP>0), bear_call H1, IF term-inv + GOOGL override, hard pause, all entry windows (45/75-DTE, T-5, earnings T-N incl. per-name overrides), DOWNSIZE=0.5, sector cap+ETF exemption, earnings gate + bypasses, demotions (covered_call/bull_put_mp cohorts empty with wiring intact), loss-cap floor. **4 REAL DIVERGENCES — all more permissive than the plan, USER DECISION needed (fix code vs revise plan):** (1) soft-downsize trigger #2 fires only AFTER the 200-DMA break (`trending_down = below_200dma` proxy), plan says "within 2% from above, trending down" (snapshot.py:197-210); (2) Stage 2 (SPY<200dma, IVR<0.5) can emit bull_put GO at FULL size, plan says 50%; (3) Stage 1 never pauses new ZEBRA entries — ZEBRA is fully regime-ungated; (4) ZEBRA long-put overlay auto-attaches on only 5 names (`COHORT_ZEBRA_OVERLAY_AUTO`), plan mandates it on EVERY zebra — which also weakens the 6/9 cap-drop rationale. **PLAN-STALE cluster → draft v2.5:** ZEBRA cap drop (6/9), IF $300 cap, MIN_CREDIT_WIDTH 0.50→0.35 (5/5), the 2×-credit STP LMT stop policy (plan's caveats nominally reject mark-based stops — never reconciled), zebra T-21/rolling self-contradiction (v2.4 log vs 5/14 body edit; close_helper still applies T-21 to the zebra short call), anti_zebra + macro cap + below-MA downsize + EV tiebreak absent from plan, stale version pointers (title v2.3 vs log v2.4; code cites v1.7; nonexistent `_bear_call_h1_ok()` reference). Informational: bear_call ⅓-dollar-risk sizing is advisory-only, not coded. |
| C2 | EV-rank tiebreaker + concentration caps behave under live slate | T2 | 🟡 | Built + tested; over-cap path not yet exercised live — `project_ev_rank_tiebreaker`. |
| C3 | Loss-cap (2× rule) + stop policy surfaced for the user to apply manually | T1 | ✅ | **Audited 2026-06-12 — found a real regression, FIXED same day.** The compact-card redesign had orphaned most discipline content into unrendered dict keys (`summary`/`sizing`/`liquidity_warning` had ZERO consumers): C/W floor PASS/FAIL, max loss, T-21, WIDE flag computed but never shown; only the stop line survived. **Fix:** every card now renders a `discipline` block — verticals: `C/W x.xx PASS/⚠FAIL (floor 0.35) · max loss $N (defined)` + `50% target · T-21 time exit` + conditional natural-worst-below-floor and WIDE warnings; IF: max loss/profit + `50%-of-max ONLY — no 21-DTE stop, no stop-loss`; zebra: held-to-OpEx + 3.5% spot stop; anti_zebra: defined-risk + H1 note. Live-verified on real MSFT bull_put + NVDA IF cards. **RESOLVED 2026-06-13 (Item 7):** the formerly-dead annotation helpers were re-wired as compact one-line discipline entries (`_moneyness_line`/`_ma_bucket_line`/`_if_wing_line`) appended to every construction card's `discipline` block — moneyness + IF-wing walk-forward picks now visible, plus the ⚠ MA-bucket warning on sub-200dma bull_puts. Earnings-track GOs remain card-less by documented design ("later phase"). |
| C4 | System never **recommends** rejected structures; manual off-framework rows handled gracefully | T1 | ✅ | `project_active_trading_styles`. Reframed 2026-06-12: the KO iron_condor (id 181) was **placed deliberately by the user** — not a software leak (system is advisory-only; manual trades are out of scope for this gate). Resolved by splitting it into supported verticals: id 181 → bull_put 77.5/75 @ 0.45, id 192 → bear_call 77.5/80 @ 1.31 (5×, Jul-17) — full monitoring stack now covers both. Graceful-handling half verified (alert excludes IC from profit-target per B4/G1; close_helper skips cleanly). **Recommendation-surface audit DONE 2026-06-12 → ✅:** no rejected structure can reach a GO or a card — `STRUCTURE_TO_OPENER` routes only active styles (iron_condor/iron_fly/jade_lizard/strangle openers exist for backtests but are unrouted; unknown structure → clean error card); covered_call + bull_put_mp inert via empty cohorts with wiring intact (as documented); auto-promotion is hard-coded to the 4 walk-forward structures and its cohort-writer map has no rejected targets (bear_call promotion explicitly disabled); the AI commentary is contractually barred from recommending structures (prompt v2 §"do not recommend opens"). **anti_zebra is a legitimate 5th active path** (promoted 5/17, H1-gated, defined-risk-verified) — was missing from `ALL_STRUCTURES` + `is_in_cohort`, **fixed 2026-06-12**; memory "active trading styles" updated. Cosmetic, accepted: `detect_earnings_risk` labels a same-OpEx bull_put+bear_call pair with the combined-position name ("iron_condor"/"iron_fly") — that's book-monitoring display of what the user actually holds (KO), not a recommendation. Log mid-line splices = stdout/stderr interleaving, not a bug. |
| C5 | Qualifier verdict logic (GO/DOWNSIZE/etc.) sound for live sizing | T1 | ✅ | **Sign-offs CODED 2026-06-13** (the "remaining to ✅" items below): bull_put_earnings now respects hard-pause (regime threaded into `build_earnings_verdicts` → `evaluate_earnings_cell`; PAUSE verdict, unit-tested — IF/bear_call earnings unaffected); anti_zebra now carries the $300 spot budget cap (`MAX_SPOT_ANTI_ZEBRA`; added to `BUDGET_CAPS` + the budget-gated spot fetch; SKIP-over-cap unit-tested). Earnings track still bypasses the *concentration* caps by design (different time bucket) — accepted. Original audit ↓. Audited 2026-06-12 (code-walk + live DB evidence). **Sound:** verdict precedence is a strict early-return chain — no path upgrades SKIP→GO, caps only ever downgrade (cycle_qualifier.py:306-464, 863, 957); DOWNSIZE=0.5 consistent everywhere (35/35 DB rows; alert says "half-size"); 45-DTE window arithmetic verified for Aug-21 (target 2026-07-07 exact; ±1-day tolerance is forward-only); overrides scoped correctly (GOOGL `IF_NO_GATE` still budget-SKIPped live 6/12); persistence all-or-nothing, cron failure-trap + heartbeat verified. **FIXED same day:** latent ArrowInvalid crash — `annotate_bucket_ev` stamps raw `EVScore` objects (`_ev`) onto rows; `write_parquet_artifact` serialized all keys → guaranteed 9:25 crash the first time a concentration cap fired post-6/9 EV wiring (would have hit July 7). Now strips `_`-prefixed keys; repro-tested + 5/5 unit tests. **FIXED 2026-06-12 (2nd pass, punch list):** (1) earnings gate fail-open closed — `earnings_calendar` now distinguishes verified-no-earnings (sentinel cache rows) from fetch-FAILED (reported per symbol; total outage reports all); the qualifier annotates actionable verdicts "⚠ EARNINGS UNVERIFIED" + prints a RUN WARNINGS header (side benefit: sentinels fixed the perpetual cache re-fetch — ETF symbols now count as cache coverage); (2) budget cap fail-open closed — actionable ZEBRA/IF verdicts with no Schwab quote get "⚠ BUDGET CAP UNCHECKED" + run warning; (3) `load_regime_state` age bound added — warn + annotate when the row isn't same-day, REFUSE OpEx verdicts beyond 5 days; both paths dry-run verified. **Remaining to ✅: conscious sign-offs only** — earnings track bypasses hard-pause + both caps by design; anti_zebra has no budget cap/overlay. |

## D. Data integrity

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| D1 | `final_pnl` = TOTAL convention; `shares` non-NULL | T1 | ✅ | Migrations 006 + 007, 2026-06-09; clean `SUM(final_pnl)`. |
| D2 | `target_hit_pnl` convention resolved | T2 | ✅ | Dormant, 1-ct rows, now explicit via 007 — `feedback_close_trade_protocol`. |
| D3 | Price/adjustment correctness (splits, adjusted close) for live marks | T2 | ⬜ | `reference_orats_split_adjustment`, `reference_price_data_sources`. |
| D4 | Staleness handling: alert flags stale feeds, but stale data never drives a live decision | T1 | ✅ | Audited 2026-06-12. **Safe by construction:** no price caching exists anywhere — construction cards + close_helper always live-fetch Schwab chains, failures render visibly; close-mark path has the one real decision-coupled staleness gate (`MARK_STALE_AFTER_DAYS=2` suppresses profit-target alerts, daily_alert.py:502-555); cron-layer trapping (run_cron.sh + heartbeat + 10:00 orats_health) is loud. **Statement NOT strictly true yet — silent paths:** (1) ~~worst: ORATS stall stamped with fresh dates~~ **FIXED 2026-06-12**: the 9:20 snapshot writer now refuses to write `regime_state` when SPY `as_of_close` is older than D-2 business days (correct expectation for ORATS' T+1-evening delivery, verified from parquet mtimes; holiday-aware via the shared `previous_business_day`) and exits 1 → cron email at 9:20, before the 9:25 qualifier. 9-case test incl. May-incident replay + July-7 entry day: all pass. Day-1 stalls remain covered by the 19:40 orats_health check (its docstring wrongly said 10:00 — corrected). Residual: the qualifier itself still reads the most recent `regime_state` row with no age bound (C5 caveat 3) — on a tripped morning it runs on yesterday's row; the 9:20 email is the operator's signal; (2) ~~stale qualifier slate renders as "entry TODAY"~~ **FIXED 2026-06-12**: `_qualifier_slate_warning` banner now renders in both the entry-window events and the construction-card block whenever `MAX(run_date)` != today (tested on synthetic stale DB); the qualifier itself also got the regime age bound (warn/annotate any non-same-day row, refuse >5d — see C5); (3) ~~zebra stop-loss spot date never rendered~~ **FIXED 2026-06-12 (3rd pass)**: 🛑 zebra stop lines now render `[spot as of <date>]` and 🎯/💰 profit-target lines render `(mark <date>)` — every decision-adjacent number in the alert now shows its data's date. The former 1-day silent mark window is thereby visible (a 1-day-old mark passes the gate but its date is displayed). **✅.** |

## E. Data disposition manifest (paper-purge plan) — first-class section

**Requirement:** at go-live the DB must be **pristine of paper-trading activity**; all
**collected market/signal data is kept** (applies to live). **Execute LAST** (final
pre-go-live window), via backup → dry-run → guarded idempotent migration (cf. 006/007).
"DELETE" = **delete the rows, keep the schema** (the table stays; crons/reconciler
repopulate it with real data at go-live).

_Refreshed from the 21-table inventory 2026-06-10 (was a 20-table first pass on 6/9).
Key finding: **NULL-REFS is a non-issue** — every table that carries a `trade_id`
(`position_health_snapshots`, `psychological_gap_log`, `spread_score_daily`,
`trade_ledger_enriched`) is itself in the DELETE bucket, and the KEEP tables hold NO
hard trade references (`cycle_qualifier_runs` has no trade/outcome columns;
`cohort_changes` links only by date/ticker). So nothing dangles after the purge._

**LIVE-AWARE since 2026-06-12 (migration 012):** `spread_score_trades` now carries
`account` ('paper' default / 'live'). Real-money trades placed during the paper window
(first: **HCA bull_put 370/365 Aug-21, id 193, +$48 gross / $2.72 fees, closed 6/12**)
are tagged `account='live'` and **SURVIVE the purge**, along with their
`trade_id`-linked rows in the four linked tables. Migration 010 rewritten accordingly
and the **destructive path was verified on a DB copy** (only the live row + schwab_fills
/ order_legs survive; all paper rows deleted). Any future live trade during the paper
window MUST be inserted with `account='live'`.

| Table | Rows | Classification | Action | Notes |
|---|---:|---|---|---|
| `spread_score_trades` | 133 | paper book | **DELETE rows** (keep `account='live'`) | The paper book. Reconciler repopulates from real orders at go-live. |
| `trade_ledger_enriched` | 133 | paper book (`trade_id`) | **DELETE rows** | Snapshot-at-entry of paper trades; `snapshot_ledger` cron refills live. |
| `spread_score_daily` | 737 | paper book (`trade_id`) | **DELETE rows** | Paper marks; mark daemon refills for live positions. |
| `position_health_snapshots` | 287 | paper book (`trade_id`) | **DELETE rows** | Refills daily at live. |
| `trade_log` | 11 | legacy paper | **DELETE rows** | Legacy stock paper trades (consider dropping the table if truly unused). |
| `psychological_gap_log` | 1 | paper book (`trade_id`) | **DELETE rows** | Paper psych entries. |
| `order_legs` | 2 | **REAL Schwab mirror** | **KEEP** | Real filled-order legs (the HCA real order). NOT paper. |
| ⚠️ `schwab_fills` | 7 | **REAL account data** | **KEEP** | Real CD/HCA reads — must NOT be deleted. |
| `live_snapshots` | 4,285 | collected data | **KEEP** | Market data. |
| `regime_state` | 3,239 | collected data | **KEEP** | Regime history. |
| `regime_health_snapshots` | 434 | collected data | **KEEP** | |
| `regime_health_composites` | 186 | collected data | **KEEP** | |
| `bear_call_census_daily` | 31 | collected data | **KEEP** | |
| `cycle_qualifier_runs` | 4,834 | signal output | **KEEP** | No trade refs → no scrub needed (the qualifier↔trade link lives on the deleted trade row). |
| `cohort_changes` | 480 | process/research | **KEEP** | Promotion-decision history; no trade refs; applies to live. Promote→outcome dashboard join shows live outcomes going forward. |
| `alert_history` | 391 | output archive | **KEEP** | No trade-id columns; operational history, useful for post-mortem. Optional: clear for a clean "live era" slate. |
| `daily_alert_runs` | 23 | output archive | **KEEP** | Same as alert_history. |
| `alert_thresholds` | 47 | config | **KEEP** | |
| `ai_advisor_cache` / `ai_pre_cycle_cache` / `ai_macro_brief_cache` | 8 | regenerable cache | **KEEP** | Regenerates; harmless. |

### Resolved decisions (recommendations for sign-off)
1. **alert archives (`alert_history`, `daily_alert_runs`) → KEEP.** They have no
   trade-id columns and aren't the trade book; keeping preserves post-mortem
   reconstruction continuity. (Optional: clear them if you want a visually clean
   live-era archive — harmless either way, no refs.)
2. **`cohort_changes` → KEEP.** Promotion-decision history is process/research, not
   paper-trade activity, and the promotion logic continues at live.
3. **qualifier NULL-REFS → NO ACTION.** The feared dangling links don't exist —
   `cycle_qualifier_runs` stores no trade references; the link is the
   `qualifier_run_date` column on the (deleted) trade rows.

### Execution sequence (go-live cutover, runs LAST)
1. Final paper post-mortem (capture paper results before the purge).
2. Backup (the restore-tested `backup_db.sh`).
3. Dry-run the purge migration; review counts.
4. Apply: `DELETE FROM` the 6 paper-book tables (rows only, schema kept).
5. Keep everything else incl. `order_legs` + `schwab_fills` (real).
6. From go-live, the order reconciler + crons repopulate the book from real fills.

**Purge migration BUILT 2026-06-10:** `scripts/migrations/010_purge_paper_book.py`
— dry-run by default; requires BOTH `--apply` AND `--yes-purge-paper-book` (accidental-run
guard, must never fire during paper); auto-takes a consistent safety backup before any
delete; asserts `order_legs`/`schwab_fills` row counts unchanged; idempotent. Dry-run
validated (1,302 paper rows across the 6 tables; real-data tables untouched; guard refuses
a single-flag `--apply`). NOT wired to cron — operator-run once at cutover.

## F. Risk controls & money safety

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| F1 | Defined-risk only — system only *recommends* defined-risk structures | T1 | ✅ | **Verified leg-by-leg 2026-06-12** for every structure with a reachable recommendation path: bull_put/bear_call (wing−credit), inverted_fly (debit at center; both shorts covered), zebra tiers + protected/overlay (debit; short covered by 2× longs; overlay adds a LONG put), **anti_zebra** (2× long ITM put / 1 short ATM put — short covered at a higher long strike, max loss = debit, structures.py:461-528), earnings variants (same openers). Undefined-risk code (jade_lizard naked put, short_strangle) is backtest-only and unrouted from every surface; AI commentary barred from structure recommendations. No naked-short path exists. |
| F2 | Crash-hedge sizing reviewed for live book | T2 | ⬜ | Revisit early Sept — `project_crash_hedge_sizing`. |
| F3 | Position-size / concentration caps computed correctly + surfaced (advisory — user enforces at the broker) | T1 | ✅ | **China-slot CODED 2026-06-13** — `CORRELATED_SLOT_GROUPS={"china_adr":["PDD","BABA"]}` + `apply_correlated_slot_cap` (runs after sector/macro caps): ≥2 distinct members actionable in the same OpEx → all DOWNSIZE 0.5, reason "shared slot — size both half or pick one". Unit-tested (both-actionable→both half; single member, different-OpEx, and one-name-two-structures all correctly untouched). Closes the one-China-slot gap that was previously memory-only. **Still MANUAL by decision (not blockers):** contract counts (cards are 1-lot) and the book-level defined-risk budget (~$15.2k figure, folds into Sept F2). Sector cap still counts rows not symbols — visible via the CAPPED OUT line. Original audit ↓. Audited 2026-06-12. **Sound:** EV-rank cap-ranking math correct (within-kind percentile neutralizes zebra-vs-vertical units; 5/5 unit tests; fail-open→alphabetical verified); macro SOFT cap=3 is a real sizing change (GO→DOWNSIZE 0.5, surfaced with reason, fired live 6/4-5; all 154 cohort names present in macro_profile.parquet); IF $300 cap enforced with live Schwab quotes (note: it's a SPOT cap proxying "debit ≤ ~$2.5k", not an actual debit test); below-MA downsize + zebra-trend SKIP + liquidity WIDE flag all verified. **FIXED same day:** the `_ev` parquet crash (same defect as C5). **FIXED 2026-06-12 (2nd pass, punch list):** (1) `SKIP_CONCENTRATION` visibility — `format_verdicts` now has a CAPPED column + a "Capped out today" list, and the daily alert's entry-window section renders "CAPPED OUT (n): names"; (2) budget-gate fail-open annotated (see C5); (4a) SECTOR-LOAD annotation now reads `G.SECTOR_CAP_MAX_PER_OPEX`. **Remaining to ✅:** (3) **NOT CODED — memory-only rules the user enforces by hand:** one-China-slot (PDD+BABA both pass sector cap=2 at full size), contract counts (cards are always 1-lot), book-level defined-risk budget (the ~$15.2k figure is manual analysis, computed nowhere); (4b) sector cap counts ROWS not symbols (dual-cohort BABA can consume both consumer_disc slots and cap PDD out — now at least *visible* via the CAPPED OUT line). MIN_CREDIT_WIDTH is 0.35 (recalibrated 2026-05-05), surfaced as PASS/FAIL on cards but not a hard exclusion — by design (user decision point). |
| F4 | Fees applied to live P/L (paper was gross-only) | T2 | ⬜ | `feedback_paper_pnl_gross_only` — Schwab supplies at live. First live data point: HCA round trip $2.72 on 1-lot (id 193, `fees_total`). |
| F5 | **Live positions never run dark** — every real fill reaches the ledger so monitoring covers it | T1 | ✅ | **Proven gap 2026-06-12:** HCA bull_put (opened 6/09 live) ran 3 days with ZERO system coverage — fills ingested but never matched to a ledger row (retro-logged as id 193). **ALL 3 FIXES BUILT + TESTED same day** (user-approved: matcher writes without asking — bookkeeping of executed trades, not trading decisions; system stays advisory): (1) **fills→ledger matcher** (`lib/fills_ledger_match.py`, wired into `ingest_schwab_fills`): auto-creates `account='live'` rows for clean verticals + single-leg options, auto-closes on clean closes (TOTAL gross P/L + fees accumulate), links pre-recorded trades by order_id, FLAGS (exit 1 → cron email, re-nags every run) rolls/partials/3+ legs/naked shorts; `schwab_fills.ledger_trade_id` column = idempotency. 8/8 unit tests (`tests/test_fills_ledger_match.py`) + **live-validated: ingested today's HCA close fills, linked all 4 legs to id 193, zero duplicates**. (2) **Intraday ingest**: 10:00/12:00/14:00/16:22 ET weekdays (plist generator gained multi-time support; deployed + loaded same day) — dark window now ≤2h, was all-day. (3) **positions-vs-ledger reconciler** (`lib/live_book_reconcile.py`) in the 16:45 alert: broker option positions vs open live rows, 🚨 line for dark positions, ⚠ for ghost ledger rows; renders only on mismatch (✓ goes to cron log); counts toward not-all-quiet. Live run clean; both mismatch directions verified synthetically. First full live alert render: tonight 16:45. |

## G. Monitoring & observability  *(overlaps the active Daily-Alert formatting work)*

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| G1 | Daily alert is **clear and unambiguous** (the active formatting workstream) | T2 | 🟡 | **Round 1 done 2026-06-09:** fixed token-refresh stdout leak (now cron-log only), de-duplicated errors, iron_condor handled as "Not priced" (no false "mark daemon disabled"), removed redundant 52-week section, relabeled IF-cohort as context-only, redesigned close-side table (LIMIT/P&L action-first, cr/db entry tags, mid→worst fill range). **Round 2 done 2026-06-09:** light de-dupe (POSITION HEALTH collapses healthy 🟢 to a count; PSYCH-GAP compacted to grouped id lines referencing POSITION HEALTH) + close-candidate reasons now state the concrete "why" (strike cushion/breach + regime). **Remaining:** spot timestamp differs across sections (POSITION HEALTH=EOD snapshot vs close_helper=live Schwab) — align later; deeper restructure into a single per-position block deferred (user chose light). |
| G2 | Dashboards reflect live (not paper) state post-purge | T2 | 🟡 | **Filled Book page added 2026-06-10** (`dashboard/pages/10_Filled_Book.py` + `queries/filled_book.py`): read-only, **spread-level** view of real Schwab trades — each spread with its credit/debit + fees + open/close, plus reconciled positions w/ P&L. Per-leg prices stay under the hood (dedup + P&L), not shown in UI. Reconciler gained `--mirror-only` to populate `order_legs` during paper without writing the book. |
| G3 | Psych-gap log / discipline prompts adapted for live | T3 | ⬜ | |

## H. Docs & runbook  *(item 2)*

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| H1 | `PROJECT_OVERVIEW.md` → shareable explanatory text + user's guide | T2 | ✅ | **Done 2026-06-10.** Restructured into Part I (explanatory text, the existing §1–6) + **Part II (user's guide, new §7–13)**: advisory principle, daily operating rhythm (grounded in the real launchd schedule), reading the daily alert, the dashboard (10 pages), life of a cycle, maintenance/failure-handling, paper-test→go-live. Added a "How to read this document" orientation + advisory-only framing up top; corrected the subtitle from "research and **execution** system" → "decision-support" (the old wording contradicted [[feedback_never_execute_trades]]). Canonical mechanics stay in `TRADING_PLAN.rtf`. Note: §8 + §12 partially pre-cover H2 (operator runbook) at a narrative level. |
| H2 | Operator runbook: daily flow, what each cron does, how to intervene | T2 | ⬜ | |
| H3 | Go-live cutover checklist (the literal steps on go-live day) | T1 | ✅ | **Written 2026-06-12: `docs/GO_LIVE_CUTOVER.md`** — T-7 prep (gate check, final post-mortem, fresh re-auth, live-tag verification), cutover-day sequence (backup → dry-run purge → apply → 3 verifications → wire reconcile_orders cron → dashboard sanity), first-live-week watch list, rollback via restore_db.sh. Executes §E. |

---

## Running go/no-go summary

_Updated 2026-06-13 (7-item build list IMPLEMENTED + verified)._

- **T0:** ✅ (A1b — no execution code exists).
- **T1 ✅ (16):** A1, A1b, A3, A5, B1, B3, C1, C3, C4, C5, D1, D4, F1, F3, F5, H3.
- **BUILD LIST IMPLEMENTED + VERIFIED 2026-06-13** (decisions made 6/12, coded 6/13;
  unit + dry-run tested, EV suite still 5/5):
  - **C1a ✅** soft-downsize trigger #2 → trending-down = 5-day return < 0 (was the
    below-200dma proxy). `research_cohort_snapshot.py`. _(committed 6/13, 03a69f8)_
  - **C1b ✅** bull_put DOWNSIZE whenever SPY < 200dma (Stage 2 → 0.5).
    `cycle_qualifier.py` step 4.
  - **C1c ✅** SKIP new ZEBRA at regime stage ≥1 (anti_zebra exempt — doesn't start
    with "zebra"). `evaluate_opex_cell` zebra branch.
  - **C1d ✅** priced overlay rendered for EVERY zebra GO/DOWNSIZE; AUTO-cohort
    labeled "backtest-validated", the rest "plan-mandated (no per-name validation)".
    `daily_alert.py`. _(Plan v2.5 TEXT redraft still owed — doc-only, not a
    live-safety blocker; tracked under C1 row.)_
  - **C5 ✅** hard-pause check added to the bull_put_earnings track (regime threaded
    into `build_earnings_verdicts`); `anti_zebra` added to BUDGET_CAPS at $300 spot
    (own `MAX_SPOT_ANTI_ZEBRA` const) + into the budget-gated spot fetch.
  - **F3 ✅** correlated-slot cap CODED — `CORRELATED_SLOT_GROUPS={"china_adr":
    ["PDD","BABA"]}` + `apply_correlated_slot_cap` (runs after sector/macro caps):
    ≥2 distinct members actionable same-OpEx → all DOWNSIZE 0.5, "shared slot — size
    both half or pick one". Contract counts + book-level budget stay MANUAL through
    go-live (book budget folds into Sept F2 review).
  - **Item 7 ✅** the moneyness/MA-bucket/IF-wing helpers re-wired as compact
    one-line discipline entries on every construction card (were zero-caller dead
    code). `trade_construction.py`.
  - **A2 + B2: ACCEPTED 🟡** — weekly manual re-auth + 2-day warning is the standing
    process; documented heartbeat gaps accepted.
- **T2/T3 open, scheduled or at-cutover by design:** B5 + G2-completion (post-purge),
  C2 (over-cap path first exercises ~7/7), D3, F2 (early Sept), F4 (first live close
  convention), G1 remainder, G3, H2 (blocked on UI per user instruction).
- **Cutover runbook:** `docs/GO_LIVE_CUTOVER.md` (H3 ✅).
