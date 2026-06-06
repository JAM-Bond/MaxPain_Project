# Macro-Regime Conditioning of Credit Spreads — Pre-Registration

**Sealed:** 2026-06-04
**Origin:** Phase-6 question — can the macro-sensitivity profile (a) justify an
asymmetric concentration tilt (concentrate in regime-favored names, lighten the
disfavored) and (b) unlock bull-market bear calls on macro-headwind names?

## Method (sealed)
- **Per-cycle P/L:** join macro-alignment onto the existing validated credit-spread
  backtests `data/profile/bull_put_moneyness_results.parquet` and
  `bear_call_moneyness_results.parquet` (per-cycle, 2015–2026, OTM moneyness only,
  ~45-DTE; slip already baked in). Primary metric `mgd50_pnl` (50%-managed exit);
  `held_pnl` reported as the lower-bound robustness check.
- **Macro-alignment score** at entry date t: dot product of the name's
  Phase-3-**stable** factor betas {b_level, b_inflation, b_dollar} with the trailing
  **60-trading-day cumulative standardized change** of those same factors (from
  `macro_panel.parquet`). Positive = recent macro has been a *tailwind* for the name
  (its betas align with how the factors have moved); negative = *headwind*.
  Using only the Phase-3-stable factors keeps the sign contemporaneously knowable
  (mitigates the full-sample-beta look-ahead).
- **Bull-market flag:** SPY > 200-DMA AND H1 off, from `regime_state` at entry.
- **Terciles:** pooled across all cycles by alignment score (top = aligned/tailwind,
  bottom = adverse/headwind).
- **Walk-forward:** 4 splits (2021-23 / 2022-24 / 2023-25 / 2024-26).

## H-A — bull_put regime alignment (concentration-tilt justification)
Gates (sealed):
| Gate | Threshold |
|---|---|
| A1 | aligned (top tercile) mean `mgd50_pnl` > adverse (bottom tercile), spread ≥ $0.05/sh |
| A2 | aligned > adverse holds in ≥ 3/4 walk-forward splits |
| A3 | ≥ 200 cycles per tercile per split |

Pass A1+A2+A3 → regime alignment improves bull_put → supports favored-sizing /
asymmetric concentration tilt (within a cap backstop). Fail → tilt stays advisory.

## H-B — bull-market bear calls on macro-headwind names
**Prior (stated up front): SKEPTICAL.** Un-gated bear_call loses in every bull-market
cohort cell we've tested; ~75% bull-trend persistence lifts weak names over the short
strike; relative underperformance ≠ absolute decline. This is the one new (macro-based)
selection criterion not yet tried.
Gates (sealed):
| Gate | Threshold |
|---|---|
| B1 | adverse-tercile bear_call, bull-market entries only, mean `mgd50_pnl` ≥ $0/sh |
| B2 | adverse-tercile beats the un-gated bull-market bear_call baseline by ≥ $0.05/sh |
| B3 | aligned>adverse ordering + B1 hold in ≥ 3/4 walk-forward splits; ≥ 150 cycles/split |

Pass B1+B2+B3 → macro-headwind unlocks a non-H1 path to bear_call (a real finding).
Fail → bull-market bear calls stay rejected; macro-headwind is not enough.

## Caveats (sealed)
- Regime-persistence assumption (trailing regime as proxy for the trade horizon).
- Full-sample betas (mitigated by stable-factor-only alignment); rigorous confirmation
  would re-run with rolling betas if H-A/H-B pass.
- OTM moneyness only; `mgd50` primary, `held` lower-bound.

## RESULTS (2026-06-04) — both NOT supported

`scripts/research/macro_regime_conditioning.py`. Clean nulls, not borderline misses.

**H-A (bull_put alignment): NOT supported.** aligned mgd50 +0.008 / adverse +0.004 / neutral −0.049 — aligned≈adverse (spread +0.004, need ≥0.05), non-monotonic, WF 0/4. Macro-regime tailwind does **not** improve bull_put outcomes. → asymmetric *return* tilt NOT justified.

**H-B (bull-market bear_call on headwind names): NOT supported — prior holds.** bull-market baseline mgd50 −0.232; adverse tercile −0.224; aligned −0.225 — all indistinguishable. Macro headwind does **not** make bull-market bear calls viable; bull drift overwhelms it (relative underperformance ≠ absolute decline, as predicted). B1/B2/B3 all fail.

**Interpretation:** macro-sensitivity is a validated **risk/diversification descriptor** (2a/2b/3/4), NOT a **selection/timing edge** — consistent with the whole signal graveyard. So **Phase 6 builds the Tier-1 diversification cap only** (risk management, self-justifying, no P/L test needed); the Tier-2 return tilt and bull-market bear calls are **rejected**. Clean null (aligned≈adverse≈baseline, 0/4 WF) → do not re-fish with alternate alignment defs.

## Status
**SEALED 2026-06-04. CLOSED 2026-06-04 — both hypotheses rejected.** Analysis: `scripts/research/macro_regime_conditioning.py`.
