# Pre-Registration — Market Narrowness as a Conditional Bear-Call Modifier

**Status: PARKED 2026-05-30 (never sealed, never run).** The *live motivation* dissolved before sealing: the user's own RSP-vs-SPY overlay showed the rally is **broad**, not narrow — the bullish resolution of the 2026-05-11 breadth divergence the user had pre-identified (see `project_breadth_divergence_20260511`). With narrowness not actually elevated, the discretionary "stealth soft bear" premise this pre-reg was built to validate is not currently operative, so there is nothing live to test against. The **historical** question (§2) remains valid and the design below is preserved intact — un-park and seal only if a future regime re-presents genuine top-tercile narrowness. Note also: the primary signal here needs RSP history, and `data/orats/by_ticker/RSP.parquet` is currently MISSING — a one-time extract is a prerequisite to running it.

Author drafted: 2026-05-29 (Opus 4.8 session). Parked: 2026-05-30. Conceptual basis distinct from all prior bear-call work (see §3).

---

## 1. The thesis being tested

The user's live observation: the major indices (SPY/QQQ/SOXX/DIA) are in a sustained but **narrow** rally — a handful of AI-complex mega-caps carrying a cap-weighted index while breadth deteriorates and whole sectors (healthcare, financials) are weak. The user is selling bear calls **while H1 is OFF** (SPY above its 200-DMA) on the conviction that this "stealth soft bear" makes the laggards unlikely to rally through a short call strike.

This pre-reg asks whether that conviction is a **validated, positive-expectancy edge** or a **negative-expectancy, high-hit-rate pattern** that merely *feels* like edge during a win streak.

## 2. Hypothesis (falsifiable, one sentence)

> Within H1-OFF bear-call cycles, cycles entered when market **narrowness is in its top tercile** have **positive** mean PnL (≥ +$0.02/sh, slip 0.50, managed-50%), beat the H1-OFF baseline by a meaningful margin, show a monotonic narrowness→PnL gradient, and stay positive across walk-forward windows.

Null: narrowness adds no durable signal to bear-call expectancy in H1-OFF regimes (the prior from all related studies).

## 3. Why this is conceptually distinct from prior REJECTED work

| Prior study | What it tested | Why this differs |
|---|---|---|
| `bear_call_below_ma` (5/05) | per-**name** 200-DMA position | this is **market-wide** narrowness, not per-name trend |
| `sector_etf_stage2_bearcall` (5/18, REJECTED) | **sector** breakdown as standalone **trigger** | this is a **continuous modifier**, not a standalone trigger |
| `if_breadth_gate` (5/12, REJECTED) | **binary** breadth extreme on **IFs** | binary fired 0×; this is a **continuous** covariate on **bear calls** |
| `bear_call_h1_h3` (4/24) | SPY<200-DMA+IVR (the **opposite**, confirmed-bear regime) | this is the **H1-OFF** (pre-confirmation) regime — the user's actual use case |

This is the one untested branch: **continuous market narrowness, as a modifier, in the H1-OFF regime.** New conceptual basis, not a tweak of a failed version.

## 4. Honest prior (stated before looking, to avoid fooling ourselves)

Rejection is the **expected** outcome. Three documented headwinds:
1. Bear calls universe-lose in **every** MA bucket outside H1 (`bear_call_below_ma`: all cells −$0.20 to −$0.31/sh).
2. **Bear-trend mean-reversion**: when SPY < 50-DMA, it stays down only 35% of the time; 5-week forward return **+2%** (`weekly_ivhv_trend_persistence`). Laggards bounce; that breaches short call strikes.
3. Narrowness-divergence is a **top-of-bull** phenomenon; H1 is a **post-break** phenomenon — largely **temporally disjoint** (cf. IF G4 = 0 cycles). So the H1-ON × narrowness cell is expected near-empty (reported, not gated).

If it passes anyway, *that* is the surprising, valuable result. If it fails, the value is **closure** — the user stops discretionarily fighting the tape and waits for H1.

## 5. Data & substrate (no new simulation — avoids added degrees of freedom)

- Reuse `data/profile/bear_call_moneyness_results.parquet` (76,277 cycles × 324 tickers, 45-DTE monthly OpEx, slip=0.50, managed-50% and held-to-expiry already simulated). **Same substrate as the MA-bucket study.**
- Join each cycle's `entry_date` to the narrowness series and H1 state. No re-pricing, no new exit rules.
- Narrowness history must cover the full cycle sample (2013–2026).

## 6. Signal definitions (FIXED before sealing — no measure-shopping after)

- **PRIMARY narrowness measure:** RSP/SPY relative strength. `ratio = RSP_close / SPY_close`; `narrowness_z = −zscore_252(ratio)` (higher = equal-weight underperforming cap-weight = narrower leadership). RSP history (2003+) covers the sample.
- **SECONDARY (robustness only, NOT a separate gate):** `−(% of S&P members above their 200-DMA)` from `data/profile/breadth_spx500_v2.parquet`. Reported alongside; does not change the verdict.
- **H1 (fixed):** SPY < 200-DMA **AND** SPY 252-day IV rank > 0.5. H1-OFF = NOT that.
- **Buckets:** narrowness_z terciles **within H1-OFF cycles** → LOW / MID / HIGH. Tercile boundaries computed once on the full H1-OFF sample, frozen.

## 7. Cohorts

- **Primary:** H1-OFF cycles, HIGH-narrowness tercile vs H1-OFF baseline (all H1-OFF). *This is the user's real use case.*
- **Secondary (report-only):** H1-ON × narrowness (expected sparse per §4.3).
- Exit rule: **managed-50%** (primary), slip **0.50**. Both fixed — no exit-rule or slippage shopping (`backtest_slip_assumption` discipline).

## 8. Pre-committed gates (proposed thresholds — adjust before sealing, then frozen)

| Gate | Criterion | Threshold |
|---|---|---|
| **A — expectancy** | HIGH-narrowness H1-OFF mean PnL/sh | ≥ **+$0.02** |
| **B — beats baseline** | HIGH mean − H1-OFF baseline mean | ≥ **+$0.10/sh** lift |
| **C — monotonic gradient** | mean PnL rises LOW → MID → HIGH | strictly monotonic |
| **D — walk-forward** | HIGH-bucket mean > 0 in disjoint windows | ≥ **3 of 4** (Phase-C: 10y train / 3y validate) |
| **E — N-adequacy** | HIGH-bucket cycle count | ≥ **150** total AND ≥ **30** per WF window; else verdict = **INCONCLUSIVE**, not "promising" |
| **F — tail / caution** | HIGH-bucket worst-cycle loss & p95 loss vs baseline | **no worse** than H1-OFF baseline (don't buy mean with a fatter melt-up tail — here the short IS the user's tail) |

**Decision rule:** promote ONLY if A, B, C, D, F all pass and E is adequate. Any gate fails → **REJECT** (closed). E inadequate → **INCONCLUSIVE** (re-evaluate only with materially more data).

## 9. Out-of-scope variants (FORBIDDEN post-hoc — would be hypothesis-search)

If the primary test fails, none of these may be run as a "rescue" without a *fresh, independent* conceptual pre-reg:
- Swapping the narrowness measure (RSP/SPY → breadth → VIX term → AI-basket ratio) after seeing results.
- Re-bucketing (terciles → quartiles/deciles) to find a positive cell.
- Restricting to a subset of names/sectors (e.g., "just financials/healthcare").
- Switching to held-to-expiry, or lowering slippage below 0.50, to rescue.
- Changing the H1 definition.
- Adding compound filters (narrowness AND term AND IVR …).

## 10. Negative-result plan

- "Market narrowness as a bear-call modifier" is **closed**. No immediate variant retest.
- Discretionary H1-OFF bear calls remain **explicitly discretionary**: tagged `placed=1` + a discretionary flag, **hard-capped ≤ ⅓ standard size**, logged, and scored **against the H1 baseline** as forward evidence. They are NOT framework-sanctioned.
- Breadth/narrowness keeps its validated role as a **risk-posture / sizing** input (lean new entries defensive, downsize bull-puts), per `breadth_divergence_20260511` — a posture claim, not a return claim.

## 11. Positive-result plan

- Promote as a **conditional bear-call enabler**: H1-OFF **AND** HIGH-narrowness.
- Per bear-call deployment discipline (`bear_call_h1_h3`): **forward paper N ≥ 10 cycles** before any live sizing, **⅓ size**, hold-to-expiry / Window rules, **no rolling** (H3 falsified).
- Re-confirm tail (Gate F) on the forward sample before scaling.

## 12. Artifacts (to be produced by the run)

- Script: `scripts/backtest/narrowness_bearcall_walkforward.py` (idempotent; cached narrowness fast-path).
- Output: `data/profile/narrowness_bearcall_walkforward.parquet`.
- Report: `reports/narrowness_bearcall_validation_<date>.md` with the per-gate table + the narrowness→PnL gradient + tail distribution.
