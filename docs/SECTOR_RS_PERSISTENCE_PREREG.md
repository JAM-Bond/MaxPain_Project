# Pre-Registration — Sector Relative-Strength Persistence over a 45-DTE Horizon

**Status: SEALED 2026-05-29 by user.** Thresholds in §6/§8 are now frozen. Build artifacts in §12 may be implemented; the gates and decision rule below were fixed BEFORE the run.

Author drafted: 2026-05-29 (Opus 4.8 session). **Directly unblocked by the 2026-05-29 split-adjustment fix** — see §5. Conceptually distinct from the rejected sector-ETF bear-call work (see §3).

---

## 1. The question being tested

Does a sector ETF's recent relative strength versus SPY **persist** over the next ~45 trading days (the option-exposure horizon), or does it **mean-revert**? Put concretely: when a sector has materially lagged SPY over the trailing 6 months, is the next 45 days more likely to be **more lagging** (momentum/persistence) or a **snap-back** (reversion)?

This is a **phenomenon** question about the relative-strength signal itself — measured at the sector level, cross-sectionally, decoupled from any option structure. It is **not** a return-claim on a trade. The motivating live case is **XLV (Health Care)**: ~−15% vs SPY over 6 months, rolled from +11% above its 200-DMA to ~flat / on support. The user's open question is whether that weakness is informative (keep underweighting / avoid selling premium into it) or a setup for reversion (fade the weakness). The point of this pre-reg is to answer that with the *general* sector behavior over 13 years, then read XLV against it — not to design a trade around XLV specifically.

## 2. Hypothesis (falsifiable, one sentence)

> Ranking the sector cohort cross-sectionally each month by trailing-6-month relative return vs SPY, the **forward 45-trading-day** relative return vs SPY is **monotonically increasing** across BOTTOM → MID → TOP trailing-RS terciles, with a **positive, walk-forward-stable TOP−BOTTOM spread** — i.e. sector relative strength persists, weak stays weak and strong stays strong over the horizon.

**Null:** sector RS is not persistent over 45 trading days — trailing-weak sectors are as likely to outperform SPY forward as to underperform (random / reversion). This is the conservative prior given the documented +2% bear-bounce and the cohort-wide rejection of sector bear-calls.

**Symmetric alternative (explicitly admissible, not a moving goalpost):** the gradient is monotonic in the **opposite** direction (TOP−BOTTOM spread reliably **negative**) → sector RS **mean-reverts**. This is a distinct, also-actionable finding and its decision branch is pre-committed in §6 so confirming it is not post-hoc rescue.

## 3. Why this is conceptually distinct from prior REJECTED work

| Prior study | What it tested | Why this differs |
|---|---|---|
| `sector_etf_stage2_bearcall` (5/18, **REJECTED** 4/5 gates) | Stage-2 break as a bear-call **entry trigger** (return claim on a structure) | this makes **no structure claim**; it tests whether the RS *signal* has predictive validity at all |
| `bear_call_below_ma` (5/05, REJECTED) | per-name 200-DMA position → bear-call PnL | per-name absolute trend vs **cross-sectional sector-relative** rank; outcome is forward **relative** return, not option PnL |
| `narrowness_bearcall` (5/29, **PARKED**) | market-wide breadth/narrowness as a bear-call modifier | market-wide single covariate vs **cross-sectional sector dispersion**; no option structure here |
| `weekly_ivhv_trend_persistence` (5/xx) | **absolute** SPY trend persistence (SPY<50DMA → +2% fwd) | absolute index level vs **sector-relative** rank; that finding is an input to the §4 prior, not the same test |

The novel, untested object: **cross-sectional sector relative-strength persistence over the 45-DTE window, on split-clean data.** Not a tweak of a failed version — a different measurement of a different quantity.

## 4. Honest prior (stated before looking)

Genuinely uncertain — unlike the narrowness pre-reg, rejection is **not** the foregone conclusion. Two forces pull opposite ways:

- **For persistence:** cross-sectional momentum (relative-strength) is one of the more robust documented anomalies, and it is *relative* (sector vs SPY), which sidesteps the absolute-trend mean-reversion that sank the bear-call studies. A 45d horizon is the classic momentum sweet spot (shorter = reversal, much longer = reversal).
- **For reversion:** the documented +2% bounce when SPY is below its 50-DMA, and the cohort-wide failure of "lean against weak" sector bear-calls, both say weak snaps back on this horizon.

Honest expectation: a **weak** persistence signal (small positive TOP−BOTTOM spread, possibly failing the magnitude bar but not the sign), or null. A clean, walk-forward-stable persistence result would be genuinely valuable; a clean reversion result would be equally valuable (and would tell the user to *stop* underweighting laggards). The low-value outcome is null/inconclusive.

## 5. Why clean data is the precondition (the unblock)

Relative strength is `sector_close / SPY_close`. On the **raw** ORATS `stkPx` archive, 6 of the cohort split during the sample (XLB/XLE/XLK/XLU/XLY 2:1 on **2025-12-05**, SMH 2:1 on 2023-05-05) — each an uncorrected ~−50% discontinuity in the numerator. That injects phantom relative-return shocks on split dates and corrupts every trailing/forward RS window spanning one. The 2026-05-29 fix (`lib/adjusted_close.py`, readers migrated in commit `f4f551b`) removes exactly this. **This pre-reg MUST compute every close via `lib.adjusted_close.load_adjusted_close` — never raw `stkPx`.** (Concrete proof it matters: post-fix XLU reads +0.4% vs its 200-DMA; raw read −28.3%.)

## 6. Signal definitions (FIXED before sealing — no measure-shopping after)

- **Price series:** `lib.adjusted_close.load_adjusted_close(ticker)` for every sector ETF **and** SPY. Split-adjusted, continuous.
- **Relative-strength ratio:** `rs(s, t) = adj_close(s, t) / adj_close(SPY, t)`.
- **Trailing RS signal (the rank key):** `rs_trail(s, t) = ln( rs(s, t) / rs(s, t − 126) )` — trailing **126 trading days ≈ 6 months** relative log-return vs SPY. (6m matches the XLV observation.)
- **Forward RS outcome (the thing predicted):** `rs_fwd(s, t) = ln( rs(s, t + 45) / rs(s, t) )` — forward **45 trading days** relative log-return vs SPY (matches the 45-DTE exposure window). Observations with no `t+45` bar are dropped.
- **Sampling dates:** the monthly bear-call/bull-put **entry dates** from `lib.opex_calendar` (45-DTE before each monthly OpEx), 2013–2026 — i.e. measure at the moments the framework actually makes entry decisions. (Overlap caveat: adjacent monthly samples share ~half their forward window. Addressed by the cross-sectional rank design + disjoint walk-forward windows; reported, not gated.)
- **Buckets:** at each sampling date, rank the cohort sectors **cross-sectionally** by `rs_trail` and assign **terciles** → BOTTOM / MID / TOP. ("Weak sector" is inherently relative to its peers on the same date.) A sector with insufficient history at date `t` (e.g. XLC pre-2019) is simply absent from that date's ranking.

## 7. Cohort (FROZEN at seal)

The 11 GICS sector partition (clean cross-sectional ranking — no overlapping sleeves):

```
XLB  Materials              XLP  Consumer Staples
XLC  Communication Services XLU  Utilities
XLE  Energy                 XLV  Health Care
XLF  Financials             XLY  Consumer Discretionary
XLI  Industrials            IYR  Real Estate (proxy; XLRE not extracted)
XLK  Information Technology
```

- **SMH excluded** — semiconductors are a sub-slice of XLK (Info Tech); including it double-counts tech exposure in a cross-sectional ranking. (It may be revisited as a separate momentum study, not here.)
- **XLC** carries history only from 2018-08; it contributes to rankings only from then. Not a defect — it just has fewer observations. All 11 present 2013→2026 except XLC.
- Benchmark: **SPY** (cap-weighted broad market), the same benchmark the regime layer uses.

## 8. Pre-committed gates (proposed thresholds — adjust before sealing, then frozen)

All measured on `rs_fwd` (forward 45td relative log-return vs SPY), pooled across the full sample unless noted.

| Gate | Criterion | Threshold |
|---|---|---|
| **A — spread sign & magnitude** | mean `rs_fwd`(TOP) − mean `rs_fwd`(BOTTOM) | **≥ +1.0%** (persistence) |
| **B — monotonic gradient** | mean `rs_fwd` ordered BOTTOM < MID < TOP | strictly monotonic |
| **C — directional reliability** | share of BOTTOM-tercile obs with `rs_fwd` < 0 **and** share of TOP-tercile obs with `rs_fwd` > 0 | **each ≥ 55%** (above coin-flip) |
| **D — walk-forward stability** | TOP−BOTTOM spread > 0 in disjoint windows 2021-23 / 2022-24 / 2023-25 / 2024-26 | **≥ 3 of 4** |
| **E — N-adequacy** | observations per tercile, total **and** per WF window | **≥ 150** total/tercile AND **≥ 30** per tercile per window; else **INCONCLUSIVE** |

**Decision rule (sealed):**
- **PERSISTENCE CONFIRMED** → A, B, C, D all pass and E adequate.
- **MEAN-REVERSION CONFIRMED** → the *mirror* holds with the same bars: spread ≤ **−1.0%**, gradient strictly monotonic **TOP < MID < BOTTOM**, reliability ≥55% the other way, walk-forward spread < 0 in ≥3/4, E adequate.
- **NULL** → neither set of sign-consistent conditions is met (e.g. spread inside ±1.0%, or non-monotonic, or walk-forward unstable).
- **INCONCLUSIVE** → E inadequate; re-evaluate only with materially more data.

Persistence and reversion are **symmetric pre-committed branches**, so reporting whichever fires is not post-hoc. Both feed §10/§11.

## 9. Out-of-scope variants (FORBIDDEN post-hoc — would be hypothesis-search)

If the primary test is NULL, none of these may be run as a "rescue" without a fresh, independent conceptual pre-reg:
- Swapping the trailing window (126d → 63 / 252 / etc.) or the forward window (45d → 21 / 63) to find a horizon that works.
- Re-bucketing (terciles → quartiles/deciles/median) to surface a positive cell.
- Switching from cross-sectional rank to absolute-RS thresholds, or to a single sector, after seeing results.
- Restricting to a sub-period, a sub-set of sectors, or a regime filter (H1 on/off, IVR, etc.) chosen after the fact.
- Adding an absolute-trend or breadth covariate to compound the signal.

## 10. Result → action mapping (pre-committed; respects existing walls)

This study informs **posture and selection**, never a direct structure promotion — a return-claim on any option structure requires its **own** pre-reg.

- **If PERSISTENCE confirmed:** bottom-tercile sectors are expected to keep lagging over the horizon. Actions: (a) **avoid selling bull-puts** on names in bottom-tercile sectors (you'd be selling premium into a persistent downtrend); (b) a defensive **sizing/posture tilt** on those sectors; (c) a *candidate future structure pre-reg*, sealed separately, with explicit acknowledgement that sector **bear-calls** are already rejected cohort-wide — so any structure would have to be something else (e.g. avoidance rules, or bull-puts favored toward **top**-tercile sectors). **XLV live read:** if XLV is currently bottom-tercile, persistence says continue to underweight / avoid premium-sell on healthcare names near support.
- **If MEAN-REVERSION confirmed:** bottom-tercile weakness is more likely to snap back. Actions: **stop fading laggards**; XLV's lag becomes a *future* bull-put candidate **after** stabilization (not an immediate entry — the bounce timing is unmodeled here). Explicitly do **not** add bear-calls to weak sectors (already rejected).
- **If NULL:** sector RS carries no durable 45d edge in either direction; the user's discretionary read on XLV stays discretionary and uninformed-by-this. Closes the "weak sectors keep lagging" question. Breadth/RS keeps only its already-validated **risk-posture** role (`breadth_divergence_20260511`).

## 11. Negative / positive-result plan

- **Persistence or reversion confirmed:** record the finding + the cross-sectional gradient + per-window table in the report; update memory; queue a *separate* structure pre-reg only if the user wants to convert posture into a trade. No structure ships from this doc.
- **Null:** the phenomenon is closed; no immediate variant retest (that would be hypothesis-search). A future variant needs a new conceptual basis, sealed before looking.
- Either way the **XLV live read** (§10) is produced as a one-line "as-of" interpretation, clearly labeled as an application of the general finding, not a separate test.

## 12. Artifacts (to be produced by the run, post-seal)

- Script: `scripts/backtest/sector_rs_persistence_walkforward.py` (idempotent; uses `load_adjusted_close`; no new pricing/simulation — pure relative-return arithmetic, so minimal added degrees of freedom).
- Output: `data/profile/sector_rs_persistence.parquet` (per-observation: date, sector, rs_trail, tercile, rs_fwd).
- Report: `reports/sector_rs_persistence_validation_<date>.md` — per-gate table, BOTTOM/MID/TOP forward-RS gradient, per-window spreads, and the XLV as-of read.

## 13. Sign-off

**Drafted by:** Claude Opus 4.8 (1M context)
**Drafted on:** 2026-05-29
**Sealed-by:** user
**Sealed-on:** 2026-05-29

Sealed as-is (no threshold edits). Build artifacts in §12 may be implemented.

## 14. Cross-references

- `lib/adjusted_close.py` + `reference_orats_split_adjustment` — the clean-data precondition (§5).
- `docs/SECTOR_ETF_STAGE2_BEARCALL_PREREG.md` + `project_sector_etf_stage2_bearcall_rejected` — why no sector-bear-call structure may ship from a positive result.
- `docs/NARROWNESS_BEARCALL_PREREG.md` (PARKED) — the parked sibling whose live premise dissolved.
- `project_breadth_divergence_20260511` — breadth/RS in its validated risk-posture role.
- `project_weekly_ivhv_trend_persistence` — the +2% absolute-trend bounce feeding the §4 reversion prior.
