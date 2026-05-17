# ZEBRA + Long-Put Overlay — Tier-2 Cohort Extension Pre-Registration

**Sealed:** 2026-05-17
**Author:** Joseph Morris (with Claude Code)
**Predecessors:** `project_zebra_put_overlay_phase1_findings.md`, `project_zebra_overlay_phase2_complete.md`

## Question

Does the V3 (10% OTM put) overlay rule that PASSED Phase 1 on the tier-1 cohort extend cleanly to the tier-2 cohort? Operationally: should tier-2 ZEBRAs auto-attach the V3 overlay at entry, the same way tier-1 entries do today?

## Hypothesis (sealed)

The overlay's lift on tier-1 was driven by high-vol single names (NVDA/AMZN/GOOGL) where the 75-day return distribution has enough tail probability below −10% that a 10%-OTM put has positive expected value at prevailing premium. Low-vol names within tier-1 (SPY/QQQ/MSFT/META) showed marginal/negative per-name lift.

Tier-2 (DIA, IWM, GLD, TJX, GE, WMT, AMD, PLTR, KRE, CMG, SCHW, CSCO, TTD, USB) is mixed-volatility: some moderate-vol single names (AMD, PLTR, CMG, TTD, KRE) and some lower-vol diversifiers (DIA, IWM, GLD, WMT, USB). Prior: cohort-level effect probably positive but smaller than tier-1, with the lift concentrated in the higher-vol subset.

The overlay COULD fail on tier-2 if the lower-vol diversifier subset's drag exceeds the higher-vol subset's lift. We do not yet know.

## Universe (sealed)

Cohort: 14 names from `gate_config.py:COHORT_ZEBRA_TIER2`:
- DIA, IWM, GLD, TJX, GE, WMT, AMD, PLTR, KRE, CMG, SCHW, CSCO, TTD, USB

ORATS coverage confirmed for all 14 (2026-05-17). Histories:
- 12 names: 2013-01-02 → 2026-05-14 (full 13yr)
- PLTR: 2020-10-20 → 2026-05-14 (~5.5 yr)
- TTD: 2017-04-13 → 2026-05-14 (~9 yr)

Expected per-name cycle counts: 100-160 for full-history names, ~60 for PLTR, ~110 for TTD. Cohort total: ~1,700-1,800 cycles.

## Method (sealed)

Identical to Phase 1, just swap cohort:
- Entry: 75-DTE before each monthly OpEx, same `open_zebra` logic
- Variants: V0 (BASE: bare ZEBRA), V1 (ATM put), V2 (5% OTM put), V3 (10% OTM put)
- Holding: held-to-expiry, both ZEBRA and put settle at intrinsic
- Slip: 0.25 (matches Phase C / Phase 1)
- Walk-forward: 4 splits matching Phase C convention
  - 2021-01..2023-12
  - 2022-01..2024-12
  - 2023-01..2025-12
  - 2024-01..2026-04 (most recent / partial)
- Per-cycle output: `data/profile/zebra_put_overlay_tier2_results.parquet`

Implementation: parameterize the existing `scripts/backtest/zebra_long_put_overlay_backtest.py` with a `--cohort {tier1,tier2}` argument. The tier-1 path remains the default to preserve reproducibility of Phase 1 results.

## Promotion gates (sealed — must pass all of 1, 2, 3 for cohort-level promotion)

| Gate | Threshold | Source |
|---|---|---|
| 1. Cohort mean lift (V3 over BASE) | ≥ $0/cyc (overlay does not drag) | Phase 1 baseline |
| 2. Cohort max-drawdown reduction | V3 worst ≤ BASE worst (overlay protects) | Phase 1 baseline |
| 3. Walk-forward stability | V3 lift positive in ≥ 3/4 splits | Phase C convention |

If gates 1+2+3 all pass: **PROMOTE tier-2 cohort-wide**. V3 becomes auto-attached for all tier-2 ZEBRA entries.

If any gate fails at cohort level: fall through to per-name selectivity below.

## Per-name selectivity (sealed — applied only if cohort promotion fails)

If the cohort fails one or more of gates 1-3, compute per-name lift V3 over BASE.

**Per-name promotion criteria:**
- Minimum N: ≥ 50 cycles per ticker (Phase C / Phase 1 floor). Names with fewer cycles (likely PLTR, maybe TTD) are **excluded from promotion** but their data is kept for the cohort-level computation.
- Per-name lift: ≥ +$0/cyc V3 over BASE
- Per-name walk-forward stability: V3 lift positive in ≥ 3/4 splits where the name has entries

**Promotion outcomes:**
- Names that pass per-name criteria → AUTO-ATTACH list (overlay attached at entry like tier-1 NVDA/AMZN/GOOGL today)
- Names that fail per-name criteria → MARGINAL list (overlay available but trader-discretion, not automatic)
- Names with N < 50 → DEFERRED (revisit after another year of data)

## What "tier-2 fails entirely" means (sealed)

If ALL of the following hold:
- Cohort-level gates 1-3 fail
- Per-name selectivity yields zero names passing per-name criteria

Then the overlay rule remains tier-1-only. Update the operational rule to:
- ZEBRA tier-2 entries do NOT auto-attach the V3 overlay
- The four-state regime-conditional rule still applies if the trader manually chooses to attach an overlay to a tier-2 ZEBRA
- This is the conservative outcome — no change to current default behavior

## Slip-sensitivity sub-check (sealed — informational, not gating)

After the primary slip=0.25 run, re-run at slip=0.50 and report the per-name lift delta. Tier-2 names have wider bid-asks on average than tier-1 (smaller volume, lower IV); if slip=0.50 collapses the lift it's important context for the trader even if it doesn't affect formal promotion.

This sub-check is informational only — it does NOT alter the promotion decision under slip=0.25.

## What promotion looks like operationally

If cohort-wide PROMOTION:
- Update `scripts/monitor/zebra_overlay_rule.py` and `scripts/monitor/trade_construction.py` to attach V3 overlay to tier-2 ZEBRA entries identically to tier-1.
- The four-state regime-conditional strike rule (OTM10 / ATM / ITM5 / OTM10) applies to tier-2 entries as it does to tier-1.
- Update `docs/TRADING_PLAN.rtf` (manual) and memories.

If PER-NAME PROMOTION:
- Add `COHORT_ZEBRA_TIER2_OVERLAY_AUTO` list to `gate_config.py` containing the validated names.
- `trade_construction.py` checks membership before attaching V3 by default.
- For other tier-2 names, overlay is available manually but not auto-attached.

If FAIL:
- Document the result and leave overlay tier-1-only.
- Add a memory `project_zebra_overlay_tier2_rejected.md` with the per-name evidence.

## Decision rule summary

```
IF cohort gate 1 PASS AND gate 2 PASS AND gate 3 PASS:
    PROMOTE cohort-wide
ELSE:
    compute per-name selectivity
    IF ≥ 1 name passes per-name criteria:
        PROMOTE per-name auto-attach list (+ marginal list)
    ELSE:
        REJECT — overlay remains tier-1 only
```

## Risk of overfitting (sealed)

Tier-2 is a smaller cohort with several lower-vol diversifiers. Per-name lift is computed across ~100-160 cycles per name; that's PRELIMINARY-to-SUGGESTIVE territory per `feedback_pinning_efficacy_claims.md` for any individual name. The cohort-level result aggregates ~1,700-1,800 cycles, which is ADEQUATE.

Bias: testing tier-2 AFTER seeing tier-1 work introduces a confirmation prior. Mitigation: the decision rule is rigid (specific gates with hard thresholds), and the cohort-level test runs BEFORE per-name slicing. Per-name selectivity is only invoked if the cohort fails — limiting the per-name test to a fallback context rather than a primary search.

## Status

**SEALED 2026-05-17.** Backtest run pending — will execute after this seal.

## Cross-references

- `project_zebra_put_overlay_phase1_findings.md` — Phase 1 baseline (tier-1)
- `project_zebra_overlay_phase2_complete.md` — Phase 2 results (tier-1)
- `project_zebra_findings.md` — Original ZEBRA validation (defines tier-1/tier-2)
- `ZEBRA_UNIVERSE_EXPANSION_PREREG.md` — sister pre-reg for ZEBRA bare-structure tier-2 expansion (separate question)
- `feedback_pinning_efficacy_claims.md` — N-discipline rules
- Code: `scripts/backtest/zebra_long_put_overlay_backtest.py` (to be parameterized)
