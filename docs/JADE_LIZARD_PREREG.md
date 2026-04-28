# Jade Lizard backtest — pre-registration (2026-04-25)

Set BEFORE any code runs. This document seals the hypothesis, universe,
methodology, and falsification criteria for the Jade Lizard study so
selection bias cannot creep in afterward.

## Strategy spec

A Jade Lizard is a 3-leg defined-call-side / undefined-put-side credit
structure on a single underlying:

- Short OTM put (30Δ short delta target)
- Short OTM call (30Δ short delta target)
- Long OTM call (15Δ long delta target — further OTM than the short call)

The defining rule: **total credit collected at entry > call-wing width**.
Width = (long-call-strike − short-call-strike). When this rule is satisfied,
upside risk is structurally zero — at any underlying price above the long
call, the position pays out (credit − width), which the rule guarantees is
strictly positive. Downside risk is still significant: the short put
behaves like a naked put below its strike.

## Hypothesis

Jade Lizard mean P&L exceeds the natural baseline (`bull_put_30Δ` +
`bear_call_30Δ` on the same underlying + same entry day + same expiration)
by **+$0.05 to +$0.20 per cycle**, on the universe filtered to skew-rich
names. The mechanism is twofold: (1) the put side captures the fear-pricing
premium in put skew on these names; (2) the structurally-zero upside risk
removes the BP+BC combo's exposure to the bull_put leg whenever a rally
takes spot above the bear_call short strike.

## Universe

Pre-filter the 150-symbol Track A universe to names where the mean of
(30Δ-equivalent put IV − 30Δ-equivalent call IV) over the available ORATS
history is positive. Names with positive mean skew across the sample are
the natural fit; names with flat or inverted skew (call IV > put IV — rare,
typically commodity producers in supply-shock regimes) are excluded.

Expected universe size: ~80-110 of the 150. Final size locked at universe
extraction time, not adjusted post-hoc.

ETFs are included if their IV pairs price (most do). VIX, VXX, UVXY are
excluded — their skew structure is degenerate.

## Test matrix

- Wing width on the call side: defined by 30Δ short / X long, where X is
  the long-call strike that minimizes wing while satisfying the credit-rule
  (credit > wing). 15Δ is the default heuristic, but the binding constraint
  is the credit-rule, not a fixed delta target. Smoke test 2026-04-25 on a
  hard 30/30/15 specification produced ~0-10% credit-rule fire rate across
  10 skew-rich names — not tradeable as a hard spec. The practitioner
  variant ("find the long call that makes the rule satisfy") is what
  TastyTrade material actually describes; that is what we test.
- Entry: 45-DTE before monthly OpEx (Window A spec, matches the rest of
  the v1.5 plan).
- Exit:
  - Held to expiration (primary)
  - Managed first-trigger (50% of max profit OR DTE ≤ 21) (secondary;
    matches Window A managed-exit spec)
- Slip: 0.25 AND 0.50.
- Per-cycle credit-rule filter: skip the cycle if (total credit ≤ call
  wing width). Track the firing rate of this filter — too restrictive (<30%
  of cycles qualify) is itself a falsification condition.

## Comparison baselines

For each Jade Lizard cycle that passes the credit-rule filter:

1. **BP+BC combo on same (ticker, expiration, entry-day):** open a
   bull_put_30Δ AND bear_call_30Δ on the same chain, exit per the same
   rule, compute combined P&L. This is the structurally-equivalent
   defined-risk alternative the user is already comfortable with.
2. **Per-ticker bull_put_30Δ alone:** same name + entry, no call side.
   Reveals whether the call wing in Jade Lizard adds or subtracts edge.

The lift = jade_lizard mean − BP+BC combo mean, computed cohort-wide and
per-ticker.

## Output target

Per-cell scorecard at `data/profile/jade_lizard_scorecard.parquet`:

- rows = (ticker, exit_rule, slip)
- cols = N, mean_pnl, win_rate, worst, best, total_pnl,
  credit_rule_fire_rate, baseline_BP_BC_mean, lift_vs_baseline,
  baseline_BP_alone_mean, lift_vs_BP_alone
- Plus a cohort-wide row per (exit_rule, slip).

## Falsification criteria

Any one of these → strategy stays parked, NOT promoted into
TRADING_PLAN.rtf:

1. Cohort-level lift ≤ 0 vs BP+BC baseline at slip=0.50, either exit rule.
2. Credit-rule filter fires on <30% of cycles in the skew-rich universe
   (rule is too restrictive to support a tradeable cadence).
3. Per-ticker analysis shows fewer than 5 names with both positive
   absolute mean AND positive lift vs BP+BC at slip=0.50.
4. Walk-forward (train 2013-2022, validate 2023-2026) on top-5 names
   shows ≥3 names flipping from positive train to negative validation.

## Promotion criteria

ALL of these must hold for the strategy to be promoted into the plan:

1. Cohort-level mean lift ≥ +$0.05/cycle vs BP+BC at slip=0.50 (matches
   the lower bound of the predicted range).
2. Credit-rule fire rate ≥ 30% on the skew-rich universe.
3. At least 5 names with positive absolute mean AND positive lift at
   slip=0.50.
4. Walk-forward validates on each of those names individually.
5. Statistical significance p < 0.05 (Welch's t-test, jade vs baseline)
   at the cohort level.

If only 3-4 of the above hold, the result is "informative but not
deployable" and is captured in a memory file as such — no plan update.

## Methodology discipline

- Pre-registered (this doc, sealed before code).
- All defined-side risk capped by the long call. Put side is undefined
  but bounded by user's general crash-protection logic (which is policy-
  level, not structure-level).
- Slip 0.25 AND slip 0.50 both reported.
- Output written even if hypothesis is falsified — null results are
  valuable and prevent re-investigation later.
- Findings memory written either way; nothing in TRADING_PLAN.rtf unless
  promotion criteria all hold.
