# Sector-ETF Stage-2 Break — Bear-Call Entry Trigger — Pre-Registration

**Status: SEALED 2026-05-18 by user.**
**Purpose:** Test whether the Stage-2 momentum-break signal (price below its 200-day moving average today, was above 30 trading days ago) can serve as a standalone bear-call entry trigger on sector ETFs, independent of the existing H1 broad-market gate (SPY < 200-DMA + IVR_252 > 0.50).

**Pairs with:** `docs/H2_PHASE2_PREREG.md` (sealed + rejected earlier today). H2 Phase 2 tested the same signal definition (R3) as a DEFENSIVE bull-put exclusion filter and rejected it on Gate C (0 of 5 live bull-put losers caught). This pre-reg tests the SAME signal as an OFFENSIVE bear-call entry trigger on a DIFFERENT cohort (sector ETFs). The two use-cases are structurally separate:
- H2 R3 ran against single-name bull-put failure dates → catches event AT entry day
- This pre-reg runs against sector-ETF bear-call cycles → uses event as positive trigger to OPEN bear-call

The signal definition is identical. The hypothesis being tested, the cohort, and the structure are different.

---

## 1. Why this exists

The framework's bear-call rules today require the H1 broad-market gate to be active before any bear-call entry fires:
- H1 = (SPY closes below its 200-day average) AND (SPY IVR_252 > 0.50)

H1 was validated 2026-04-26: +$0.092/cycle, 40% win at slip=0.50 mgd50 on SPY itself. The gate ties all bear-call activity to broad-market regime, which is conservative but leaves a known gap: a sector can be in a clean rolling-over pattern while SPY remains elevated (today's banks / financials being the live example). The current rule will not authorize a bear-call entry in that case.

**Exploratory observation (2026-05-18):** Stage-2 break events on 8 sector ETFs (91 fires across 13 years of OTM/mgd50/slip=0.50 cycles) produced pooled mean +$0.027/share with 82% win rate, a +$0.16/share lift over the ungated baseline. Five of eight sectors showed positive lift; none showed lift worse than -$0.01/share; XLF/XLU/IYR/XLK individually showed positive mean P/L when the signal fired. Per-sector sample sizes ranged 6-19 — small.

**Hypothesis to test under sealed gates:** Stage-2 break is an offensive bear-call entry trigger on sector ETFs, sufficient to authorize entries without requiring H1.

This is a new hypothesis. The exploratory observation is not a finding — it's the motivation. The decision rule below was set BEFORE the validation run.

---

## 2. Conceptual basis

Sector ETF rolling over from a definitive uptrend produces three structural advantages for a bear-call entered at that moment:

1. **Cushion intact at entry.** Price is just-below the 200-day average — neither deep in distress (where mean reversion ambushes the short) nor still in clean uptrend (where premium decay races against a rally).
2. **Momentum clearly turned.** The 30-day lookback excludes one-day fake-outs. By definition the break was sustained: above 30 days ago, below today.
3. **Entry timing aligns with the 45-DTE exposure window.** The bear-call expires before either deep mean-reversion (3-6 months out) or full bear extension (deep below 200-DMA, where vol crush is the dominant risk) typically resolves.

The mechanic is well-established in trend-following literature (Weinstein's Stage-2 → Stage-3 transition). The novelty here is testing it as a directional bear-call trigger, not as a pure long/short signal.

---

## 3. Sealed signal definition

A sector ETF matches **STAGE2_BREAK** on trading date D if BOTH:
- ETF close price was **above** its trailing 200-day moving average on date `D - 30 trading days`
- ETF close price is **below** its trailing 200-day moving average on date D

The 200-day moving average uses `min_periods=100` (matches existing pipeline). The 30-trading-day lookback uses `shift(30)` on the daily series (calendar-day equivalent ≈ 42 days).

Identical to H2 Phase 2's R3 definition (`lib/h2_phase2_definitions.py:r3_stage2_break`).

---

## 4. Sealed cohort

The 12 sector / broad-industry ETFs with extracted historical options data:

```
XLB  Materials
XLC  Communication Services
XLE  Energy
XLF  Financials
XLI  Industrials
XLK  Information Technology
XLP  Consumer Staples
XLU  Utilities
XLV  Health Care
XLY  Consumer Discretionary
IYR  Real Estate (proxy; XLRE has no extracted data)
SMH  Semiconductors (industry sub-slice)
```

XLRE excluded — no `by_ticker/XLRE.parquet`. Adding it would require a one-time historical extract (~5 min) and is a follow-up if the pre-reg promotes.

**Restriction:** No single-name candidates in Phase 1. If Phase 1 promotes, a Phase 2 pre-reg may extend the trigger to a single-name cohort (different cycle counts, different mean-reversion dynamics, requires separate validation). Out-of-scope here.

---

## 5. Sealed validation methodology

### Step 0 — Backtest re-run prerequisite
The existing `data/profile/bear_call_moneyness_results.parquet` contains 8 of 12 sector ETFs. XLB / XLC / XLP / XLY were extracted today (2026-05-18) but the aggregate bear-call backtest hasn't been re-run since. Before validation:
- Re-run `scripts/backtest/bear_call_moneyness_backtest.py` to regenerate the cycles parquet with full 12-sector coverage
- Verify all 12 ETFs produce cycles (any with <30 cycles → drop from cohort, log as data-gap)

### Step 1 — Per-cycle signal tag
For each sector-ETF bear-call cycle in the regenerated parquet:
- Compute the ETF's daily close series + ma200 + 30-day-lagged close & ma200
- Tag each cycle with `stage2_at_entry ∈ {0, 1}` based on entry_date

### Step 2 — Cohort definition
- Trade structure: bear-call vertical, 45-DTE entry, OTM short (30Δ short call), 50%-managed exit, slip=0.50
- Cohort = the 12 sector ETFs in §4 with cycles available
- Only cycles where `stage2_at_entry == 1` are evaluated as the "trigger ON" set
- Ungated baseline = all cycles in the cohort (trigger and non-trigger combined) for lift comparison

### Step 3 — Gate evaluation
Each of the five sealed gates below must PASS for promotion.

---

## 6. Sealed decision rule

A bear-call entry trigger of "STAGE2_BREAK on sector ETF" PROMOTES to live deployment IF AND ONLY IF all five of the following pass:

### Gate A — Pooled positive expectancy
- Pooled mean per-share P/L across all trigger-ON cycles ≥ **+$0.020/share** at slip=0.50, OTM, mgd50
- Equivalent to ≥ +$2.00 per contract on a 1-lot

### Gate B — Pooled win rate
- Pooled win rate across all trigger-ON cycles ≥ **78%**
- Strictly above the ungated baseline of ~75%; modest bar but must clear it

### Gate C — Cross-sector consistency
- ≥ **6 of 12** sector ETFs in the cohort show positive mean per-share P/L when trigger fires
- AND no individual sector shows mean per-share P/L worse than **-$0.10/share** when trigger fires (loss-cap on the worst single-sector cell)

This guards against the result being driven by one or two strong sectors with the rest flat-to-negative.

### Gate D — Walk-forward stability
- Pooled mean per-share P/L > 0 in ≥ **3 of 4** of the standard validation windows:
  - 2021-2023, 2022-2024, 2023-2025, 2024-2026
- Reuses the 4-split convention already in use for auto-promotion + Phase C work

### Gate E — Sample-size adequacy
- ≥ **100** trigger-ON cycles across the 12 sector ETFs combined
- (Current 8-sector exploration had 91; full 12-sector expected to exceed 100)
- Per-sector cycles allowed to be small; pooled total is the bar

**The rule sealed:** PROMOTE if Gates A AND B AND C AND D AND E all pass. SKIP otherwise.

---

## 7. What promotion looks like

If the rule passes:

1. `lib/sector_etf_stage2.py` — new module with `is_stage2_break_active(ticker, asof_date) -> bool` reading from `data/orats/by_ticker/{TICKER}.parquet`
2. `scripts/qualifier/gate_config.py` — add:
   - `COHORT_BEAR_CALL_STAGE2_SECTOR_ETF` — the list of 12 sector ETFs (frozen at promotion)
   - `STAGE2_SECTOR_BEARCALL_ENABLED = True` flag for future kill-switch
3. `scripts/qualifier/cycle_qualifier.py` — bear-call verdict path: if symbol is in `COHORT_BEAR_CALL_STAGE2_SECTOR_ETF` AND `is_stage2_break_active(symbol, run_date)` is True, emit GO regardless of H1 status (additive to current H1-conditional bear-call cohort)
4. `scripts/monitor/daily_alert.py` — surface "★ SECTOR-STAGE2 trigger active" annotation on any bear-call card derived from this path; distinguish visually from H1-derived bear-call cards
5. `docs/TRADING_PLAN.rtf` — v2.5 update describing the trigger + cohort + rationale (queue with existing v2.5 items)

If the rule does NOT pass: no integration. The 5-gate failure mode (which gate fired) determines the follow-up — see §9.

---

## 8. What we are NOT testing

Locked-out from this pre-reg to prevent hypothesis-search drift:

- **Single-name application.** Adding to non-ETF tickers requires a separate validation pass — different mean-reversion regime, different liquidity, different cycle volumes. If Phase 1 promotes, this is a candidate Phase 2.
- **Alternative lookback windows.** The signal uses 30 trading days. Not testing 10 / 60 / 90 / etc. Testing all permutations converts hypothesis-testing into hypothesis-searching.
- **Alternative moneyness.** OTM (30Δ short) only. Not testing ATM / ITM bear-calls under this trigger.
- **Alternative DTE entries.** 45-DTE only. Not testing 30 / 60 / 75 DTE.
- **Combining with H1.** The whole point is to test the trigger as a STANDALONE entry path. If both gates happen to fire, the position would be authorized either way; we're not testing the intersection.
- **Alternative exit rules.** Managed-at-50% only. Not testing held-to-expiry or other managed thresholds.
- **The defensive bull-put exclusion variant.** That hypothesis was sealed-and-rejected this morning (H2 Phase 2 R3); per pre-reg discipline, no Phase 3.

---

## 9. Negative-result plan

If any of Gates A-E fails:
- **No promotion.** Sector-level bear-call entries remain governed by H1 alone.
- Validation memo records which gate failed and the per-sector breakdown.
- The conceptual case is **NOT** automatically rejected (a single failed pre-reg doesn't rule out the mechanic across all parameter choices). However, **no immediate variant retest is permitted** — that would be hypothesis-searching. A future variant pre-reg requires:
  - A new conceptual rationale distinct from "tweak to make the failed version pass"
  - Sealed BEFORE looking at the variant's results
  - Approved by user before any code

If exactly one gate fails by a tiny margin and the other four pass cleanly, the validation memo documents that observation but does NOT promote. The bar was set in advance.

---

## 10. Falsification triggers (post-promotion monitoring)

If the rule promotes and goes live, the following would trigger a kill-switch review:

- Any 6-cycle rolling window of triggered entries with cumulative P/L < -$5.00/share → freeze trigger, investigate
- Per-sector cumulative P/L (across all live trigger fires) < -$3.00/share → drop that sector from the cohort
- Trigger fires on > 20 sector-cycles per quarter → unexpectedly high fire rate, investigate (historical baseline ≈ 7 per quarter)

These are monitoring tripwires, not gates.

---

## 11. Build artifacts (to be created post-seal)

- `scripts/backtest/sector_etf_bearcall_stage2_validation.py` — runs Step 0-3 above + emits gate verdicts
- `lib/sector_etf_stage2.py` — pure-function signal definition (re-used by validation AND live qualifier if promoted)
- `data/profile/sector_etf_bearcall_stage2_validation.parquet` — per-gate results + per-sector cycle-level rollup
- `reports/sector_etf_bearcall_stage2_validation_YYYY-MM-DD.md` — human-readable summary including the GO/NO-GO decision

If promotion:
- `scripts/qualifier/gate_config.py` + `cycle_qualifier.py` + `daily_alert.py` updates per §7
- Update to `docs/TRADING_PLAN.rtf` (queued v2.5)

---

## 12. Effort estimate

- Step 0 (re-run bear-call backtest with 12 sector ETFs): ~30 min
- Validation script + signal lib: ~2-3 hours
- Run + interpret + write findings memo: ~1 hour
- Promotion integration (only if Gates A-E pass): ~2-3 hours

Total if reject: ~4 hours. Total if promote: ~7 hours.

---

## 13. Sign-off

**Drafted by:** Claude Opus 4.7
**Drafted on:** 2026-05-18
**Sealed-by:** user
**Sealed-on:** 2026-05-18

Sealed. Build artifacts in §11 may be implemented.

## Pre-seal exploration log (2026-05-18 — informs but does not change sealed gates)

Before sealing, a variant of this pre-reg — "trade the worst-performing top-5 constituent when the parent ETF Stage-2 fires" (the user's instinct) — was explored in `scripts/backtest/sector_etf_stage2_constituent_targeting.py`. Headline result looked stronger than the ETF-direct version (+$0.91/share on N=91 cycles). On concentration audit, ABBV alone (4-5 cycles in 2018-2019) accounted for the entire +$83 dollar P/L; excluding it, the variant collapsed to -$0.19/share. This is a single-name tail-event artifact, not a tradeable systematic edge. The variant was rejected pre-seal in favor of the ETF-direct version captured here. A Phase 2 could revisit constituent-targeting with explicit per-name concentration caps (Gate D-style), but only if Phase 1 promotes.

---

## 14. Cross-references

- `docs/H2_PHASE2_PREREG.md` — companion pre-reg, R3 identical definition tested as defensive filter and rejected
- `reports/h2_phase2_validation_20260518.md` — H2 Phase 2 rejection report (R3 details)
- `lib/h2_phase2_definitions.py` — R3 source-of-truth definition (will be reused by `lib/sector_etf_stage2.py`)
- `project_bear_call_h1_h3_findings.md` — H1 validation that established the current SPY-based bear-call gate
- `scripts/backtest/sector_etf_bearcall_self_h1.py` — exploration of per-sector H1 (rejected this afternoon by inspection — informed today's hypothesis)
- `scripts/backtest/sector_etf_bearcall_transition_signals.py` — exploration that produced the +$0.16/share lift observation that motivates this pre-reg
- `data/profile/sector_etf_bearcall_transition_signals.parquet` — exploratory artifact (not validation; gates not yet applied)
- `docs/AUTO_PROMOTION_PIPELINE_PREREG.md` — auto-promotion pipeline (orthogonal; nightly cohort refresh, not entry trigger)
