# ZEBRA Overlay — Active-Bear Deepening Rule Pre-Registration

**Sealed:** 2026-05-17
**Author:** Joseph Morris (with Claude Code)
**Predecessors:** `project_zebra_overlay_strike_grid_findings.md`, `project_zebra_overlay_phase2_complete.md`
**Implementation already live:** `scripts/monitor/zebra_overlay_rule.py`

## Context

The strike-grid finding (2026-05-14, tier-1 cohort, 934 cycles, slip=0.25) revealed that the optimal long-put overlay strike on a ZEBRA depends on the regime stage at entry. Phase 1 + grid sweep + Phase 2 results converged on a four-state regime-conditional rule.

The four-state rule was implemented in `scripts/monitor/zebra_overlay_rule.py` and is consumed by `scripts/monitor/daily_alert.py` and `scripts/monitor/trade_construction.py`. The rule has been operationally live since 2026-05-14.

This pre-reg formalizes the rule, documents the N=1 caveat on the active-deepening sub-rule, and locks in falsification criteria so the next bear cycle generates a clean validation/rejection decision rather than a post-hoc reinterpretation.

## The rule (sealed)

```
At ZEBRA entry (75 DTE before parent OpEx), select long-put strike per:

  bear_gate_open AND active_deepening  →  ITM5  (+5% above spot)
  bear_gate_open AND NOT active_deepening (troughing/unwinding)
                                       →  OTM10 (-10% below spot)
  Stage 3 OR any cascade ring yellow   →  ATM   (0%)
  Stage 1-2 AND all cascade rings green →  OTM10..OTM15 (-10..-15% below spot)

All overlays match parent ZEBRA expiration. Both legs held to OpEx.
```

Operational definitions (matching the live code):

- `bear_gate_open` = `regime_state.stage >= 4` OR `≥2 cascade rings red` OR `(h1_active=1 AND below_200dma=1)`
- `active_deepening` = SPY made a new 60-calendar-day low within the past 30 calendar days. Computed daily by `_spy_active_deepening()` in `scripts/monitor/zebra_overlay_rule.py`.
- `cascade rings` = AI / QQQ / SPY component status from `regime_health_snapshots`, latest snapshot per family.
- `Stage` = `regime_state.stage` (1–5 per the Regime Transition v1.7 framework).

## Empirical basis (sealed at write time)

From the 2022-2024 walk-forward window (one regime sample of "bear gate open" in the 13-year ORATS history):

| Sub-period | Regime descriptor | ITM5 lift over BARE | OTM10 lift over BARE | ITM5 vs OTM10 |
|---|---|---|---|---|
| 2022 | Active deepening (SPX peak Jan → trough Oct, multiple new 60d lows) | +$193.73/cyc | +$144.57/cyc | **+$49.16/cyc** ✓ |
| 2023 | Bear unwinding (recovery from trough, no new 60d lows) | −$14.66/cyc | −$5.37/cyc | −$9.29/cyc |
| 2024 | Recovery / above-trend | +$14.87/cyc | +$23.66/cyc | −$8.79/cyc |

ITM5 wins in 2022 (active deepening); OTM10 wins in 2023-2024 (post-deepening even under "bear gate" framing). The 60d-low recency test partitions the regime into "deepening" vs "troughing/recovery" and the strike rule switches accordingly.

## N=1 caveat (sealed)

The active-deepening sub-rule is supported by a **single regime sample** (the 2022 drawdown). 2008 and 2020 were structurally different events and are not present in the ORATS sample. Directionally clear, statistically thin. This pre-reg's purpose is to capture the rule formally so the next bear regime can validate or reject it without back-fitting.

## Falsification criteria (sealed — do NOT relax these without a new pre-reg)

The next bear cycle generates the validation sample. "Next bear" = the next contiguous period where `bear_gate_open` is true on at least 60 of 90 consecutive trading days (defines a real regime, not a single yellow flag). When the next bear closes (`bear_gate_open` false for 60+ consecutive trading days), run the validation analysis:

**Validation analysis:**
1. Identify "deepening entries" = ZEBRA cycles opened during the next bear where `active_deepening` was true on the entry date.
2. Identify "non-deepening entries" = ZEBRA cycles opened during the next bear where `bear_gate_open` was true but `active_deepening` was false.
3. For each subset, compute cohort-mean P/L of ITM5 vs OTM10 overlay (both held to expiry, slip=0.25).
4. Minimum N: ≥ 7 entries per subset (one cycle per tier-1 name). If fewer, mark as "insufficient sample" and defer to subsequent bear.

**Decision rule (sealed):**

| Outcome | Verdict | Action |
|---|---|---|
| Deepening subset: ITM5 beats OTM10 by ≥$20/cyc (half the 2022 effect) AND non-deepening subset: OTM10 beats ITM5 by ≥$0 | **VALIDATED** | Keep the rule. Promote N caveat from "one bear sample" to "two bear samples." |
| Deepening subset: ITM5 beats OTM10 by $0 to $20/cyc OR direction-consistent but insufficient margin | **WEAKLY VALIDATED** | Keep the rule operational. Caveat persists until a third bear sample arrives. |
| Deepening subset: ITM5 loses to OTM10 (any margin) | **REJECTED** | Revert to: bear_gate_open uniformly uses OTM10 regardless of deepening state. Update `zebra_overlay_rule.py` to remove the deepening branch. The two-state rule (bear/non-bear) replaces the four-state. |
| Insufficient N in either subset | **DEFERRED** | Wait for the bear after next. Operational rule unchanged. |

The Stage 1-2, Stage 3, and OTM10 (troughing) branches of the rule do NOT depend on the deepening sub-rule and are not affected by this validation. Those three branches are validated by Phase 1 + strike-grid + Phase 2 walk-forward and have multi-regime support.

## Inputs that could falsify the rule independently (sealed)

If any of the following changes, the rule must be re-evaluated regardless of bear sample:

1. The regime classifier upstream of `bear_gate_open` changes (Regime Transition v1.7 → v2.x, or cascade-ring logic changes). The strike-by-regime rule is conditional on the classifier; if the classifier shifts, the rule's optimality shifts with it.
2. The 60-day low + 30-day recency thresholds change (currently `DEEPENING_LOW_WINDOW=60`, `DEEPENING_RECENCY=30` in `zebra_overlay_rule.py`). Tunable changes invalidate the empirical basis.
3. The slip assumption changes (currently 0.25 from Phase C / Phase 1). ITM puts have wider real-world bid-asks in stressed markets; if slip is increased to ≥0.50 the cost calculus may flip back toward OTM.
4. The tier-1 cohort changes substantively. The 2022 evidence is the 7-name tier-1 sample; cohort changes invalidate the per-cycle aggregation.

## What promotion looks like (sealed — what success means for next-bear validation)

If next bear validates:
- Update this pre-reg's status to **VALIDATED 2026-XX-XX (N=2 bear samples)** with a brief findings section.
- Update `project_zebra_overlay_phase2_complete.md` memory to remove the N=1 caveat.
- No code change (rule is already live).

If next bear rejects:
- Update this pre-reg's status to **REJECTED 2026-XX-XX**.
- Modify `scripts/monitor/zebra_overlay_rule.py` to remove the active-deepening branch.
- Update `daily_alert.py` and `trade_construction.py` if they depend on the four-state branch labels.
- Add a memory `project_zebra_overlay_bear_deepening_rejected.md` documenting the validation analysis.

## Status

**SEALED 2026-05-17.** Rule is operationally live. Awaiting next-bear validation sample.

## Cross-references

- `project_zebra_overlay_strike_grid_findings.md` — strike-grid sweep findings (basis for the four-state rule)
- `project_zebra_overlay_phase2_complete.md` — full Phase 2 results including N=1 stability test
- `project_regime_cascade_early_warning.md` — three-ring cascade logic (input to bear_gate_open)
- `project_regime_health_monitor.md` — regime_state + regime_health_snapshots schema
- Code: `scripts/monitor/zebra_overlay_rule.py`
- Wiring: `scripts/monitor/daily_alert.py`, `scripts/monitor/trade_construction.py`
