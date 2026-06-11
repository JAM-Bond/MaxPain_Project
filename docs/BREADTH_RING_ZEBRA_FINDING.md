# Breadth Ring × ZEBRA — Exploratory Finding

**Status: EXPLORATORY FINDING 2026-06-11 — NOT a sealed gate. Fragility flagged; a clean robustness study must pass before a sizing-gate pre-reg is warranted.**

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

## 6. Recommendation

Keep the ring **fully descriptive** for now (it already annotates zebra construction cards on 🟡/🔴 days via step B). Do NOT gate zebra sizing yet. The robustness study in §5 is the gating prerequisite; it is ~2–3 hours on data already on disk and can be run whenever. This finding exists so the signal — and its fragility — are not lost or overstated.

## 7. Artifacts & cross-references

- Corroboration run + the mixed-metric artifact: `scripts/backtest/breadth_ring_sizing_corroboration.py`; [[BREADTH_RING_SIZING_PREREG]] §14.
- Consistent-metric walk-forward numbers (§2): ad-hoc analysis over `zebra_put_overlay_phase2_results.parquet` (`pnl_v3_combined_hold`) + `zebra_put_overlay_tier2_results.parquet` (`pnl_v3_otm10_combined`), tagged by `breadth_ring_daily`.
- Signal basis: memory `project_rsp_spy_breadth_signal`. Discipline: descriptive-before-gating ([[project_go_live_plan]]); advisory-only ([[feedback_never_execute_trades]]).

**Drafted by:** Claude Opus 4.8 (1M context) · **Drafted on:** 2026-06-11
