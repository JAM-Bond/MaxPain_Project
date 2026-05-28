# Bear-Call 200-DMA Cross Exit Rule — Pre-Registration

**Status: SEALED 2026-05-20 by user.**

**Purpose:** Test whether adding a 200-DMA cross-above exit rule to bear-call positions improves cohort P/L versus the existing exit-rule stack (held-to-expiry baseline + managed-at-50% target close).

**Origin:** Phase 1 exploratory analysis (2026-05-20, `scripts/backtest/bear_call_ma_cross_during_hold.py`) showed that bear-call cycles entered below the underlying's 200-DMA which CROSSED ABOVE during the hold returned −$0.95/share mean held to expiry vs +$0.24/share for those that stayed below. The crossed cohort (41.8% of below-entry cycles) drives the un-gated cohort to net −$2,618 across 10,243 cycles.

---

## 1. Why this exists

Current bear-call exit-rule stack:
1. **Managed-at-50%** — close when mark ≤ 0.5 × entry_credit (sealed in plan)
2. **T-21 management cue** — close around DTE 14-25 regardless of capture % (universal rule)
3. **STP LMT GTC at 2× entry credit** — defined stop-loss
4. **Held-to-expiry** — fallback if nothing else fires
5. **H1 entry gate** — SPY < 200dma + IVR > 0.5 (filters most low-edge entries upstream)

Phase 1 showed the existing stack fails to catch the structural-damage subset: cycles where the underlying crosses its own 200-DMA UPWARD during the hold. The mgd50 rule rescues ~24% of the loss in that subset (−$0.95 → −$0.72), but the crossed cohort is still net negative. **An exit-at-cross rule may capture the position before further rally + decay take it to max loss.**

## 2. Conceptual basis

A bear-call profits when the underlying drifts flat-to-down. The 200-DMA cross-above is empirically a **structural break** in the downtrend — Phase 1 showed mean P/L flips sign (+$0.24 → −$0.95) and win rate collapses (87% → 45%) when the crossed flag fires. Three reasons an exit-at-cross might work — AND one specific reason the bull-put symmetric Phase 2 failure may NOT project here:

1. **The directional thesis is broken.** A position predicated on "the underlying will stay below its short strike" loses its premise when the underlying breaks long-term trend upward.
2. **The mgd50 rule won't save crossed cycles.** Once the 200-DMA cross-above happens, the position is usually already too damaged for mark to come back down to 50% target.
3. **Vol asymmetry vs bull-put rejection.** The bull-put symmetric pre-reg was rejected because cross-below-200-DMA fires at a vol-EXPANSION peak — exit is expensive. The bear-call mirror event fires when spot is RISING through its MA. Equity vol typically COMPRESSES in rallies (VIX falls with rising SPY). If this holds at the cross-above event, the exit cost should be relatively cheap, potentially making the rule's P/L lift survive where the bull-put version did not.

This vol-asymmetry hypothesis is the central reason we are running this Phase 2 despite the bull-put rejection. The validation will implicitly probe it.

## 3. Sealed exit rule

A bear-call position **CLOSES via cross-exit** on the first trading day D where ALL of:

- Spot close on D > **1.02 × own 200-DMA** on D (small buffer to avoid one-day fake-outs at the exact MA line — mirror of bull-put's 0.98 buffer)
- **At least 5 trading days** have elapsed since entry (filter same-cycle whipsaws)
- Spot was **at or below** the 200-DMA at entry (cycles entered above the 200-DMA use the existing exit stack — out of scope for this rule)
- The position is still open (mgd50 did not already fire)

Exit price = the day-D mid-cost to close (`close_cost(pos, day_D_chain)`), same convention as the existing mgd50 exit logic in `scripts/backtest/structures.py`.

## 4. Validation methodology

### Step 0 — Add cross-exit columns
Build `scripts/backtest/bear_call_ma200_cross_simulation.py` mirroring the bull-put simulation. New per-cycle outputs:
- `entry_below_ma200` (0/1)
- `cross_exit_triggered` (0/1)
- `cross_exit_date` (date or NaT)
- `cross_exit_pnl` (per-share P/L; equals held_pnl if rule never fires)
- `combined_pnl` (min-time between mgd50 and cross-exit; falls back to held)

### Step 1 — Compute combined-rule P/L
- **Baseline (current stack):** `mgd50_pnl` (which already incorporates the 50% target; equals held_pnl if mgd50 didn't fire)
- **Combined rule:** whichever fires first between mgd50 and cross-exit

### Step 2 — Cohort restriction
Apply the comparison **only to cycles entered below the 200-DMA** (the Phase 1 cohort). Cycles entered above are out of scope (rule doesn't apply by design).

### Step 3 — Gate evaluation
Five sealed gates, all must pass.

## 5. Sealed decision rule (all of)

### Gate A — Pooled improvement in the firing cohort
- Among cycles entered below 200-DMA where the cross-exit rule fired:
- **Combined rule mean P/L − Baseline (mgd50) mean P/L ≥ +$0.20/share**
- Baseline reference from Phase 1: −$0.72/share. Combined rule must hit ≥ −$0.52/share or better.

### Gate B — No harm to the non-firing cohort
- Among cycles entered below 200-DMA where the cross-exit rule did NOT fire:
- **Combined rule mean P/L ≥ Baseline mean P/L − $0.05/share** (small tolerance for noise)
- Rule should not damage the cycles where it doesn't fire. By design it shouldn't, but sanity-check.

### Gate C — Walk-forward stability
- Compute Gate A's improvement in each of the 4 standard validation windows (2021-23, 22-24, 23-25, 24-26)
- **Improvement ≥ +$0.10/share in ≥ 3 of 4 windows**

### Gate D — Concentration cap
- Of the combined improvement (sum across all firing cycles), no single calendar year contributes > **50%** of the total

### Gate E — Sample adequacy
- ≥ **500** cycles where the cross-exit rule was the binding constraint (i.e., it fired AND was earlier than mgd50 if mgd50 fired at all)
- Phase 1 had 4,286 crossed cycles in scope; expect cross-exit to fire on a large subset

## 6. What promotion looks like

If all five gates pass:

1. **`scripts/qualifier/cycle_qualifier.py`** — add daily management cue for OPEN bear-call positions: surface `MA200_CROSS_EXIT` annotation when underlying ticks the rule conditions (spot > 1.02 × MA200 + ≥5d since entry + entered below)
2. **`scripts/monitor/daily_alert.py`** — render an actionable management card for any open bear-call where the trigger has fired today
3. **`docs/TRADING_PLAN.rtf`** — v2.5 paragraph documenting the rule as a management cue ranked alongside T-21
4. **Live integration** — alert-only first; manual close based on alert

## 7. What we are NOT testing

Locked-out from this pre-reg:

- **Alternative MA windows** (50-DMA, 100-DMA, 50/200 cross). Phase 1 hinted 200 is the cleaner break.
- **Alternative buffer values** (1.01, 1.03, 1.05 × MA200). 1.02 is sealed (mirror of bull-put's 0.98).
- **Alternative cool-down periods** (3, 7, 10 trading days). 5 is sealed.
- **Extending the rule to bear-calls entered ABOVE the 200-DMA.** Out of scope.
- **Combining with the H1 entry gate.** The simulation uses un-gated entries (consistent with bull-put pre-reg). Whether the rule's lift survives ON TOP OF the H1-filtered live entries is a separate forward-test question post-promotion.
- **Re-entry rule** — if the underlying crosses back below after exit, do we re-open? No. Single-shot rule.

## 8. Negative-result plan

If any of Gates A-E fails:
- **No promotion.** Bear-call exit stack remains unchanged (mgd50 + T-21 + stop-limit + H1 entry gate).
- A rejection memo records which gate failed and the per-window breakdown.
- This becomes the 5th example in the "real Stage-2 forensic signal, no tradeable mechanic" pile (after bull-put cross, H2 Phase 2 R1-R5, sector-ETF Stage-2 entry, sector-ETF Stage-2 standalone).
- **No immediate variant retest.** A future variant pre-reg requires a fresh conceptual rationale, not a tweak.

If Gate A passes but Gate C (walk-forward) fails:
- Document the regime-dependence. Suggests a regime-conditional rule, which would be a fresh pre-reg.

## 9. Falsification triggers (post-promotion, if applicable)

If the rule promotes and goes live, the following would trigger kill-switch review:
- 6 consecutive live triggers where the rule's exit was worse than holding (using forward-30-day spot path as the counterfactual)
- Annual P/L attributable to the rule turns negative on a 6-cycle rolling basis
- Rule fires on > 60% of live bear-calls in a single OpEx cycle

## 10. Build artifacts (post-seal)

- `scripts/backtest/bear_call_ma200_cross_simulation.py` — mirror of bull_put_ma200_cross_simulation.py with `open_bear_call` and reversed cross condition (`spot > 1.02 × ma200`)
- `scripts/backtest/bear_call_ma200_cross_validation.py` — mirror of bull_put validation; identical gate thresholds
- `data/profile/bear_call_ma200_cross_results.parquet` — per-cycle simulation output
- `data/profile/bear_call_ma200_cross_validation.parquet` — per-gate verdicts
- `reports/bear_call_ma200_cross_validation_YYYY-MM-DD.md` — findings + GO/NO-GO

If promoted:
- `scripts/qualifier/cycle_qualifier.py` + `scripts/monitor/daily_alert.py` updates per §6

## 11. Effort estimate

- Simulation script (mirror of bull-put): ~30 min
- Run on the 27K OTM bear-call cycles: ~10-20 minutes
- Validation script (mirror) + report: ~30 min
- Promotion integration (only if all 5 gates pass): ~2-3 hours

Total if reject: ~1.5 hours. Total if promote: ~4 hours.

## 12. Sign-off

**Drafted by:** Claude Opus 4.7
**Drafted on:** 2026-05-20
**Sealed-by:** user
**Sealed-on:** 2026-05-20

Sealed. Build artifacts in §10 may be implemented.

## 13. Cross-references

- `docs/BULL_PUT_MA200_CROSS_EXIT_PREREG.md` — symmetric pre-reg (REJECTED 5/19); methodology mirrored
- `scripts/backtest/bear_call_ma_cross_during_hold.py` — Phase 1 exploratory study that motivates this pre-reg
- `data/profile/bear_call_ma_cross_during_hold.parquet` — Phase 1 results
- `project_bear_call_ma_cross_findings.md` — Phase 1 findings memo with vol-asymmetry hypothesis
- `project_bull_put_ma200_cross_rejected.md` — bull-put Phase 2 rejection (vol-peak explanation)
- `project_credit_spread_stop_policy.md` — existing stop discipline
- `project_t21_management_discipline.md` — universal T-21 time exit
- `project_bearcall_below_ma_findings.md` — 5/4 75K-cycle un-gated baseline (all cells negative)
- `feedback_backtest_held_to_expiry_lower_bound.md` — comparing against mgd50 not held (honored)
