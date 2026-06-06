# Pre-Registration — FedWatch Repricing × Vertical Side

_Sealed 2026-06-04 · Phase 1 of the FedWatch integration (project_fedwatch_integration.md).
Thresholds below are committed BEFORE looking at outcomes; do not move them after._

## ⚠️ Read first — this is a "ripens with time" study, not a one-shot

The truly novel signal here (ANTICIPATORY FedWatch repricing) can only be tested on the
manually-uploaded `cme_fedwatch_history`, which starts **2026-02-23** — so today the native
test (Track B) is **N-limited and expected to be underpowered**. That is by design, and it is
explicitly accounted for:

> **A null under insufficient N is INCONCLUSIVE, NOT REJECTED.** It does not retire the
> hypothesis or send it to the signal graveyard. The frontrunning thesis is structurally
> sound (big books reposition ahead of an increasingly-likely Fed move); the only missing
> ingredient is sample, and the FedWatch series grows ~2×/week. **Re-run this study as N
> accrues until the power gate (Gate P) is met — only THEN does a null become terminal.**
> This study could become a real edge once the data exists; treat an early null accordingly.

This distinguishes it from the ~12 falsified mechanics in the graveyard, which were
*powered* nulls.

## Hypotheses

- **H1 (alpha — primary but skeptical).** Within an active FedWatch repricing episode, credit
  verticals on the side ALIGNED with the repricing direction (via each name's `regime_primary`
  PC1 loading) have higher per-cycle expectancy than ADVERSE-side verticals, AND beat the
  no-episode baseline.
- **H2 (positioning-risk — likelier to survive, fits the crash-protection profile).**
  ADVERSE-side verticals during a repricing episode carry a materially worse loss-rate / tail
  than aligned ones — i.e. the value is in AVOIDING the wrong side, not in picking winners.
  H2 can pass even if H1 fails.

Prior context: the H-A/H-B regime-conditioning study (project_macro_sensitivity_profile_research)
already rejected macro conditioning of vertical selection — but using TRAILING realized regime.
This is ANTICIPATORY (forward implied probabilities + frontrunning), a hypothesis the prior null
did not cover. Not a re-run; also not auto-valid.

## Signal definitions (sealed)

- **Repricing velocity** for a meeting = Δ(hike% − cut%) over the trailing **14 calendar days**,
  from `lib/macro_brief.get_fedwatch_trajectory` (`cme_fedwatch_history`). Positive = repricing
  toward HIKES (reflation); negative = toward CUTS.
- **Episode** = the nearest 1–2 future meetings show |Δ(hike − cut)| **≥ 5.0 pp / 14d**.
  Below that, the rate regime is "quiet" → no signal, excluded from the aligned/adverse test
  (reported as the baseline cohort).
- **Episode direction** = sign of the velocity on the nearest qualifying meeting.
- **Name macro side** via `regime_primary`: **PC1+** = reflation/rates-up beneficiary;
  **PC1−** = reflation/rates-up loser. NEUTRAL / PC2± / PC3± are NOT classified on the rate
  axis for this test → excluded from alignment, reported separately (they are not the bet).
- **Aligned / adverse mapping (sealed):**

  | Episode | bull_put (wants tailwind) favors | bear_call (wants headwind) favors |
  |---|---|---|
  | HIKE-repricing | PC1+ | PC1− |
  | CUT-repricing  | PC1− | PC1+ |

  "Aligned" = the favored side; "Adverse" = the opposite PC1 sign.

- **Outcome** = the existing per-cycle vertical backtest P/L (mgd50 exit, slip = 0.25),
  the same substrate H-A/H-B used — now split by episode state + alignment at entry.

## Two tracks

- **Track A — proxy, POWERED (13yr).** Replace FedWatch velocity with a forward-rate ANTICIPATION
  PROXY = trailing 14–21d change in the front-end yield (DGS2 / 3m) from the macro panel. Run the
  full 4-split walk-forward on the existing bull_put/bear_call per-cycle backtest.
  Caveat: this proxy is close to the TRAILING conditioning already nulled, so a Track-A null is
  weakly informative about the anticipatory thesis — it is a lower bound + a plumbing/sanity check,
  NOT a verdict on H1/H2.
- **Track B — native FedWatch, N-LIMITED (Feb-2026→).** The real anticipatory test on
  `cme_fedwatch_history`. Run on whatever cycles have closed with a classifiable episode at entry.
  This is the track the non-terminal-null provision protects.

## Sealed gates

- **Gate A (effect size, H1):** aligned − adverse mean expectancy **≥ +$0.05 / contract / cycle**.
- **Gate B (consistency, H1):** aligned > adverse in **≥ 3 of 4** walk-forward windows (Track A),
  or **≥ 2 of 3** episodes (Track B once it has ≥3 episodes).
- **Gate C (tail, H2):** adverse loss-rate ≥ aligned loss-rate **+ 10 pp**, OR adverse
  mean-loss-among-losers ≥ **1.3×** aligned. (H2 passes on Gate C alone.)
- **Gate P (POWER — the terminal-vs-inconclusive switch):** a result is POWERED only with
  **≥ 6 distinct repricing episodes** AND **≥ 40 classifiable candidate-trades**. Below this
  threshold the verdict is INCONCLUSIVE regardless of point estimate.

## Verdict logic

1. **PASS** — Gate P met AND (Gate A + Gate B) for H1, or Gate C for H2 → promote to **Phase 2**
   (soft sector/side tilt in the qualifier, downstream of the sector + macro caps, respecting the
   cap division-of-labor).
2. **POWERED NULL** — Gate P met, gates fail → terminal; graveyard with the other powered nulls.
3. **UNDERPOWERED** — Gate P NOT met → **INCONCLUSIVE. Non-terminal. Re-run later.**

## Re-run trigger (for the non-terminal case)

While the verdict is UNDERPOWERED, re-run Track B whenever **either** condition holds, and log
the running episode/trade counts each time so power accrual is visible:
- **≥ 3 new repricing episodes** have accumulated since the last run, OR
- **one calendar quarter** has elapsed.

Stop re-running only when Gate P is met (verdict becomes terminal PASS or NULL) or the user
retires it. Carry it on the live-prep / revisit watchlist so it isn't forgotten.

## What Phase 2 would look like (only if PASS)

A soft, qualifier-stage SIDE/sector tilt: during a classified repricing episode, prefer aligned
candidates and (the H2 use) flag/avoid adverse-side verticals whose short strike sits on the wrong
side of the frontrun-in-progress move. Soft, audited, downstream of the existing caps — never a
hard gate, consistent with "macro is a risk descriptor, not a hard selection edge."

## First run

Expected INCONCLUSIVE today (Track B has ~1–2 classifiable episodes since Feb-2026). Track A can be
run now as the powered lower-bound / plumbing check. Do not read an early Track-B null as a verdict.
