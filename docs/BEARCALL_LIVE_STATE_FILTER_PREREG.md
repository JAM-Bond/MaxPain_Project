# Pre-Registration — A "Live-State" Filter on Auto-Promoted Bear-Call Candidates

**Status: SEALED 2026-05-30 by user.** Thresholds in §5/§6 are frozen (IV-rank ≥ 50th pctile, Gate-A lift ≥ $0.05/sh, persistence window 126d, eligible set = reconstructed Gate-B-as-of-entry). Build artifacts in §9 may be implemented; the gates and decision rule were fixed BEFORE the run.

Author drafted: 2026-05-30 (Opus 4.8 session). Grounded in the existing auto-promotion mechanism, not a hand-derived chart pattern. Conceptually distinct from the five prior weakness-based bear rejections (see §3).

---

## 1. What this is, and why it exists

The nightly auto-promotion pipeline promotes a name to `COHORT_BEAR_CALL` on **walk-forward backtest expectancy** (Gate B: ≥3/4 time-splits positive, most-recent-split mean ≥ $5/contract, val_n ≥ 12). On 2026-05-30 it held/added UNH (4/4, mean $159/contract), STZ (3/4, $25), ZTS (3/4, $77). This is a real, statistically-clean selection — it identifies names whose bear-call has actually made money out-of-sample, sidestepping the failed feature-signal approach.

**But it has a look-back bias.** That expectancy is dominated by the period when each name *fell* — bear calls profited *because* the stock crashed. The pipeline rewards the history but is blind to whether the bearish setup is **still live today**. The three live positions show all three states:
- **STZ** — still below a *falling* 200-DMA, still a persistent underperformer → edge **live**.
- **ZTS** — the profit came from a crash that just happened; now pinned 1.9% under the short strike at 97% realized vol → edge **exhausted**.
- **UNH** — biggest backtest mean ($159), yet the stock has *recovered +62%*, sits *above a rising 200-DMA*, IV at the 2nd percentile → the mean is a **fossil**; the pipeline is short into a recovery.

This pre-reg tests whether a **current-state ("live") filter** applied on top of the pipeline's bear-call candidates improves forward outcomes — keeping STZ-like live downtrends, dropping UNH-like recovered names, and flagging ZTS-like exhausted crashes.

## 2. Hypothesis (falsifiable, one sentence)

> Among bear-call cycles on names the pipeline deems eligible (positive walk-forward expectancy), cycles entered in a **LIVE bearish state** — price below a **falling** 200-DMA, persistent relative weakness vs the market, AND implied-vol rank in the upper half of its 1-year range — have **materially higher** mean P/L than the un-filtered eligible set, the filtered lift is **positive across walk-forward windows and beats a placebo filter**, and the **RECOVERED state** (above a rising 200-DMA) shows **negative** mean P/L.

**Null:** the live-state filter adds nothing beyond the pipeline's existing walk-forward selection (the prior, given five prior weakness-signal rejections).

## 3. Why this is distinct from the five prior REJECTED studies

| Prior REJECTED work | What it tested | Why this differs |
|---|---|---|
| `bearcall_below_ma` | per-name *below 200-DMA* → bear-call | adds the **falling-slope** + **persistence** + **IV-rank** conditions and is applied only to **pipeline-eligible** names, not the whole universe |
| `theta_timing` (placebo-null) | transient market-stripped weakness → no-bounce timing | this is **persistent** weakness + a **premium** condition, conditioned on names with proven bear-call P&L |
| `sector_rs_persistence` / narrowness | sector / market-wide relative strength | single-name **current-state** filter, not a cross-sectional or market-wide signal |
| `sector_etf_stage2_bearcall` | a signal as a standalone **trigger** on a fixed cohort | a **filter on an already-validated candidate generator**, isolating the one untested ingredient (IV-rank) |

**The genuinely new ingredient is the IV-rank / premium-richness condition** — it aligns with the only validated edge family (sell vol when it is expensive: [[project_signal_vrp_termstruct]]). Trend-state alone is in rejected territory; the test must show the IV-rank condition is doing real work, not the trend.

## 4. Honest prior (stated before looking)

Mixed, leaning skeptical-but-plausible. Skeptical: five straight rejections of weakness-based bear edges; "below falling 200-DMA" is close to already-rejected `bearcall_below_ma`; bear calls are regime-bound to the broad market being down, and SPY is at highs. Plausible: the IV-rank ingredient is genuinely untested on bear calls and is the one factor tied to a validated edge; and the UNH-vs-STZ contrast is a concrete, mechanism-level reason to expect the *recovered* state to lose. Most likely outcomes, in order: (a) the **trend/persistence** part adds little but the **IV-rank** part adds a real, modest lift; (b) full null; (c) the whole filter works. Even outcome (a) is valuable — it would tell us to gate the pipeline's bear calls on premium-richness and to **drop recovered names like UNH**.

## 5. Definitions (FIXED before sealing — no measure-shopping after)

All on split-clean, gap-filled adjusted close ([[reference_split_ledger]]).
- **Eligible candidate set (SEALED — reconstructed Gate-B-as-of-entry):** a bear-call cycle enters the test only if its name passed Gate B using data STRICTLY BEFORE the cycle's walk-forward window — for a cycle in window W, the name qualified on the splits *prior* to W (≥3/4 of prior splits positive, most-recent prior-split mean ≥ $5/contract, val_n ≥ 12). This keeps eligibility itself out-of-sample (no look-ahead). The full bear-call substrate (`data/profile/bear_call_moneyness_results.parquet`) is the universe; only Gate-B-eligible-as-of-entry cycles are evaluated. Cycles in the earliest window (no prior splits) are excluded.
- **LIVE-state filter (all three required):**
  1. **Falling downtrend:** entry close < 200-DMA AND 200-DMA slope over trailing 21 trading days < 0.
  2. **Persistent relative weakness:** trailing **126-day** return of (name / SPY) < 0 (underperformed the market over ~6 months — *persistent*, not transient).
  3. **Rich premium:** name's ATM ~30-DTE implied-vol **rank ≥ 50th percentile** over the trailing 252 trading days.
- **RECOVERED state (the UNH cell, reported separately):** entry close > 200-DMA AND 200-DMA slope > 0.
- **Structure (fixed):** bear-call vertical, 45-DTE entry, OTM short (~16–20Δ), **managed-50%**, slip **0.50**. No exit-rule or slippage shopping ([[feedback_backtest_slip_assumption_validation]]).

## 6. Pre-committed gates (proposed thresholds — adjust before sealing, then frozen)

| Gate | Criterion | Threshold |
|---|---|---|
| **A — filter lift** | mean P/L(LIVE-state cycles) − mean P/L(all eligible cycles) | ≥ **+$0.05/sh** |
| **B — absolute expectancy** | mean P/L(LIVE-state cycles) at slip 0.50, mgd50 | **> 0** |
| **C — placebo** | LIVE lift must exceed the lift from a RANDOM filter of equal selectivity | real lift > **95th pct** of placebo lifts |
| **D — walk-forward** | LIVE-state mean P/L > 0 in disjoint windows 2021-23/22-24/23-25/24-26 | ≥ **3 of 4** |
| **E — recovered cell is negative** | mean P/L(RECOVERED state) | **< 0** (confirms the look-back-bias mechanism; if ≥0 the UNH thesis is wrong) |
| **F — IV-rank carries its weight** | LIVE lift with IV-rank condition − LIVE lift without it | ≥ **+$0.03/sh** (the new ingredient must matter) |
| **G — N-adequacy** | LIVE-state cycle count, total and per WF window | ≥ **150** total AND ≥ **30**/window; else **INCONCLUSIVE** |

**Decision rule:** PROMOTE the filter if A, B, C, D, F pass and G adequate; E is confirmatory (a pass strengthens the mechanism story but the filter can promote on A-D+F alone). Any of A/B/C/D/F fails → **REJECT** (the pipeline's existing selection stands un-filtered). G inadequate → **INCONCLUSIVE**.

## 7. Out-of-scope variants (FORBIDDEN post-hoc)

If null, none of these may be run as a rescue without a fresh sealed pre-reg: swapping the trend window (200/21d), the persistence window (126d), the IV-rank percentile, or the moneyness/DTE/exit; restricting to a subset of names or a sub-period; adding more compound conditions until something passes.

## 8. Result → action mapping (pre-committed)

- **Filter validates (A-D, F):** add the LIVE-state filter as a *gate on bear-call entries* — the pipeline still generates candidates by walk-forward expectancy, but a cycle only fires when the name is in a live bearish state. Concretely: **drop UNH-type recovered names**, keep STZ-type live downtrends, and (if E passes) treat the recovered state as a do-not-short flag. Forward paper N ≥ 10 before any live sizing ([[project_bear_call_h1_h3_findings]]), ⅓ size.
- **IV-rank passes but trend doesn't (likely):** gate bear-call entries on **premium-richness alone** (IV-rank ≥ threshold) + drop recovered names; this is the minimal validated version.
- **Null:** the pipeline's walk-forward selection stands as-is; document that no current-state filter beats it; revisit the look-back-bias concern only via a different, sealed approach. The UNH/ZTS management calls (book/close) stand on their own risk merits regardless.

## 9. Artifacts (post-seal)

- Script: `scripts/backtest/bearcall_live_state_filter_validation.py` (tags each bear-call cycle with trend-state + persistence + IV-rank; emits the gate table; placebo + walk-forward).
- Output: `data/profile/bearcall_live_state_filter.parquet`.
- Report: `reports/bearcall_live_state_filter_<date>.md` with the per-gate table, the LIVE/RECOVERED/all expectancy comparison, the IV-rank ablation, and the placebo distribution.

## 10. Sign-off

**Drafted by:** Claude Opus 4.8 (1M context) · **Drafted:** 2026-05-30 · **Sealed-by:** user · **Sealed-on:** 2026-05-30

Sealed (IV-rank midpoint, Gate-A $0.05/sh, persistence 126d, Gate-B reconstruction). Build artifacts in §9 may be implemented.

**RESULT 2026-05-30 — REJECT.** `reports/bearcall_live_state_filter_2026-05-30.md`. On 1,332 Gate-B-eligible OTM cycles: LIVE-state lift **−$0.071/sh (Gate A FAIL)**, below the placebo 95th (+0.080, **C FAIL**), 0/4 walk-forward (**D FAIL**), IV-rank ablation **−$0.018 (F FAIL** — the new ingredient hurts). Mechanism falsified: RECOVERED cell −$0.128 is *less* bad than LIVE −$0.235 (Gate E technically true but backwards). **Deeper finding:** Gate-B-eligible cycles themselves average **−$0.165/sh** forward — the pipeline's bear-call walk-forward selection does not persist out-of-sample (look-back bias at the pipeline level). 6th independent bear rejection.

## 11. Cross-references
- `scripts/maintenance/auto_promotion_gate_check.py` — the walk-forward Gate B this filter sits on top of.
- `scripts/qualifier/gate_config.py` `COHORT_BEAR_CALL` — the candidate set.
- [[project_theta_timing_null]] — the placebo discipline this pre-reg inherits; the prior that weakness-signals fail.
- [[project_bear_call_h1_h3_findings]] — the only validated bear-call regime gate (broad-market H1).
- [[project_signal_vrp_termstruct]] — the validated volatility-premium edge the IV-rank ingredient draws on.
