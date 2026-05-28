# Bull-Put 200-DMA Cross Exit Rule — Pre-Registration

**Status: SEALED 2026-05-19 by user.**

**Purpose:** Test whether adding a 200-DMA cross-below exit rule to bull-put positions improves cohort P/L versus the existing exit-rule stack (held-to-expiry baseline + managed-at-50% target close).

**Origin:** Phase 1 exploratory analysis (2026-05-19, `scripts/backtest/bull_put_ma_cross_during_hold.py`) showed that bull-put cycles entered above the underlying's 200-DMA which CROSSED BELOW during the hold returned -$0.93/share mean held to expiry vs +$0.45/share for those that stayed above. The crossed cohort (31.8% of above-entry cycles) essentially cancels the entire bull-put edge.

---

## 1. Why this exists

Current bull-put exit-rule stack:
1. **Managed-at-50%** — close when mark ≤ 0.5 × entry_credit (sealed in plan)
2. **T-21 management cue** — close around DTE 14-25 regardless of capture % (universal rule, shipped 2026-05-07)
3. **STP LMT GTC at 2× entry credit** — defined stop-loss (shipped 2026-05-07)
4. **Held-to-expiry** — fallback if nothing else fires

Phase 1 showed the existing stack fails to catch the structural-damage subset: cycles where the underlying crosses its own 200-DMA during the hold. The mgd50 rule rescues ~27% of the loss in that subset (-$0.93 → -$0.67), but the cohort is still net negative. **An exit-at-cross rule may capture the position before vol expansion + further decay take it to max loss.**

## 2. Conceptual basis

A bull-put profits when the underlying drifts flat-to-up. The 200-DMA cross is empirically a **structural break** in the trend — Phase 1 showed mean P/L flips sign (+$0.45 → -$0.93) and win rate collapses (90% → 47%) when the crossed flag fires. Three reasons an exit-at-cross might work:

1. **Vol expansion has begun but hasn't peaked.** At the moment of cross, the spread is more expensive than entry but typically not yet at max-loss territory. Exiting before further deterioration locks in a manageable loss vs. riding to expiry.
2. **The directional thesis is broken.** A position predicated on "the underlying will stay above its long strike" loses its premise when the underlying breaks long-term trend.
3. **The mgd50 rule won't save crossed cycles.** Once the 200-DMA cross happens, the position is usually already too damaged for mark to come back down to 50% target. The mgd50 exit is foreclosed.

## 3. Sealed exit rule

A bull-put position **CLOSES via cross-exit** on the first trading day D where ALL of:

- Spot close on D < **0.98 × own 200-DMA** on D (small buffer to avoid one-day fake-outs at the exact MA line)
- **At least 5 trading days** have elapsed since entry (filter same-cycle whipsaws)
- Spot was **at or above** the 200-DMA at entry (cycles entered below the 200-DMA use the existing exit stack — out of scope for this rule)
- The position is still open (mgd50 did not already fire)

Exit price = the day-D mid-cost to close (`close_cost(pos, day_D_chain)`), same convention as the existing mgd50 exit logic in `scripts/backtest/structures.py`.

## 4. Validation methodology

### Step 0 — Add `cross_exit_pnl` column
Extend `scripts/backtest/bull_put_moneyness_backtest.py`'s `simulate_one()` to track the cross-exit trigger during the forward loop. New per-cycle outputs:
- `cross_exit_triggered` (0/1)
- `cross_exit_date` (date or NaN)
- `cross_exit_pnl` (per-share P/L; equals held_pnl if rule never fires)

For cycles where mgd50 fired BEFORE the cross-exit conditions were met, `cross_exit_pnl` defaults to `mgd50_pnl` (the existing rule wins on time priority).

### Step 1 — Compute combined-rule P/L
The proper comparison is "current rule stack + cross-exit" vs "current rule stack alone." Define:
- **Baseline (current stack):** `mgd50_pnl` (which already incorporates the 50% target; equals held_pnl if mgd50 didn't fire)
- **Combined rule:** `min_exit_pnl = whichever fires first between mgd50 and cross-exit`

### Step 2 — Cohort restriction
Apply the comparison **only to cycles entered above the 200-DMA** (the Phase 1 cohort). Cycles entered below are out of scope (rule doesn't apply by design).

### Step 3 — Gate evaluation
Five sealed gates, all must pass.

## 5. Sealed decision rule (all of)

### Gate A — Pooled improvement in the crossed cohort
- Among cycles entered above 200-DMA that crossed below during hold (Phase 1's "Cell B" subset):
- **Combined rule mean P/L − Baseline (mgd50) mean P/L ≥ +$0.20/share**
- Baseline reference from Phase 1: -$0.67/share. Combined rule must hit ≥ -$0.47/share or better.

### Gate B — No harm to the un-crossed cohort
- Among cycles entered above 200-DMA that did NOT cross during hold (Phase 1's "Cell A"):
- **Combined rule mean P/L ≥ Baseline mean P/L − $0.05/share** (small tolerance for noise)
- Rule should not damage the cycles where it doesn't fire. By design it shouldn't, but sanity-check.

### Gate C — Walk-forward stability
- Compute Gate A's improvement in each of the 4 standard validation windows (2021-23, 22-24, 23-25, 24-26)
- **Improvement ≥ +$0.10/share in ≥ 3 of 4 windows**

### Gate D — Concentration cap
- Of the combined improvement (sum across all firing cycles), no single calendar year contributes > **50%** of the total
- Mirror of the auto-promotion Gate D pattern

### Gate E — Sample adequacy
- ≥ **500** cycles where the cross-exit rule actually fires (i.e., the rule was the binding constraint, not mgd50)
- Phase 1 had 5,091 crossed cycles in scope; expect cross-exit to fire on a large subset

## 6. What promotion looks like

If all five gates pass:

1. **`scripts/backtest/structures.py`** — no change (exit logic is per-strategy not core)
2. **`scripts/qualifier/cycle_qualifier.py`** — add daily management cue for OPEN bull-put positions: surface `MA200_CROSS_EXIT` annotation when underlying ticks the rule conditions
3. **`scripts/monitor/daily_alert.py`** — render an actionable management card for any open bull-put where the trigger has fired today
4. **`docs/TRADING_PLAN.rtf`** — v2.5 paragraph documenting the rule as a management cue ranked alongside T-21
5. **Live integration** — user discretion on whether to wire as auto-execute or alert-only (alert-only is the cleaner first step; manual close based on alert)

## 7. What we are NOT testing

Locked-out from this pre-reg:

- **Alternative MA windows** (50-DMA, 100-DMA, 50/200 cross). Phase 1 hinted 200 is the cleaner break; not testing all permutations.
- **Alternative buffer values** (0.95, 0.97, 1.00 × MA200). 0.98 is sealed.
- **Alternative cool-down periods** (3, 7, 10 trading days). 5 is sealed.
- **Extending the rule to bull-puts entered BELOW the 200-DMA.** Out of scope. Those cycles' existing rule stack is fine.
- **Extending to other structures** (bear_call, IF, ZEBRA). Separate per-structure validation if pursued.
- **Re-entry rule** — if the underlying crosses back above after exit, do we re-open? No. Single-shot rule.
- **Combining with the H2 Phase 2 R3 entry filter** (sealed-and-rejected this morning). Different use case.

## 8. Negative-result plan

If any of Gates A-E fails:
- **No promotion.** Bull-put exit stack remains unchanged (mgd50 + T-21 + stop-limit).
- A rejection memo records which gate failed and the per-window breakdown.
- **No immediate variant retest.** A future variant pre-reg requires a new conceptual basis distinct from "tweak to make the failed version pass."

If Gate A passes but Gate C (walk-forward) fails:
- Document the regime-dependence. This means the cross-exit rule works in some periods but not others — suggests a regime-conditional rule, which would be a fresh pre-reg.

## 9. Falsification triggers (post-promotion, if applicable)

If the rule promotes and goes live, the following would trigger kill-switch review:
- 6 consecutive live triggers where the rule's exit was worse than holding (using forward-30-day spot path as the counterfactual)
- Annual P/L attributable to the rule turns negative on a 6-cycle rolling basis
- Rule fires on > 60% of live bull-puts in a single OpEx cycle (suggests broad regime, possibly H1 territory where bull-puts shouldn't be on)

## 10. Build artifacts (post-seal)

- Extend `scripts/backtest/bull_put_moneyness_backtest.py` with cross-exit tracking in `simulate_one()`
- New script `scripts/backtest/bull_put_ma200_cross_validation.py` runs Gates A-E + emits per-gate results
- `data/profile/bull_put_ma200_cross_validation.parquet` — per-gate verdicts + cohort rollup
- `reports/bull_put_ma200_cross_validation_YYYY-MM-DD.md` — findings + GO/NO-GO

If promoted:
- `scripts/qualifier/cycle_qualifier.py` + `scripts/monitor/daily_alert.py` updates per §6

## 11. Effort estimate

- Backtest extension (cross-exit logic in simulate_one): ~1 hour
- Re-run on the 27K OTM bull-put cycles: ~10-20 minutes
- Validation script + report: ~1 hour
- Promotion integration (only if all 5 gates pass): ~2-3 hours

Total if reject: ~2.5 hours. Total if promote: ~5 hours.

## 12. Sign-off

**Drafted by:** Claude Opus 4.7
**Drafted on:** 2026-05-19
**Sealed-by:** user
**Sealed-on:** 2026-05-19

Sealed. Build artifacts in §10 may be implemented.

## 13. Cross-references

- `scripts/backtest/bull_put_ma_cross_during_hold.py` — Phase 1 exploratory study that motivates this pre-reg
- `data/profile/bull_put_ma_cross_during_hold.parquet` — Phase 1 results
- `project_credit_spread_stop_policy.md` — existing stop discipline (STP LMT GTC at 2× credit)
- `project_t21_management_discipline.md` — universal T-21 time exit
- `project_bullput_below_ma_findings.md` — earlier MA-bucket study on ENTRY (this pre-reg is on HOLD-PERIOD CROSS, different question)
- `docs/AUTO_PROMOTION_PIPELINE_PREREG.md` — companion pre-reg discipline (gate structure mirrored)
- `feedback_backtest_held_to_expiry_lower_bound.md` — never reject managed-exit on held-to-expiry baseline (this pre-reg honors that by comparing against mgd50 not held)
