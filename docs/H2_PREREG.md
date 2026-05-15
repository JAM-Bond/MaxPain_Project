# H2 Weakness Gate — Phase 1 Pre-Registration

**Sealed:** 2026-05-15 (before walk-forward + live-failure validation)
**Sources:** `project_h2_weakness_research.md`, `feedback_signal_as_filter_pattern.md`
**Purpose:** Validate whether the W3 multi-filter definition of "weak" should be
promoted as a bull_put **exclusion filter** (NOT a short signal) in the cycle
qualifier.

---

## Conceptual basis (sealed)

H2 originated as a research question into whether CANSLIM-style weak names
underperform when SPX is bull-extended. The pooled-mean answer was NO —
Daniel & Moskowitz "Momentum Crashes" dominates the mean (weak names crash
hard but also rip back violently in recoveries). The asymmetric distribution
DOES matter, though: weak names crash >20% in 75 days roughly 3× as often as
strong names.

For a defined-risk credit-spread strategy (bull_put: short put + long put,
same expiration, same number of contracts), elevated assignment risk + max-loss
exposure on weak names is the operational concern, not the mean expectancy.
WFC's 5/12 stop (-$180 at 2 contracts) is the live failure mode.

**H2 is therefore reframed as a defensive exclusion gate, not a short signal.**
The conceptual claim: names matching W3 have elevated tail-risk relative to
SPX, sufficient to justify excluding them from the bull_put cohort even when
they otherwise pass per-name gates.

## W3 operational definition (sealed)

A symbol matches W3 on a given date if ALL of the following are true:

1. **RS bottom 10%**: trailing 252-day total return rank ≤ 0.10 within the
   327-name ORATS universe cross-section.
2. **Below own 200-DMA**: latest close < 200-day moving average of own price.
3. **Far from 52-week high**: latest close / 252-day rolling max ≤ 0.70 (i.e.,
   ≥30% off 52w high).

All three are computed from daily close-only data (no fundamental signals).

## Decision rule (sealed)

The W3 filter promotes to live deployment as a bull_put exclusion gate IF AND
ONLY IF all three of the following pass:

**Gate A — Primary crash-rate asymmetry (pooled):**
W3-name forward-75d crash rate (return < -20%) ≥ 2× SPY baseline crash rate
on the same bull-extended dates.

**Gate B — Walk-forward stability:**
The crash-rate-ratio (W3-rate / SPY-rate) ≥ 1.5× in at least 3 of 4
validation windows. Validation windows match the ZEBRA Phase C convention:
- 2021-2023 (3-yr window)
- 2022-2024
- 2023-2025
- 2024-2026

**Gate C — Live-failure out-of-sample validation:**
At least 2 of the following 3 names match W3 on their actual entry dates:
- WFC at 2026-05-05 (the live -$180 stop on 5/12)
- XLU at 2026-05-06 (the live bull_put that, while not stopped, was in the
  same regime-fragility cohort)
- KRE at 2026-05-05 (the live ZEBRA entry that needed a separate long-put hedge)

The bar is "at least 2 of 3" rather than "all 3" because the goal is
identifying the dominant failure mode, not perfection. False negatives
(letting a future WFC-style name through) are unavoidable on a daily-close
signal; the cap on false negatives is a separate concern.

## What promotion looks like

If all three gates pass:

1. `lib/h2_weakness.py` — new module with `is_weak_w3(symbol, asof_date)`
   reading per-name daily close history from ORATS by-ticker parquets.
2. `scripts/qualifier/gate_config.py` — add `H2_EXCLUSION_VERDICT` reason
   string + tunables (`H2_RS_DECILE_THRESHOLD`, `H2_MIN_DIST_BELOW_52W_HIGH`).
3. `scripts/qualifier/cycle_qualifier.py` — for bull_put / bull_put_earnings
   verdicts only, evaluate W3 at run_date. If matches, downgrade verdict to
   SKIP with reason "h2 weakness gate". Other structures (bear_call, IF,
   ZEBRA) UNAFFECTED — they have their own gates.
4. `scripts/monitor/daily_alert.py` — surface H2 status in any bull_put
   candidate card. SKIP-by-H2 rows already filtered from the actionable set,
   so the annotation is for transparency on PENDING rows that may flip.

## What does NOT change

- This is a **bull_put EXCLUSION** gate only. Not used for trade generation,
  not used for sizing on other structures.
- The existing per-name 200-DMA bucket DOWNSIZE rule (Rule #3 construction
  block) stays as-is; H2 is a stricter, multi-condition filter that fires on
  a smaller set of names.
- The framework's bull/bear gating (H1 etc.) is independent of H2. H2 fires
  per name; H1 fires for the broad regime.

## What we are NOT testing

- Whether short-the-weak is profitable (already rejected — D&M momentum
  crashes).
- Whether longer/shorter RS lookback periods change the result.
- Whether W1/W2/W4 from the original research add lift beyond W3 (W4 was
  marginally more consistent on the underperformance count, but W3 had the
  highest crash-rate ratio — the conceptual basis prioritizes crash-rate).
- Whether H2 composes well with H1 — separate study.

These are out-of-scope; doing them post-hoc would convert Phase 1 from
hypothesis testing into hypothesis searching.

## Negative-result plan

If any gate fails:
1. The filter is NOT promoted.
2. A memory note records the rejection + which gate failed.
3. The conceptual case (tail-risk asymmetry → defensive filter) survives if
   Gate A passes but B or C fail — a stricter variant could be tested in a
   later phase. Gate A failure is a fundamental rejection of the W3
   definition itself.
4. The watchlist item is updated with status REJECTED + the specific failure.

No back-fitting variants until the next OpEx cycle.
