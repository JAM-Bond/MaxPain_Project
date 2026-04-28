# Signal Validation Pre-Registration — May 15 2026 OpEx Cycle

**Sealed: 2026-04-26.** Document pre-registers each signal's expected behavior BEFORE May 15 cycle closes, so post-cycle measurement cannot be retroactively rationalized.

This is a forward-test of the SPY-regime signals captured daily in the `regime_state` table. Backtest evidence is cited per signal; the question this doc tests is whether **backtest-validated signals forward-test on the live May 15 cycle**.

---

## Cohort under test

- 30 placed=1 open positions on the May 15 OpEx cycle (29 spread positions + 1 closed AXP). Trades entered 2026-04-16 through 2026-04-24.
- Plus 1 protected NVDA ZEBRA entered 2026-04-26 (id=101) targeting July 17 OpEx.
- Cycle close: 2026-05-15 (spread positions); July 17 (ZEBRA).

## Pre-registration discipline

For each signal below:
- **Claim** — what the signal predicts
- **Backtest evidence** — cited memory + key lift number
- **Forward-test prediction** — what we expect to see in May 15 outcomes
- **Falsification** — what result would force us to drop or reweight the signal

If May 15 results contradict any of these predictions, the signal is downgraded — even if the post-mortem narrative wants to rationalize it.

---

## Signal 1 — bull_put_signal_active (contango + VRP>0)

- **Claim**: a bull_put trade entered on a day when SPY term spread is in contango (front<back) AND VRP is positive will outperform the same trade entered on other days.
- **Backtest evidence**: `project_mp_phase2f_rescue.md`. On 1,307 bull_put_mp cycles: filtered cohort mean +$0.019 vs unfiltered −$0.002 per share. 57% of cycles satisfy. Worst case −$4.57 (vs −$6.29 baseline).
- **Forward-test prediction (May 15)**: bull_put trades placed during contango+VRP>0 windows will close with **HIGHER mean P/L** than bull_put trades placed during other regimes. Lift expected: positive but small at this N (~24 placed bull_puts). Even directional lift counts as forward-validation.
- **Falsification**: filtered-cohort mean P/L is **less than or equal to** unfiltered-cohort mean P/L. Or worst case wider than baseline.

## Signal 2 — h1_active (SPY<200dma + IVR>0.5)

- **Claim**: a bear_call trade entered on a day when h1_active=ON will outperform the same trade entered on other days.
- **Backtest evidence**: `project_bear_call_h1_h3_findings.md`. On 102 cycles where h1 fired: mean +$0.092 vs baseline −$0.087. 40% win rate vs 21.7%.
- **Forward-test prediction (May 15)**: this is **untestable on the May cycle** because h1_active was OFF for the entire entry window (April 16-24). SPY +5.4% above 200dma. The 8 bear_call positions in the live book were entered IN VIOLATION of the H1 gate. **Prediction**: the bear_calls placed with h1=OFF will show outcomes consistent with the cohort baseline (~21.7% win rate, mean ~−$0.087 per share = ~−$8.70 per contract on the per-share schema convention). If they WIN, that's noise at this N=8, not validation of bear_call without h1.
- **Falsification of the gate's value**: bear_calls without h1 win at significantly higher rates than 21.7% would force a reweight. (Single cycle = noise; need multiple cycles.)

## Signal 3 — if_gate_active (term_spread > 0)

- **Claim**: an inverted_fly trade entered on a day when term_spread > 0 will outperform the same trade entered on contango days.
- **Backtest evidence**: `project_if_phase_a_batch_findings.md`. Lifts mean from +$0.191 to +$0.639 per share (3.3× lift). Win rate 60.2% → 66.6%.
- **Forward-test prediction (May 15)**: **untestable on this cycle** — no inverted_fly trades were placed for May 15. The if_gate_active signal is currently ON (term inverted at +0.0066) but the user has no actual IF positions to validate.
- **Forward-test prediction (July 17)**: inverted_fly entries placed under if_gate_active should outperform any placed under contango. Need to enter at least 1-2 IF positions during May/June to begin measurement.

## Signal 4 — Term spread (continuous, magnitude predictor)

- **Claim**: SPY term spread predicts magnitude of forward moves. Inverted (spread > 0) predicts more movement; deep contango predicts quieter.
- **Backtest evidence**: `project_signal_vrp_termstruct.md`. 13-year SPY: inverted-term days have 20% probability of 45d ≥10% move (vs 7.5% baseline = 2.6× lift). Joint with low VRP: 30% probability of 45d ≥10% move (4× lift).
- **Forward-test prediction (May 15)**: SPY 45-day forward |return| distribution should be wider than baseline given term_spread was inverted from at least Apr 21 onward. Threshold for "validated forward": SPY moves ≥5% in either direction by May 15 from April 21 close ($703.36) — i.e., SPY May 15 close < $668 OR > $738.
- **Falsification**: SPY May 15 close in range $668-$738 with no intracycle move ≥5%.

## Signal 5 — IVR > 0.7 (elevated vol regime)

- **Claim**: IVR > 0.7 predicts elevated forward-move probability and supports the soft-downsize trigger.
- **Backtest evidence**: `project_regime_signals_symmetry.md`. 1.88× lift on forward 45d ≥10% move probability (29.0% vs 15.5% baseline).
- **Forward-test prediction (May 15)**: **partial test** — current IVR is 0.328 (well below 0.7). Trades placed at low IVR shouldn't show heightened movement-of-underlying signal. If anything, the cohort's outcomes should reflect modest moves.
- **Falsification of the IVR>0.7 signal**: would require an IVR>0.7 entry to compare. Not testable this cycle.

## Signal 6 — Pre-downturn composite (3 of 5 signals firing)

- **Claim**: when 3+ of {term near-inv, VRP>+3%, IV rank>0.7, SPY>+10% over 200dma, 21d >+5%} fire, the next 45 days have elevated drawdown probability.
- **Backtest evidence**: `project_regime_signals_symmetry.md`. 24.3% crash rate vs 15.5% baseline (1.58× lift). Fires on 5.1% of days. Caught 3 of 5 historical downturns; missed Dec 2018 and Aug 2015 entirely.
- **Forward-test prediction (May 15)**: SPY currently at +5.4% above 200dma with IVR 0.328. Composite shouldn't fire at entry. **Negative-prediction**: no drawdown ≥5% expected by May 15.
- **Honest expected limitation**: even if the composite fires later, it has known false-negative rate (40% of historical downturns came with no warning). May 15 outcome alone cannot validate or falsify the composite.

## Signal 7 — Below_200dma (trend filter, reactive)

- **Claim**: SPY below 200dma is a trend-flip signal but it's REACTIVE — it fires after damage is done.
- **Backtest evidence**: 33.2% crash rate vs 15.5% baseline (2.15× lift). But "early warning" is misleading — by the time SPY is below 200dma, drawdown is already underway.
- **Forward-test prediction (May 15)**: SPY currently +5.4% above 200dma. Signal won't fire at entry. If it fires DURING the cycle, that's mid-cycle regime alert. Trades placed before signal fires have no protection.
- **Falsification**: not directly testable in one cycle.

## Signal 8 — VIX level (newly captured 2026-04-26)

- **Claim**: VIX > 20 supports soft_downsize_active when paired with term inversion. Higher VIX = more expected movement.
- **Backtest evidence**: VIX-specific lift is not yet directly tested (project memories use spy_atm_iv30 as proxy). Backtest research treats VIX > 25 as "elevated" historically.
- **Forward-test prediction (May 15)**: VIX currently 18.71 (below 20 trigger). Soft-downsize NOT active via VIX path. If VIX rises above 20 with term inverted, soft_downsize fires. **Prediction**: cohort outcomes consistent with no soft-downsize regime activation.
- **Falsification (this cycle)**: cycle outcomes can't directly validate VIX as the lift mechanism — would need cross-section of VIX-high vs VIX-low entry days. Defer until N grows.

## Signal 9 — Stage 0-3 composite

- **Claim**: stage is the composite directional state. Stage 0 = calm/bull; 1 = soft-downsize triggered; 2 = SPY<200dma without IVR confirmation; 3 = H1 active (bear regime).
- **Backtest evidence**: derived from H1 + below_200dma + soft_downsize logic. No standalone backtest of stage as a unit.
- **Forward-test prediction (May 15)**: stage is currently 0 (calm/bull). Trades placed in stage 0 should reflect bull-regime structure outcomes. If stage transitions during the cycle (e.g., 0→1 or 0→2), trades placed BEFORE the transition were in a different regime than at close — that's the discipline question for sizing/management.
- **Falsification**: stage in calm/bull regime should produce overall cycle that's NOT a major drawdown. ≥5% SPY drawdown by May 15 with stage staying at 0 the whole time would falsify the stage logic.

---

## Per-trade signal-state attribution at entry (point-in-time)

For each May 15 placed trade, the regime state AT ENTRY is the binding pre-registration. We can't backfill this cleanly because regime_state tracking only began 2026-04-25 (after most entries). For trades entered before that date, we treat the April 21-25 ORATS-derived signals as the entry state (same regime week).

State as of 2026-04-21 (best-available proxy for the entry week):
- SPY $703.36, +5.4% vs 200dma — bullish trend
- IVR 0.328 — low
- Term spread +0.0066 — INVERTED
- VRP −0.0221 — slightly negative
- bull_put_signal_active: OFF (need contango + VRP>0; contango fails)
- h1_active: OFF (need below_200dma; fails)
- if_gate_active: ON (term inverted)
- soft_downsize_active: OFF
- stage: 0 (calm/bull)

**Implication**: bull_put placed during this period were placed with NO bull_put gate fire. They are essentially "ungated" bull_put trades. Bear_calls placed during this period were placed with NO h1 gate fire (gate was OFF). The 30 placed trades are forward-test of the **un-gated** baselines.

If un-gated trades win, that's noise at small N — bad signal interpretation. If un-gated trades lose, that's CONFIRMATION the gates matter (consistent with backtest "you should have stayed out").

---

## Measurement protocol (executed at May 15 close)

1. Run `cycle_postmortem_qualifier.py --opex 2026-05-15`. Capture per-trade outcome + signal state at entry.
2. For each signal claim above, compute: predicted vs actual. Record as VALIDATED / NULL / FALSIFIED in a follow-up memory.
3. Update signal weights / thresholds in `gate_config.py` for any signals that forward-test cleanly.
4. Add post-mortem cross-tab: signal-firing-state at entry × win/loss outcome × dollar P/L. (See `project_signal_validation_v2_plan.md` for the heavier build.)

## Honest caveats

- **N is small.** 30 placed trades single-cycle = PRELIMINARY per the project's adequacy tiers. Forward-test results from May 15 are directional-only, not validating.
- **Single regime.** All 30 trades placed during stage 0 / no-gate-firing. Cannot test signal lift on cohort that wasn't gated this cycle.
- **Confounded by trade quality.** Some placed trades violate the 2× loss-cap rule (per `feedback_loss_cap_discipline.md`). Their outcomes will reflect both signal and structural-fit issues.
- **No null-control.** We don't have a parallel cohort of trades placed under different signal regimes to compare against. The cycle is the test.

The June OpEx cycle will be the first cycle where signal-aware trade selection has a chance to forward-test discipline against this baseline.
