# Anti-ZEBRA — Phase 2 Pre-Registration

**Sealed:** 2026-05-17
**Author:** Joseph Morris (with Claude Code)
**Predecessor:** `project_anti_zebra_findings.md` (Phase 1, 2026-05-14)
**Status of base data:** Phase 1 backtest already produced `data/profile/anti_zebra_results.parquet` (8,940 ticker-cycle-slip rows). Phase 1 reported a coarse train/val split — that finding is informational context but is NOT a Phase 2 decision input. The Phase 2 4-split walk-forward is new.

## Background

Anti-ZEBRA is the structural mirror of ZEBRA on the put side: buy 2× ITM put + sell 1× ATM put, same expiration. Net short-delta synthetic-short with defined max loss (debit paid). Phase 1 confirmed the mechanic works (88.6% fire rate; positive gamma capture vs naked short stock), but the un-gated cohort loses money (−$1.46/share). The H1 gate (`SPY below_200dma AND IVR_252 > 0.5`) flipped expected value to +$17.73/share — a $21 swing — implying the structure is regime-conditional, like bear_call.

H1 has fired on 8.9% of trading days in the 13yr ORATS history. Currently OFF as of 2026-05-15 (SPY above 200dma; last H1 fire 2025-04-22). Phase 2 ships the rule and cohort so the structure is deployment-ready the next time H1 activates.

## What gets validated

Three discrete sub-questions, each with its own decision rule:

1. **Base structure** — does the H1-gated anti-ZEBRA cohort produce positive cohort-mean P/L under 4-split walk-forward at slip=0.50?
2. **Cohort finalization** — which subset of the 36 v1.5 names should be the AUTO-attach tier-1 anti-ZEBRA cohort?
3. **Long-call overlay** — does adding a long call at entry (mirror of ZEBRA's long-put overlay) improve cohort outcomes?

Each is a separate gate. Failing one does not block the others.

## Methodology (sealed — applies to all three sub-questions)

- **Data source:** `data/profile/anti_zebra_results.parquet` (Phase 1 output) for sub-questions 1 and 2. New parquet from a new backtest for sub-question 3.
- **Universe:** v1.5 deployable cohort, 36 names ex-SPX (same as Phase 1).
- **Entry gate:** H1 active on entry date (`spy_below_200dma=1 AND spy_ivr_252 > 0.5`), looked up from `regime_state` table for each entry date.
- **Walk-forward splits:** 4 splits matching ZEBRA Phase C convention:
  - 2021-01..2023-12
  - 2022-01..2024-12
  - 2023-01..2025-12
  - 2024-01..2026-04
- **Primary slip:** 0.50 (matches anti-ZEBRA Phase 1 primary; conservative).
- **Sensitivity slip:** 0.25 (informational; does NOT alter promotion decision).

## Sub-question 1: Base structure — promotion gates (sealed)

| Gate | Threshold |
|---|---|
| 1. H1-ON cohort mean (slip=0.50) | ≥ $0/share |
| 2. Walk-forward stability | ≥ 3/4 splits with H1-ON cohort mean > $0 |
| 3. Minimum H1-ON N per split | ≥ 30 cycles (so the split mean isn't dominated by 3-5 outliers) |

If gates 1+2+3 all pass: **promote base structure cohort-wide**, use the full v1.5 universe under H1=ON.

If any gate fails: fall through to per-name selectivity in sub-question 2.

## Sub-question 2: Cohort finalization — promotion rules (sealed)

Compute per-name H1-ON statistics across the same 4 walk-forward splits.

**Per-name promotion criteria (must pass all):**
- Per-name H1-ON N ≥ 4 entries (matches the Phase 1 reporting floor)
- Per-name H1-ON mean ≥ $0/share across the full sample
- Per-name walk-forward: positive H1-ON mean in ≥ 3 of 4 splits OR (positive in 2 of 4 splits AND magnitude ≥ $10/share in those splits)

Names that pass → `COHORT_ANTI_ZEBRA_TIER1`. Names that fail or have N<4 → not promoted.

**Concentration caveat (sealed):** Names whose lift is concentrated in a single year (≥80% of total H1-ON P/L from one calendar year) get an explicit "concentrated-event" tag in the finding memo and live cohort comment. They are still promoted if they pass walk-forward — the discipline is documentation, not exclusion. This matches the CMG handling in `COHORT_ZEBRA_OVERLAY_AUTO`.

## Sub-question 3: Long-call overlay — promotion gates (sealed)

**Structure:** Buy long call at entry, same expiration as parent anti-ZEBRA, held to expiry. Tested variants:
- W1: ATM call (strike = spot)
- W2: 5% OTM call (strike = spot × 1.05)
- W3: 10% OTM call (strike = spot × 1.10)

The "OTM call" direction is symmetric to "OTM put" on ZEBRA: above-spot strike on the directional hedge side, which for an anti-ZEBRA (short-delta) means upside protection. This mirrors ZEBRA's downside-protection put overlay.

**Universe for overlay test:** Same v1.5 cohort, H1-ON entries only. Use the sub-question 2 cohort if base-structure cohort fails the cohort-wide gate.

**Promotion gates for overlay** (must pass all 3):
| Gate | Threshold |
|---|---|
| 1. Overlay cohort mean lift over bare anti-ZEBRA | ≥ $0/cyc |
| 2. Overlay cohort max-drawdown reduction | overlay worst ≤ bare worst |
| 3. Walk-forward stability of overlay lift | positive in ≥ 3/4 splits |

If overlay passes: per-name selectivity (mirror of ZEBRA overlay AUTO list) — only names where overlay lift is positive AND walk-forward stable get added to `COHORT_ANTI_ZEBRA_OVERLAY_AUTO`.

If overlay fails: anti-ZEBRA promoted without overlay (bare-structure only). Overlay remains discretionary.

## Implementation specs (sealed)

**Long-call overlay backtest script:** new file `scripts/backtest/anti_zebra_long_call_overlay_backtest.py`, modeled on `zebra_long_put_overlay_backtest.py`. Parameterized with `--cohort` and `--slip` to match the ZEBRA convention.

- Entry: same logic as anti-ZEBRA Phase 1 backtest (75-DTE entry on monthly OpEx).
- Per cycle: open anti-ZEBRA + 3 long-call variants on same expiration; settle ZEBRA + calls at intrinsic at expiry.
- Output: `data/profile/anti_zebra_long_call_overlay_results.parquet`.

**Gate-config additions (only if promoted):**
- `COHORT_ANTI_ZEBRA_TIER1`: list of promoted names from sub-question 2.
- `COHORT_ANTI_ZEBRA_OVERLAY_AUTO`: subset of TIER1 where overlay passed (if any).

**Daily alert wiring (only if promoted):** new card mirroring the ZEBRA overlay card, but only renders when H1=1 AND symbol is in `COHORT_ANTI_ZEBRA_TIER1`. Card displays the structure spec and (if applicable) the overlay strike per a symmetric regime-conditional rule.

**Regime-conditional overlay strike rule:** Defer to a future pre-reg (mirror of the ZEBRA bear-deepening rule). The Phase 2 overlay test here is at-entry V1/V2/V3 only (matches ZEBRA Phase 1 scope). Regime-conditional strike refinement is a separate analysis after the at-entry result is in.

## Risk of overfitting (sealed)

The base structure has one clearly validated regime sample (the 2022 drawdown) where GOOGL +$304 and AMZN +$284 H1-ON entries dominate. The per-name walk-forward in Phase 1's coarse train/val split showed these names FLIP sign (negative in train, positive in val). The 4-split walk-forward here will reveal whether 2022 still dominates or whether the lift extends to other periods.

The cohort-wide gate (≥3/4 splits positive at cohort level) is intentionally strict — if 2022 is the only profitable split, the cohort gate fails and per-name selectivity must justify any promotion.

The Phase 1 Stable+ names (CAR, GOLD, CLF, CNQ) have small magnitudes ($0.40-$7/share). Whether they survive the 4-split walk-forward at cohort-mean level is unknown until the analysis runs.

## What sealed promotion looks like

**Best case (all three sub-questions pass):**
- Base structure cohort-wide promoted.
- Long-call overlay attaches automatically for AUTO names when H1=1.
- Both wired into qualifier verdict logic + daily alert.

**Likely case (base passes per-name, overlay results vary):**
- `COHORT_ANTI_ZEBRA_TIER1` = subset of v1.5 names that pass per-name criteria.
- Overlay either passes per-name (subset of TIER1) or fails (overlay deferred).
- Qualifier issues GO for H1=1 + TIER1 membership.

**Worst case (base fails per-name):**
- No promotion. Anti-ZEBRA remains research-only.
- Document why. Re-evaluate when next bear regime gives more data.

## Cross-references

- `project_anti_zebra_findings.md` — Phase 1 (un-gated cohort fails, H1 flips it)
- `project_bear_call_h1_h3_findings.md` — H1 gate validation (lifted same gate)
- `project_zebra_overlay_phase2_complete.md` — overlay-methodology precedent
- `project_zebra_overlay_tier2_findings.md` — recent pre-reg → tier-2 precedent
- `project_regime_signals_symmetry.md` — bull/bear flip cleanly in regime windows
- `ZEBRA_PREREG.md` — pre-reg structure that this mirrors
- Code (existing): `scripts/backtest/anti_zebra_backtest.py`, `open_anti_zebra` in `structures.py`
- Data (existing): `data/profile/anti_zebra_results.parquet` (8,940 rows)
- Code (new): `scripts/backtest/anti_zebra_long_call_overlay_backtest.py` (to be written)

## Status

**SEALED 2026-05-17.** Walk-forward analysis + cohort finalization + overlay backtest pending execution this session.
