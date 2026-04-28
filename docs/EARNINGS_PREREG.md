# Earnings structure-level backtest — pre-registration (2026-04-25)

Set BEFORE any code runs, per agenda discipline (project_tomorrow_trading_plan_agenda.md).
The bias scan (project_earnings_bias_scan.md) established directional priors on stock prices.
This study tests whether a defined-risk option structure aligned with that prior produces
edge over a non-earnings control on the same name + structure.

Frictions tested for every test: slip=0.25 AND slip=0.5.
Pricing: v2 scaled wings (0.50% of spot for verticals, 10% wings for inverted_fly).
Universe: from project_earnings_bias_scan.md, min N≥20 events per ticker.

## T1 — Bull put vertical INTO earnings on bias-UP names

Cohort: SCCO, CNQ, KO, NUE, KGC, GOOGL, NRG, RRC, META, WFC, CX, ITUB
(12 names with ≥60% positive earnings rate at min N=20 in the bias scan)

Structure: short 30Δ put + long put one VERTICAL_WING_PCT_SPOT_V2 (0.50% of spot) below.
Entry: T-3 close AND T-1 close (test both)
Exit: T+1 close AND T+3 close AND held-to-expiration (test all three)
Friction: slip=0.25 AND slip=0.5

Hypothesis: managed-or-held bull_put outperforms baseline non-earnings bull_put on the same
names. Predicted lift +$0.05 to +$0.20 per cycle on the bias-up cohort.

Falsification: cohort mean lift ≤ 0 vs non-earnings control AT slip=0.5 (any (entry,exit) cell).

## T2 — Bear call vertical INTO earnings on bias-DOWN names

Cohort: INTC, JBLU, NEM, GLNG, FCX, VST, CAR
(7 names with ≤40% positive earnings rate at min N=20; SKIP TLRY/SNAP/AMD/TSLA — std too wide.)

Structure / entry / exit / friction matrix: identical to T1.

Hypothesis: aligned bear_call lifts vs non-earnings control. Less confidence than T1
because down-bias names occasionally squeeze on rare beats.

Falsification: cohort mean lift ≤ 0 vs non-earnings control at slip=0.5.

## T3 — Earnings non-event control

Same names (T1 ∪ T2), same structures, same entry/exit windows but on randomly-selected
non-earnings dates (10× as many cycles as the earnings cohort to establish stable baseline).

This is the COMPARISON for T1 and T2. Without this control, "the bull_put on SCCO into
earnings made +$0.10/cycle" is meaningless — we need the non-earnings counterfactual.

Implementation: for each ticker, sample N×10 random trading days from the same date range
covered by the earnings events, EXCLUDING any day within ±5 trading days of an earnings
event. Run the same (entry-shifted, exit-shifted) structure on each.

## T4 — Inverted_fly around earnings on high-vol bias-ambiguous names

Cohort: RIG, ENPH, PLTR, SNAP, TME, TEVA, CFLT
(high std + no clear directional bias; long-vol structure compatible with IF thesis)

Structure: inverted_fly at 10% wings (canonical wide-wings cell)
Entry: T-3 close OR T-1 close
Exit: T+1 close

Hypothesis: vol-expansion structure captures the earnings move regardless of direction.
Predicted lift over baseline non-earnings IF on the same names.

Falsification: cohort mean ≤ baseline non-earnings IF on same names at slip=0.25.

## T5 — DEFERRED

Covered call — requires stock-ownership P&L modeling not currently in the engine.
Skip this session; revisit if T1/T2 promote.

## Output target

Per-cell scorecard at `data/profile/earnings_scorecard.parquet`:
rows = (ticker, structure, entry_day, exit_day, slip)
cols = N, mean_pnl, win_rate, worst, total_pnl, control_mean, lift_vs_control, lift_p_value

Promotion rule: any cell with positive mean P&L AND positive lift over control AND survives
slip=0.5 → promote to TRADING_PLAN.rtf Earnings Plays section. Otherwise leave as scoping.

## Methodology discipline

- All structures defined-risk (verticals + IF). NO short strangles, NO naked.
- Pre-registered hypothesis + falsification before each cell runs (this doc is the seal).
- Both slip levels reported.
- N per cell flagged — earnings events are ~24 per name, so per-cell sample is small.
- Cohort-level aggregation is the primary readout; per-name results are diagnostic only.
