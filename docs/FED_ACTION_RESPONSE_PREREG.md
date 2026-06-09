# Fed-Action Response Study — Pre-Registration

_Status: SEALED 2026-06-09, BEFORE any code or queries run. Author handoff: add a
per-name/ETF/index "how did this behave around Fed holds/cuts/hikes — and around
*surprises*" datapoint to the recommendation context._

## Why this is pre-registered (and what it is NOT)

Small N + heavy regime clustering make this a textbook over-fitting trap, so the
decision rules below are committed before looking at results.

**This is a DESCRIPTIVE context annotation, not a selection/timing gate.** Prior
work already settled the edge question: the macro-regime-conditioning backtest was
REJECTED (`project_macro_regime_conditioning` / H-A/H-B), and the standing
conclusion is *macro/Fed conditioning is a risk/context descriptor, not an alpha
signal* (`project_macro_sensitivity_profile_research`). The per-name FOMC event
study (macro Phase 4, `data/profile/macro_fomc_event_study.parquet`) already
computed hike-vs-cut response per name. This study **operationalizes that as a
recommendation-card annotation and adds the FedWatch *surprise* dimension** — it
does not relitigate whether Fed conditioning picks winners. Nothing here may be
wired as a verdict gate.

## The question

For each scope (SPY, QQQ, sector ETFs, cohort single names): historically, what was
the forward return after the Fed **held / cut / hiked** — and, the genuinely new
cut, after an action that was **expected vs. a surprise** relative to what FedWatch
had priced the prior day?

## Data realities (verified 2026-06-09, drive the design)

- `bond_agent.db:fomc_decisions` records only rate **changes**: **20 hikes, 11 cuts,
  0 holds** (2015-12 → 2025-12). Holds must be derived from the FOMC meeting
  calendar (≈8/yr).
- **Regime clustering:** 19/20 hikes ∈ {2017-18, 2022-23}; cuts ∈ {2019, 2020-COVID,
  2024-25}. So a raw "around hikes" bucket ≈ "in 2017-18 + 2022-23." This confound
  is the central threat and must be disclosed in every output cell.
- **FedWatch historical surface** confirmed back to ≥2015 via
  `/forecasts?meetingDt=YYYY-MM-DD&reportingDt=YYYY-MM-DD` (the prior-day implied
  probability distribution per meeting). This is what enables the surprise cut.

## Data sources (exact, frozen)

1. **Actions.** `fomc_decisions` (hike/cut + change_bps + new_rate). **Holds** =
   scheduled FOMC meetings absent from `fomc_decisions`, using a maintained FOMC
   meeting-date calendar `config/fomc_calendar.csv` (2015→present, 8/yr); every
   `fomc_decisions.meeting_date` MUST appear in the calendar (assert at build).
   Inter-meeting/emergency actions (e.g., 2020-03) are flagged `emergency=1` and
   reported separately, never pooled into scheduled-meeting buckets.
2. **FedWatch implied prob (prior business day).** For each meeting, pull
   `/forecasts?meetingDt=<meeting>&reportingDt=<last business day before meeting>`
   and reduce to implied P(cut)/P(hold)/P(hike) via the existing
   `api_ingester.classify_action`. `P_implied(action)` = implied probability of the
   action that was actually realized.
3. **Prices.** ORATS split-adjusted close via `lib.adjusted_close.load_adjusted_close`
   for: SPY, QQQ, the sector ETF set (XLE/XLF/XLK/XLP/XLU/XLV/… as in `sector_map`),
   and the live cohort single names (gate_config COHORT_* union).

## Method (frozen)

- **Event date** = FOMC `meeting_date`. **Forward windows: 1d / 5d / 25d** (trading
  days, close-to-close), matching `project_spy_after_fed_change_findings`.
- **Return.** SPY & QQQ: raw forward return. Sector ETFs and single names:
  **market-adjusted** (`name_return − SPY_return`) so we measure the *differential*
  response, not beta to the tape.
- **Action classification.** hike / cut / hold (+ `emergency` flag). Cut/hike from
  `fomc_decisions`; hold from calendar-minus-changes.
- **Surprise classification (sealed thresholds).** Using `P_implied(realized action)`
  as of the prior business day:
  - **EXPECTED**  if `P_implied ≥ 0.65`
  - **SURPRISE**  if `P_implied ≤ 0.35`
  - **AMBIGUOUS** (0.35 < P < 0.65) — reported in the action bucket but EXCLUDED
    from the surprise contrast.
  Thresholds are frozen here; do not tune them to results.
- **Adequacy** per cell (existing convention): PRELIMINARY <10 / SUGGESTIVE <20 /
  DEVELOPING <30 / ADEQUATE ≥30. Surprise-split halves N, so expect mostly
  PRELIMINARY/SUGGESTIVE — that is itself a pre-committed finding, not a reason to
  loosen thresholds.
- **Regime-cluster disclosure (mandatory).** Every cell carries the year-distribution
  of its events (e.g., "hike N=20: 2017-18=8, 2022-23=11, other=1").

## Hypotheses

- **H1 — Characterization (not pass/fail).** Produce per-(scope, action, window)
  forward-return distributions (mean/median/win-rate/N/adequacy/year-mix). Always
  reported; never asserted as an edge.
- **H2 — Surprise amplifies move size (the real, falsifiable test).** At the broad
  level (SPY, QQQ), |forward return| is materially larger after SURPRISE actions
  than EXPECTED ones. **Pre-committed gate: surprise 5d mean |return| ≥ 1.5×
  expected**, same sign-direction logic, on N_surprise ≥ 5 per side. If not met →
  surprise dimension reported as "no measured amplification," not surfaced as a
  signal.
- **H3 — De-confound check (pre-committed null interpretation).** For the hike
  bucket, compare the event-window return to a **same-year non-event baseline**
  (random non-meeting windows from the same calendar years). If the event-window
  mean is within ~0.5σ of the same-year baseline, the cell is labeled
  **"regime-driven, not Fed-action-driven"** in the output (and not pitched as a
  Fed effect).

## What gets surfaced (operationalization)

- A **context annotation** on the recommendation card (sibling to the macro /
  sector-drift annotations), e.g.: *"Fed context: into hikes, NAME +1.8%/25d
  (N=12, SUGGESTIVE, 2022-23-concentrated); surprise hikes +3.1% (N=4,
  PRELIMINARY)."*
- **Forward framing:** map today's FedWatch tilt for the next meeting (current
  implied P(hold/cut/hike)) to the matching historical action bucket per recommended
  name. Pure context — it does not change the verdict, size, or gating.
- **Surfacing floor:** only cells with **N ≥ 8** are shown, always with the
  regime-cluster note. Cells below the floor are stored but not displayed.
- **Hard constraint:** NEVER wired into `cycle_qualifier` verdict logic or any cap.
  Annotation only.

## Scope ladder (build order)

1. **SPY / QQQ** — validate the pipeline against `project_spy_after_fed_change_findings`
   (the SPY numbers must reconcile) before trusting anything downstream.
2. **Sector ETFs** — cleanest interpretation after the indices.
3. **Cohort single names** — only surface where N clears the floor; expect many to
   fail it. Newer tickers lack full-cycle history → buckets biased to recent cycles;
   report N, never extrapolate.

## Artifacts

- `config/fomc_calendar.csv` (frozen meeting dates) + assertion vs `fomc_decisions`.
- `scripts/research/fed_action_response.py` — the study (reads the three sources,
  writes the parquet).
- `data/profile/fed_action_response.parquet` — one row per (scope, action,
  surprise_bucket, window) with mean/median/win/N/adequacy/year-mix.
- Findings memory written AFTER the run; H2/H3 verdicts recorded verbatim.

## AMENDMENT 2026-06-09 (post-first-run, documented before any H2 verdict was read)

The first run revealed the prior-business-day surprise definition is **degenerate**:
EVERY action classified "expected" (H2 untestable, surprise N=0). Measured at T-1,
FedWatch is ~always right (the Fed telegraphs), so there are no day-before surprises
— the repricing happens weeks earlier. This is a structural measurement failure
(zero surprises), discovered BEFORE any H2 effect was observed, so correcting it is
not result-tuning.

**Amended:** surprise implied-probability is now measured at a **~42-calendar-day
lead** (`--lead-days`, default 42 ≈ the 45-DTE entry horizon) instead of T-1. The
0.65/0.35 thresholds, the 1.5× H2 gate, windows, and de-confound logic are
UNCHANGED. The descriptive (action-conditioned) results from the first run stand and
reconcile with `project_spy_after_fed_change_findings` (cuts fade 5d / recover 25d;
hold = bullish 25d drift). The lead-based surprise split + H2 are to be (re-)run next
session. Rationale logged so the amendment is auditable.

- Do not tune the 0.65/0.35 surprise thresholds, the 1.5× H2 gate, or the windows
  to results.
- Do not pool emergency/inter-meeting actions with scheduled meetings.
- Do not surface a cell without its N + year-mix caveat.
- Do not wire any output into a gate, cap, or verdict — annotation only.
- Do not claim a Fed-action effect for any cell flagged regime-driven by H3.
