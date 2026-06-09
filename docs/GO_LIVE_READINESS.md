# MaxPain — Go-Live Readiness

_Working audit document. Target go-live: **~2026-08-19** (paper-test window end).
Scaffolded 2026-06-09. This is the live checklist for the go-live audit; update
status + evidence as each item is worked._

See also: [[project_go_live_plan]] (the month plan), `docs/PROJECT_OVERVIEW.md`
(the explanatory text / user's guide, item 2), `docs/TRADING_PLAN.rtf` (mechanics + gates).

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
| A1 | Live **order placement** path tested (place / modify / cancel a real order) | T0 | 🔴 | Only **read** access verified to date — `reference_schwab_account_api_access`. The write path is unproven. |
| A2 | Schwab OAuth refresh robust under cron (file-locked, no interactive prompt) | T1 | 🔴 | **Refresh token expired/revoked as of 2026-06-09** → live chains return empty; close-side table + construction enrichment degraded until manual `Schwab/auth.py --force-reauth`. Schwab refresh tokens expire ~weekly and re-auth is interactive → recurring go-live risk. (stdout-leak sub-issue fixed in G1.) |
| A3 | Schwab **fills ingestion** correct for live option spreads | T1 | 🟡 | `ingest_schwab_fills` cron live-validated on CDs/HCA; option path unit-tested only — `project_trade_ledger_phase1_built`. |
| A4 | **Fills → spread-row matcher** (orderId grouping, propose-only) | T1 | 🔴 | Open go-live remnant — `project_session_20260609_handoff`. |
| A5 | Paper vs live account separation is unambiguous (no live order from a paper rec by mistake) | T0 | ⬜ | Define the guardrail: how does the system know an order is live? |

## B. Infrastructure & reliability

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| B1 | All crons/launchd agents enumerated, loaded, and firing | T1 | ⬜ | `reference_cron_failure_trapping`, `reference_dashboard_launchd`. Produce a definitive manifest. |
| B2 | Failure trapping + dead-man alert (healthchecks.io) covers every job | T1 | ⬜ | Confirm each job is in the heartbeat manifest. |
| B3 | DB backup cadence + tested restore | T1 | ⬜ | 8:45 ET `backup_db.sh`, rolling 7-day. **Test an actual restore.** |
| B4 | Mark daemon health (profit-target alerts can fire) | T2 | 🔴 | Alert shows "mark daemon disabled?" for KO id=181 — investigate (see G-issues). |
| B5 | Dashboard (8503) uptime + correctness post-purge | T2 | ⬜ | |

## C. Strategy / decision correctness

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| C1 | `gate_config.py` ↔ `TRADING_PLAN.rtf` consistency (mechanics + gates match) | T1 | ⬜ | |
| C2 | EV-rank tiebreaker + concentration caps behave under live slate | T2 | 🟡 | Built + tested; over-cap path not yet exercised live — `project_ev_rank_tiebreaker`. |
| C3 | Loss-cap (2× rule) + stop policy (STP LMT GTC) enforced on every live order | T0 | ⬜ | `feedback_loss_cap_discipline`, `project_credit_spread_stop_policy`. Real money → this is a blocker. |
| C4 | Active structures only (bull_put / bear_call / inverted_fly / zebra); rejected ones can't be placed | T1 | ⬜ | `project_active_trading_styles`. Note: KO **iron_condor** row exists in the book + errors in the alert — a rejected structure leaked into the book. |
| C5 | Qualifier verdict logic (GO/DOWNSIZE/etc.) sound for live sizing | T1 | ⬜ | `reference_cycle_qualifier_v1`. |

## D. Data integrity

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| D1 | `final_pnl` = TOTAL convention; `shares` non-NULL | T1 | ✅ | Migrations 006 + 007, 2026-06-09; clean `SUM(final_pnl)`. |
| D2 | `target_hit_pnl` convention resolved | T2 | ✅ | Dormant, 1-ct rows, now explicit via 007 — `feedback_close_trade_protocol`. |
| D3 | Price/adjustment correctness (splits, adjusted close) for live marks | T2 | ⬜ | `reference_orats_split_adjustment`, `reference_price_data_sources`. |
| D4 | Staleness handling: alert flags stale feeds, but stale data never drives a live decision | T1 | ⬜ | Alert shows many `[STALE]` tags — confirm none gate a live order. |

## E. Data disposition manifest (paper-purge plan) — first-class section

**Requirement:** at go-live the DB must be **pristine of paper-trading activity**; all
**collected market/signal data is kept** (applies to live). The line is not clean
per-table — decide per-table, sometimes per-row. **Execute LAST** (final pre-go-live
window), via backup → dry-run → guarded idempotent migration (cf. 006/007).

_First pass from the 20-table inventory (2026-06-09). Action ∈ {KEEP, DELETE, SCRUB-COLS, NULL-REFS}. Refine + confirm before any execution._

| Table | Rows | Classification | Proposed action | Notes |
|---|---:|---|---|---|
| `spread_score_trades` | 133 | paper book | **DELETE** | The paper book itself. |
| `trade_ledger_enriched` | 133 | paper book | **DELETE** | Snapshot-at-entry of paper trades. |
| `spread_score_daily` | 730 | paper book | **DELETE** | Daily marks of paper positions. |
| `position_health_snapshots` | 287 | paper book | **DELETE** | Per-paper-position health. |
| `trade_log` | 11 | paper book | **DELETE** | Legacy stock paper trades. |
| `psychological_gap_log` | 1 | paper book | **DELETE** | Paper psych entries. |
| `live_snapshots` | 4,127 | collected data | **KEEP** | Market data. |
| `regime_state` | 3,238 | collected data | **KEEP** | Regime history. |
| `regime_health_snapshots` | 434 | collected data | **KEEP** | |
| `regime_health_composites` | 186 | collected data | **KEEP** | |
| `bear_call_census_daily` | 31 | collected data | **KEEP** | |
| `cycle_qualifier_runs` | 4,622 | collected data (signal output) | **KEEP + NULL-REFS** | Keep the signal record; but `qualifier_run_date` outcome-links to purged trades → scrub/null the links. |
| `alert_thresholds` | 47 | config | **KEEP** | |
| `ai_advisor_cache` / `ai_pre_cycle_cache` / `ai_macro_brief_cache` | 8 | regenerable cache | **KEEP** (or clear) | Regenerates; harmless either way. |
| ⚠️ `schwab_fills` | 7 | **REAL account data** | **KEEP (review per-row)** | Holds real CD/HCA reads, **not paper** — must NOT be blanket-deleted. |
| `cohort_changes` | 474 | process/research | **KEEP (tentative)** | Auto-promotion decisions; decide whether promotion history carries to live. |
| `alert_history` | 391 | system output archive | **DECIDE** | References paper recs; keep as archive or scrub paper rows? |
| `daily_alert_runs` | 22 | system output archive | **DECIDE** | Same question as alert_history. |

**Open manifest decisions:** (1) keep vs scrub the alert archives; (2) does promotion
history (`cohort_changes`) carry into live; (3) exact NULL-REFS plan for
`qualifier_run_date` and any `trade_id` foreign-ish links once the book is purged.

## F. Risk controls & money safety

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| F1 | Defined-risk only; no naked exposure can be placed live | T0 | ⬜ | `user_trading_focus`. |
| F2 | Crash-hedge sizing reviewed for live book | T2 | ⬜ | Revisit early Sept — `project_crash_hedge_sizing`. |
| F3 | Position-size / concentration caps enforced on live orders | T1 | ⬜ | |
| F4 | Fees applied to live P/L (paper was gross-only) | T2 | ⬜ | `feedback_paper_pnl_gross_only` — Schwab supplies at live. |

## G. Monitoring & observability  *(overlaps the active Daily-Alert formatting work)*

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| G1 | Daily alert is **clear and unambiguous** (the active formatting workstream) | T2 | 🟡 | **Round 1 done 2026-06-09:** fixed token-refresh stdout leak (now cron-log only), de-duplicated errors, iron_condor handled as "Not priced" (no false "mark daemon disabled"), removed redundant 52-week section, relabeled IF-cohort as context-only, redesigned close-side table (LIMIT/P&L action-first, cr/db entry tags, mid→worst fill range). **Remaining:** deeper cross-section redundancy (a stressed name still appears in OPEN TRADES + POSITION HEALTH + PSYCH-GAP + CLOSE CANDIDATES) — needs a structural decision, not done. |
| G2 | Dashboards reflect live (not paper) state post-purge | T2 | ⬜ | |
| G3 | Psych-gap log / discipline prompts adapted for live | T3 | ⬜ | |

## H. Docs & runbook  *(item 2)*

| # | Item | Tier | Status | Evidence / notes |
|---|---|---|---|---|
| H1 | `PROJECT_OVERVIEW.md` → shareable explanatory text + user's guide | T2 | 🟡 | Parallel workstream — `project_go_live_plan`. |
| H2 | Operator runbook: daily flow, what each cron does, how to intervene | T2 | ⬜ | |
| H3 | Go-live cutover checklist (the literal steps on go-live day) | T1 | ⬜ | Includes executing section E. |

---

## Running go/no-go summary

_Update as items close._

- **T0 blockers open:** A1 (live order path), A5 (paper/live separation), C3 (loss-cap/stop on live), F1 (defined-risk enforcement). All 🔴/⬜.
- **Next actions:** scaffold complete → begin working A (execution) and E (manifest) in parallel; G1 runs as its own formatting thread.
