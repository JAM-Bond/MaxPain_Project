# Breadth Ring × ZEBRA — Exploratory Finding

**Status: EXPLORATORY FINDING 2026-06-11. §5 ROBUSTNESS BATTERY RUN 2026-06-11 — ALL 4 TESTS PASS → a zebra-specific sizing-gate pre-reg is warranted (see §8). Still historical/in-sample; not a sealed gate yet.**

**Purpose:** Document the zebra signal that surfaced from the bull_put sizing-gate corroboration ([[BREADTH_RING_SIZING_PREREG]] §14), the correction of an artifact in that run, the honest walk-forward picture, and the bar a confirmatory study must clear before any zebra sizing gate is pre-registered.

---

## 1. Origin and the artifact correction

The bull_put sizing-gate corroboration (sealed pre-reg D) failed for bull_put but its *secondary* zebra check looked dramatic: 🔴-entry zebras −0.82 vs +10.67 non-🔴 (Δ −11.5/cycle). **That headline was partly an artifact:** the corroboration script mixed two P&L definitions — tier-1 used *combined* (parent + long-put overlay) hold P&L, tier-2 used *parent-only* `pnl_zebra`. The +10.67 non-🔴 mean was inflated by the tier-1 overlay gains being averaged against tier-2 parent-only numbers. Correcting to a single consistent metric changes the magnitude materially.

## 2. The honest walk-forward picture (consistent metric)

Using a **consistent combined-hold P&L** (parent + OTM-10 long-put overlay — the structure actually traded — across tier-1 + tier-2, N=2,405), tagged by entry-day ring state:

| Split | 🔴 n | 🔴 mean | non-🔴 mean | Δmean | 🔴 worst-decile | non-🔴 worst-decile |
|---|---:|---:|---:|---:|---:|---:|
| Full | 376 | −0.13 | +17.28 | **−17.4** | −28.4 | −22.2 |
| Train ≤2019 | 208 | +2.60 | +8.46 | −5.86 | −12.9 | −9.8 |
| Test ≥2020 | 168 | −3.50 | +25.27 | −28.77 | −47.0 | −33.9 |

**Supportive:** 🔴-entry zebras earn ~0 vs ~+17/cycle, the Δ is the **same sign (🔴 worse) in both walk-forward splits**, and 🔴 carries the **fatter left tail** in both. Economically sensible — a zebra is synthetic long stock (real directional exposure), exactly what a narrow-megacap-top regime should punish, unlike a defined-risk credit spread.

## 3. The fragility (why this is NOT yet gate-able)

1. **Definition-sensitive.** On *parent-only* P&L the train split FLIPS to 🔴 +0.70 (better). The sign-stability holds only for the with-overlay (combined) structure. A signal that depends on the P&L definition is not robust.
2. **Magnitude unstable.** Δ −5.86 (train) vs −28.77 (test) — a ~5× swing; the effect is concentrated in the post-2020 drawdowns and a few tiny-sample years (2021 n=14 at −31/cycle; 2023 n=9 at −52/cycle).
3. **Dollar value is tail-reduction, not return.** 🔴 zebras are ~break-even in aggregate — skipping them changes total cohort P&L by only +0.14% (35,018 → 35,066). The *only* coherent gate rationale is cutting worst-decile / drawdown exposure for ~zero give-up in expected return.
4. **Small, rare population.** 🔴 is ~16% of days; 376 🔴 zebra cycles over 13 years, ~9–35 per year — thin for per-regime inference.

## 4. Economic logic (why it's plausible despite the fragility)

The breadth ring's validated content is downside/drawdown risk (memory `project_rsp_spy_breadth_signal`). A bull put is defined-risk and managed, so it absorbs that risk (which is why the bull_put gate failed). A zebra is *long synthetic stock* held to expiry with a put overlay — it carries genuine directional exposure and a real left tail. So if the breadth signal expresses anywhere in the book, the long-delta debit structure is the plausible home. The data is consistent with that; it is just not yet clean enough to seal a rule.

## 5. Bar to clear before a zebra sizing-gate pre-reg

A confirmatory study (not yet run) must show ALL of:
- **Sign stability** of 🔴-underperformance in BOTH walk-forward splits **on the consistent combined-hold metric** (met in §2).
- **Overlay-variant robustness:** the 🔴 tail penalty holds across overlay choices (ATM / OTM-5 / OTM-10), not just OTM-10 — i.e. it is not an artifact of one overlay strike.
- **Not driven by ≤2 years:** drop-any-single-year (and drop-2021, drop-2023) leave-one-out stability — the effect must survive removing its biggest contributors.
- **Tail-reduction counterfactual:** half-size (and skip) on 🔴 entries materially reduces worst-decile / max-drawdown contribution while total cohort P&L change stays within a pre-set tolerance (since 🔴 mean ≈ 0, the cost should be ~nil).
- **Adequacy:** report per-split 🔴 N and flag thin cells; no promotion on a single-regime artifact.

If all pass → draft and seal a zebra-specific sizing pre-reg (🔴 → half-size new zebra entries, advisory only, same anti-censoring tag-don't-downsize validation design as pre-reg D). If any fail → the zebra signal stays a descriptive observation (the step-B card annotation already covers zebra cards) and no gate is built.

## 5a. Robustness result (RUN 2026-06-11) — ALL PASS

Script: `scripts/backtest/breadth_ring_zebra_robustness.py`. Combined parent+overlay hold P&L, tier-1+tier-2 (N=2,405), tagged by entry-day ring state.

- **Test 2 — overlay-variant: PASS.** 🔴 underperforms in ALL three overlays and BOTH walk-forward splits: ATM Δ −13.6, OTM-5 −15.5, OTM-10 −17.4 (full); every train/test cell negative. The earlier train-split sign-flip was a *parent-only / no-overlay* artifact — the traded (with-overlay) structure is consistent.
- **Test 3 — drop-year: PASS.** Baseline Δ −17.4; leave-one-year-out range [−21.8, −15.0]; drop 2021 → −15.2, drop 2023 → −15.0, **drop BOTH → −12.5**. Not a 2-year artifact.
- **Test 4 — tail counterfactual: PASS.** Half-🔴 cuts cohort CVaR-10 by **+10.7%** (−82.8 → −73.9) for a **+0.07%** change in total P&L. Skip-🔴 raises total +0.14% and also cuts the tail. The value is tail reduction at ~zero return cost (🔴 zebras ≈ break-even).
- **Test 5 — adequacy: PASS.** 🔴 N: train 208 / test 168 (pooled adequate). Per-*year* 🔴 N is thin (13–58) — per-year inference unreliable; the signal is pooled across regime occurrences, not annual.

**Caveats retained:** historical/in-sample (walk-forward split is the only OOS element); penalty far larger in the drawdown-heavy test period (−28.8) than calm train (−5.9), so the gate's value concentrates in stress and is ~nil in calm markets; cohort = the backtest cohort.

## 6. Recommendation

The §5 bar is **met** → per the finding's own rule, **draft and seal a zebra-specific sizing pre-reg** (🔴 → half-size new zebra entries; advisory only; the anti-censoring tag-don't-downsize validation design from pre-reg D; frame the gate value as tail/CVaR reduction, since 🔴 zebras are ~break-even on mean). Until that pre-reg is sealed and (where possible) paper-corroborated, the ring stays **descriptive** — step B already annotates zebra cards on 🟡/🔴 days. Note the counterfactual marginally favors *skip* over *half* on total P&L; the pre-reg should test both half and skip.

## 7. Artifacts & cross-references

- Corroboration run + the mixed-metric artifact: `scripts/backtest/breadth_ring_sizing_corroboration.py`; [[BREADTH_RING_SIZING_PREREG]] §14.
- Consistent-metric walk-forward numbers (§2): ad-hoc analysis over `zebra_put_overlay_phase2_results.parquet` (`pnl_v3_combined_hold`) + `zebra_put_overlay_tier2_results.parquet` (`pnl_v3_otm10_combined`), tagged by `breadth_ring_daily`.
- Signal basis: memory `project_rsp_spy_breadth_signal`. Discipline: descriptive-before-gating ([[project_go_live_plan]]); advisory-only ([[feedback_never_execute_trades]]).

**Drafted by:** Claude Opus 4.8 (1M context) · **Drafted on:** 2026-06-11
