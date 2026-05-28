# Auto-Promotion Pipeline — Pre-Registration

**Status: DRAFT awaiting user seal (2026-05-18).**
**Purpose:** Replace the manual quarterly cohort refresh with a continuous nightly pipeline that scans the full ORATS-available universe (~5,864 tickers), liquidity-filters, walk-forwards qualifying candidates, and auto-promotes / auto-demotes names against a pre-registered rule. Once shipped, the quarterly cron is retired.

**Pairs with:** `docs/H2_PHASE2_PREREG.md` (filter-time exclusion gate, separate pre-reg). The pipeline and the H2 gate are orthogonal: the pipeline decides cohort membership; H2 decides daily eligibility for names already in the cohort.

---

## 1. Why this exists

Current state (as of 2026-05-18):
- 326 tickers tracked in `data/orats/by_ticker/` — chosen ad-hoc through three universe expansions (v1 → v2 → v3) plus manual additions
- Quarterly cohort refresh cron at 6 AM ET on 5th of Jan/Apr/Jul/Oct re-tunes per-ticker parameters (moneyness for bull_put/bear_call, wing-width for IF) for names ALREADY in the cohort
- Quarterly refresh does NOT add new names from the broader pool, does NOT demote names whose walk-forward stops being positive, and does NOT touch the 5,538 tickers ORATS publishes daily that we never extracted

The gap: 326 names is our filter, not ORATS's limit. ORATS daily parquet contains 5,864 distinct tickers (verified 2026-05-18). We look at 5.6% of what's available.

The user-articulated requirement:
> "we want a plan that is largely autonomous but covers our entire universe (all ORATS tickers). Filtering by liquidity is a great idea just to avoid wasting time on obviously not qualified candidates. … if we are going to run late at night, there is no reason why we can't run 500 tickers per night."

500 tickers / weekday × 11 weekdays = ~5,500 names covered every ~2 weeks. Runtime budget: ~4 hours/night for 3 walk-forward studies @ ~30s/ticker. Compatible with overnight 23:00 → 03:00 execution.

---

## 2. Pipeline architecture

Five stages, each idempotent:

### Stage 1 — Daily liquidity scan
- **When:** 22:30 ET weekdays (after ORATS 19:00 pipeline)
- **What:** Read the latest raw daily parquet in `data/orats/parquet/year=YYYY/month=MM/YYYY-MM-DD.parquet`. Column-project to (ticker, cBidPx, cAskPx, cVolu, cOi, pBidPx, pAskPx, pVolu, pOi, stkPx). Aggregate per-ticker liquidity metrics for that single day.
- **Gate (any one criteria failure → drop):**
  - Front-month total OI (call + put) ≥ 10,000 contracts
  - Average daily contract volume ≥ 1,000
  - ATM bid-ask spread ≤ 10% of mid
  - Spot price ∈ [$5, $1,000]
- **Output:** `data/profile/auto_promotion/liquidity_snapshot_YYYY-MM-DD.parquet` — per-ticker pass/fail + liquidity-score (front-month OI for ranking).
- **Runtime:** ~30s.

### Stage 2 — Pick the night's batch
- **When:** 22:35 ET, immediately after Stage 1
- **What:** Maintain a persistent ledger `data/profile/auto_promotion/scan_ledger.parquet` of (ticker, last_evaluated_date, last_walkforward_status). Pick the **500 names** that:
  - Passed liquidity in Stage 1 today
  - Have the OLDEST `last_evaluated_date` (or NEVER evaluated = `NULL`, ranked first)
  - Within an age-tie, rank by liquidity score (highest OI first)
- **Output:** `data/profile/auto_promotion/batch_YYYY-MM-DD.parquet` — the night's 500-ticker list with rationale (age + score).
- **Runtime:** <1 min.

### Stage 3 — Historical extract for new tickers
- **When:** 22:36 ET
- **What:** For any ticker in tonight's batch NOT yet in `data/orats/by_ticker/`, run the `daily_extract.py`-style ingest across the full historical parquet archive (~5 years back). Write to `data/orats/by_ticker/{TICKER}.parquet`. Existing tickers skip this stage.
- **Runtime:** ~5-10 min per new ticker on first encounter; ~0s on revisits. First full pass through 5,538 new tickers spread over ~11 days = ~500/night ingest cost first cycle only; after that, the by-ticker archive is complete and Stage 3 is a no-op.
- **Storage:** estimated 5,500 × ~1 MB = ~6 GB additional disk after first full pass.

### Stage 4 — Per-ticker walk-forward (the 4 studies)
- **When:** 22:45 ET
- **What:** Run the 4 per-structure walk-forward studies on tonight's 500 tickers:
  | Structure | Script | Parquet |
  |---|---|---|
  | bull_put moneyness | `bull_put_moneyness_walkforward.py` | `bull_put_moneyness_walkforward.parquet` |
  | bear_call moneyness | `bear_call_moneyness_walkforward.py` | `bear_call_moneyness_walkforward.parquet` |
  | inverted_fly wing | `inverted_fly_wing_walkforward.py` | `inverted_fly_wing_walkforward.parquet` |
  | ZEBRA per-ticker | `zebra_universe_expansion_walkforward.py` | `zebra_universe_expansion_walkforward.parquet` |
- Each walk-forward uses the same 4-split convention as Phase C work (2021-23, 22-24, 23-25, 24-26).
- Slip assumption: **slip=0.50** (conservative; matches universe v2 pre-reg).
- **Output:** appends per-ticker rows to the existing `*_walkforward.parquet` files; latest run per ticker wins on key (ticker, exit_rule). Source-of-truth is the most-recent row per (ticker, exit_rule).
- **Runtime:** ~30s × 500 × 4 studies ÷ parallelism (assume 1 worker per study, run all 4 in parallel) = ~4 hours.

### Stage 5 — Auto-promotion / auto-demotion check
- **When:** 03:30 ET (after Stage 4 completes)
- **What:** For each (ticker, structure) where the walk-forward updated tonight:
  - If currently in `gate_config.COHORT_*` AND fails the promotion rule below → **DEMOTE** (remove from constant)
  - If currently NOT in `gate_config.COHORT_*` AND passes the promotion rule → **PROMOTE** (add to constant)
  - If already in correct state → no-op
- **Output:** `data/profile/auto_promotion/changes_YYYY-MM-DD.parquet` (audit log) + automatic edit of `scripts/qualifier/gate_config.py` via AST manipulation
- Updates `scan_ledger.parquet` to record (ticker, today, walkforward_status) for every ticker evaluated tonight
- **Runtime:** ~2 min.

---

## 3. The auto-promotion rule (per-structure, all-of)

A (ticker, structure) pair is **PROMOTED** if it passes ALL of:

### Gate A — Liquidity (Section 2 Stage 1, evaluated tonight)
Already filtered upstream; if you reached Stage 5 you cleared it.

### Gate B — Per-ticker walk-forward stability
- ≥ **3 of 4** validation-window splits show **mean P/L > 0**
- AND combined train+val mean P/L on the most recent split ≥ **+$5/contract** (bull_put, bear_call), **+$10/contract** (IF), or **+5% median capture** (ZEBRA)
- AND val N ≥ **12** on the most recent split

### Gate C — Slip robustness
- Repeat Gate B's most-recent split at slip=0.50 → mean P/L still positive (any positive value)
- This is already baked into the walk-forward (slip=0.50 default), so it's a sanity check not a separate run

### Gate D — Concentration cap
- No single year contributes > **50%** of total P/L across all walk-forward splits combined
- Cap modeled on the ZEBRA tier-2 finding where CMG's lift was 96% from 2024 alone — that pattern earns a "concentrated" tag, not auto-promotion

### Gate E — Multiple-comparisons correction
- At the nightly batch level, apply **BH-FDR with q < 0.10** to the combined train+val p-values of all candidates passing Gates A-D. Survivors of the FDR cutoff are the night's auto-promotion set.
- This prevents the inflated false-positive rate that unadjusted per-ticker tests would leak at the 500-ticker scale.

**The rule sealed:** PROMOTE if Gates A AND B AND C AND D AND E all pass. SKIP otherwise.

---

## 4. The auto-demotion rule (per-structure, ANY of)

A (ticker, structure) pair currently in the cohort is **DEMOTED** if any of:

### Gate F — Walk-forward turned negative
- ≤ **1 of 4** validation-window splits show mean P/L > 0
- (i.e., the rule that requires ≥3/4 to promote — failing the symmetric 3/4 reverse threshold triggers demotion)
- Cleaner: if the name would no longer PROMOTE under the rule above (Gate B fails on a re-run), demote.

### Gate G — Liquidity collapse
- ≥ **3 consecutive nightly liquidity scans** fail Gate A (Section 2)
- Catches a name whose options volume has structurally dried up

### Gate H — Open-position protection (override)
- If the trader has an OPEN trade on this (ticker, structure) — i.e., `spread_score_trades WHERE status='open' AND placed=1 AND symbol=ticker AND spread_type=structure_short_name` returns ≥ 1 row — **defer demotion** until the position closes. Reason: demoting under an open trade creates an orphaned position and complicates per-name reporting. The cohort entry is tagged `[DEMOTION_PENDING]` in `gate_config.py` (comment line) so the next-cycle qualifier output flags it.

**The rule sealed:** DEMOTE if Gate F OR Gate G, UNLESS Gate H defers. Otherwise no change.

---

## 5. Storage and ops budget

| Item | Estimate |
|---|---|
| New tickers extracted into `by_ticker/` (first full pass) | ~5,500 × 1 MB = **6 GB** added |
| Daily liquidity snapshot parquets | ~5,864 rows × ~50 bytes = 300 KB/day = 75 MB/year |
| Batch files | ~500 rows × 100 bytes = 50 KB/day = 12 MB/year |
| Changes audit log | ~few rows/day = trivial |
| Walk-forward parquet growth | ~500 rows/night/study × 4 studies × 250 days = 500K rows/year, ~50 MB/year |
| **Total annual growth post-build-out** | **~150 MB/year** (dominated by intermediate logs) |

Disk: confirmed acceptable on user's machine.

Runtime: 4-5 hours per night, runs 22:30 → 03:30 ET. Safely completes before 7:55 ET Agent_Project Schwab health check.

---

## 6. Cron + integration

| Job | Cron | Script | Log |
|---|---|---|---|
| Stage 1 liquidity scan | `30 22 * * 1-5` | `scripts/maintenance/auto_promotion_liquidity_scan.py` | `logs/auto_promotion_liquidity_cron.log` |
| Stages 2-5 (batch + extract + walkforward + promote/demote) | `35 22 * * 1-5` | `scripts/maintenance/auto_promotion_nightly.py` | `logs/auto_promotion_nightly_cron.log` |

Both jobs idempotent. If a night misses, the next night's batch picks up the oldest unevaluated names (so the round-robin self-heals).

### Email notification (added 2026-05-18 per user request)

Stage 5 of `auto_promotion_nightly.py` sends an email summary via `lib/email_alert.send_html_alert()` after every run, regardless of whether any promotion/demotion fired. Email contents:

- **Subject:** `MaxPain Auto-Promotion — N promoted, M demoted, K evaluated — YYYY-MM-DD`
- **Body (text + HTML):**
  - Top-line counts (promoted / demoted / evaluated / batch-size / runtime minutes)
  - List of names promoted (per structure) with one-line stats: `MSFT bull_put — 4/4 splits, $7.12/cyc, val_N=18`
  - List of names demoted with reason (Gate F walk-forward fail / Gate G liquidity collapse)
  - Any open-position deferred demotions (Gate H) annotated
  - Any safety-threshold violations (§8) prominently flagged
  - Link to the night's `changes_YYYY-MM-DD.parquet` for full audit

Failure modes also emailed:
- Cron exception: subject `MaxPain Auto-Promotion — FAILED YYYY-MM-DD` with traceback in body
- Safety violations halted the writer: subject `MaxPain Auto-Promotion — HALTED (safety) YYYY-MM-DD` with violation list

This is the same email infrastructure that powers `daily_alert.py` and `schwab_health_check.py`.

**Quarterly cohort refresh cron is RETIRED** when auto-promotion ships. The retire step is in Item 4 of the user's master plan (post-Items-2-and-3, with TRADING_PLAN.rtf + PROJECT_OVERVIEW.md updates).

---

## 7. What we are NOT changing

- The **gate_config cohort lists** remain the source of truth for the qualifier. Auto-promotion writes to them via AST edits; manual edits are still possible (e.g., emergency demotion). Manual + auto-promoted coexist by design.
- The **per-ticker moneyness recommendation parquets** (`bull_put_moneyness_recommendation.parquet`, etc.) — currently updated by the quarterly refresh. Auto-promotion does NOT touch these. A separate question: do per-ticker recommendations need re-tuning beyond what walk-forward gives us? Punted to a future pre-reg if needed.
- The **mechanical gates** (H1, contango+VRP, IF term-inv, sector cap of 2) — these are validated and untouched. Auto-promotion changes cohort membership, not the gates themselves.
- **`COHORT_ZEBRA_OVERLAY_AUTO`** — set 2026-05-17, hand-curated. Auto-promotion does NOT modify it. Overlay attach is a separate decision from cohort membership.
- The **H2 weakness exclusion gate** — separate pre-reg. The pipeline produces cohort lists; H2 (when shipped per Phase 2) filters individual entries from that list at trade-time.

---

## 8. What can falsify the work

- **Promotion-rate explosion:** if any single night promotes > 50 names, the rule is mis-calibrated; freeze auto-promotion, investigate the gate that's leaking
- **Demotion-rate explosion:** if any single night demotes > 5 names, similar; freeze
- **Cohort size > 200 in any single structure:** sanity check on accumulated growth. Existing structure sizes are 30 (bull_put), 14 (bear_call), 31 (IF), 21 (ZEBRA), 5 (anti-ZEBRA). Cap suggests we'd grow ~3-5× before triggering the brake.
- **Live-book underperformance on auto-promoted names:** track a 6-cycle rolling P/L on auto-promoted vs hand-curated. If auto-promoted underperform by > 25% on average across 6 cycles → suspend auto-promotion, investigate
- **Stage 4 runtime > 6 hours:** breaches the overnight budget; reduce batch size or parallelize harder

If any falsification trigger fires, the pipeline halts and the failure mode goes into a follow-up memo. No back-fitting of the promotion rule mid-flight.

---

## 9. Negative-result plan

If after 2 full universe passes (~24 days of nightly runs) the pipeline has promoted < 5 net new names:
- The rule is too tight; document the failure, and consider per-gate threshold relaxation in a Phase 2 of THIS pre-reg (would require a NEW seal, not an edit of this one)

If after 2 full universe passes the pipeline has produced an obviously-broken cohort (per Section 8 falsification triggers):
- Halt; revert any auto-promoted names; do a forensic on which gate leaked

---

## 10. Build artifacts (to be created post-seal)

- `scripts/maintenance/auto_promotion_liquidity_scan.py` — Stage 1
- `scripts/maintenance/auto_promotion_nightly.py` — Stages 2-5 driver
- `scripts/maintenance/auto_promotion_gate_check.py` — Section 3 + 4 rule evaluator
- `scripts/maintenance/auto_promotion_gate_config_writer.py` — AST-based safe writer for `gate_config.py`
- `data/profile/auto_promotion/` directory — snapshots, batches, ledger, changes
- `lib/auto_promotion.py` — shared helpers (BH-FDR, ledger I/O, ticker-row evaluation)

---

## 11. Sign-off

**Drafted by:** Claude Opus 4.7
**Drafted on:** 2026-05-18
**Sealed-by:** [pending user seal]
**Sealed-on:** [pending]

Once sealed, no analysis logic is written before the seal date. Build artifacts in Section 10 may be implemented after seal.

---

## 12. Cross-references

- `docs/UNIVERSE_EXPANSION_V2_PREREG.md` — methodology source (liquidity gates, walk-forward, BH-FDR)
- `docs/H2_PHASE2_PREREG.md` — companion pre-reg (filter-time exclusion gate)
- `project_zebra_overlay_tier2_findings.md` — concentration-cap precedent (CMG case)
- `project_universe_expansion_v3.md` — most recent manual expansion (v3, 5/6); future expansions retire
- `project_post_june_opex_watchlist.md` — item 1 (auto-promotion) merged with H2 Phase 2 (item 11b)
