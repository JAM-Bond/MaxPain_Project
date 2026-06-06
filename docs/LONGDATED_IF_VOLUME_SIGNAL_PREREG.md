# Long-Dated Inverted Fly + Volume-Decline Signal — Pre-Registration

**Sealed:** 2026-06-03
**Author:** Joseph Morris (with Claude Code)
**Origin:** Burry's volume-thinning observation ("extended names see volume fade before tops/drops") + the user's question on long-dated inverted flys matched to MAR-2027 (the book's long-put maturity and Burry's timeframe). See `project_burry_predictions_ledger.md` (Methodology + NVDA/PLTR/NVDA volume tells).

## The unifying thesis

These are one research thread, not two:
- **Study B (volume-decline signal)** = a *selection* signal — which extended names are about to make a large move.
- **Study A (long-dated inverted fly)** = the *structure* that monetizes a large move with defined risk and survivable carry.

The inverted fly is long-vol / **net debit** (`open_inverted_fly`: long ATM call+put, short wings; rejects if debit≤0). Its weakness is theta bleed. Going long-dated (~290 DTE, MAR-2027-class) does not remove theta — that would be arbitrage — but cuts the *per-day* bleed (theta ~ 1/√T), giving the thesis runway. The volume signal supplies the move that justifies carrying the bleed. We validate each independently first; combination is a third, later step.

**No-arbitrage note (sealed framing):** a credit, positive-theta structure that pays on a big move does not exist. "Put it on as a credit" = the short `iron_fly` (already tested and REJECTED) which pays on *no* move. The convex payoff requires the debit; long-dated is the only honest lever on the carry.

## Feasibility confirmed (2026-06-03)
- ORATS `by_ticker` archive: 663 tickers, expiries out to ~1,080 DTE; **100% of trade-days have a ≥270-DTE expiry** back to 2013 (2020 for PLTR). Study A runs on existing data.
- ORATS chains carry **option volume** (`cVolu`/`pVolu`, 100% populated) → the option-volume arm of Study B runs now, zero ingest. **Equity share volume** is absent everywhere in our stores → must be ingested (yfinance) for the equity arm.

---

# STUDY A — Long-dated inverted fly (MAR-2027-class)

## Methodology (sealed)
- **Structure:** `open_inverted_fly` (long ATM call + long ATM put, short OTM call + short OTM put; net debit). Held-to-expiry settlement at intrinsic = lower bound (per `feedback_backtest_held_to_expiry_lower_bound`).
- **DTE feasibility frontier (sealed — replaces a single fixed expiry):** Burry's wide-leg risk is real, so we do NOT assume MAR-2027 is tradeable. First sweep a DTE ladder — **90 / 120 / 180 / 270 / 290 (MAR-2027) / 365** — and measure entry feasibility (combined bid-ask width vs debit, per Gate 1) at each. Define **DTE\*** = the **longest** maturity on the ladder that passes the feasibility bar. The structure study (Gates 2-5) then runs at DTE\* (MAR-2027 preferred if it is ≤ DTE\*).
  - **Six-month floor (sealed, per user):** target DTE\* ≥ **180** (~6 months). If DTE\* < 180, the long-dated thesis is *liquidity-constrained* — report that as a first-class finding, still run the structure study at DTE\* (longest feasible), and flag the limitation. If no rung ≥ 90 DTE passes, STOP.
- **Wing-width sweep (sealed):** 5%, 10%, 15% of spot (spans prior IF findings: "3-5% wings" baseline, "10-15% wings dominate crashes").
- **Universe:** the validated IF cohort + v-expansion names (per `project_if_universe_expansion`), with an explicit **mania sub-cohort tag** {NVDA, PLTR, and any AVGO/AMD/semis in-universe} reported separately.
- **Entry cadence:** one entry per name per calendar month (fixed trading day = monthly OpEx Friday), targeting the ~290-DTE expiry. Overlapping positions allowed.
- **Exit variants (all reported):** (i) held-to-expiry; (ii) managed +50% of max payoff / −50% of debit stop; (iii) 50%-profit-only (prior IF winner); plus a 60-DTE-remaining time stop on the managed variants.
- **Primary slip:** 0.25/leg; **sensitivity:** 0.50/leg. (Long-dated legs are wider — see Gate 1.)
- **Walk-forward splits (sealed, match anti-ZEBRA):** 2021-01..2023-12 / 2022-01..2024-12 / 2023-01..2025-12 / 2024-01..2026-04.
- **Baseline:** existing 45-DTE IF results over the same names/period.

## Promotion gates (sealed)
| Gate | Threshold |
|---|---|
| **1. Live-execution feasibility frontier (HARD pre-condition)** | At each DTE rung, the 4-leg structure must be quotable with combined bid-ask width ≤ **40% of entry debit** on ≥ **70%** of attempted entries (from ORATS bid/ask). Output = **DTE\*** (longest passing rung). Per `feedback_backtest_slip_assumption_validation`. If DTE\* < 180 → liquidity-constrained finding (proceed at DTE\*, flag it). If no rung ≥ 90 passes → STOP; no downstream gate matters. |
| **2. Structure viability** | Long-dated IF cohort-mean P/L ≥ **$0/cycle** at primary slip, managed 50%-only variant. |
| **3. Walk-forward stability** | ≥ **3/4** splits positive cohort mean. |
| **4. Min N per split** | ≥ **30** completed cycles. |
| **5. Beats 45-DTE on risk-adjusted terms** | Long-dated must beat the 45-DTE baseline on **P/L per dollar of debit at risk** (else there's no reason to tie up capital for 10 months). |

Gate 1 fail → STOP. Gates 2-5 fail → IF stays 45-DTE-only; document. All pass → proceed to combination with Study B's signal as entry filter.

---

# STUDY B — Volume-decline signal (option volume now; equity volume after ingest)

## Methodology (sealed)
- **Volume measures (two, reported separately):** (i) **option volume** = daily (`cVolu`+`pVolu`) per name, available now; (ii) **equity share volume**, ingested via yfinance for the universe over the backtest window.
- **Extension definitions (two, reported separately):** a name is "extended" on date *t* if EITHER (E1) close ≥ **8% above its 200-DMA**, OR (E2) within **5% of its 52-week high**.
- **Volume-decline definition (sealed):** trailing **20-day avg volume ÷ trailing 60-day avg volume**. Declining = ratio ≤ **0.85**. Sensitivity (informational): 0.75 / 0.90.
- **Comparison cohort:** extended names with **stable/rising** volume (ratio ≥ 1.00) — the control.
- **Forward windows (sealed):** **25** and **60** trading days (25d matches the 200dma+IV/HV magnitude study).
- **Targets (both, compared — per user):**
  - (T-mag) magnitude: mean |forward return| and **P(|ret| > 5%)** over the window.
  - (T-down) downside tail: mean forward **minimum return** (worst drawdown) and **P(ret < −10%)** over the window.
- **Walk-forward:** same 4 splits as Study A.

## Predictiveness gates (sealed)
| Gate | Threshold |
|---|---|
| **1. Magnitude lift** | declining-volume-extended cohort P(|ret|>5%) higher than stable-volume-extended cohort by ≥ **5 pp**, AND mean \|forward return\| higher. |
| **2. Downside lift** | declining-volume-extended cohort P(ret<−10%) higher than control by ≥ **3 pp** AND worse mean drawdown. |
| **3. Walk-forward stability** | the qualifying lift (whichever of 1/2 fires) holds in ≥ **3/4** splits. |
| **4. Adequacy** | ≥ **100** name-days per cohort per split, else label finding 🟠 SUGGESTIVE not 🟢 ADEQUATE (per SOUL §Adequacy). |
| **5. Beats the existing magnitude baseline** | the signal must add lift *over* the plain "extended" baseline from `project_200dma_ivhv_v1_findings` (70% >5% in 25d) — i.e., volume decline must be incremental, not just re-deriving extension. |

**Directionality verdict (sealed):** if Gate 1 passes but Gate 2 fails → signal is **magnitude-only** (feeds symmetric IFs). If both → signal carries a **downside** tilt (also feeds put-side/crash hedges). If neither → signal REJECTED; document, do not re-fish.

## Anti-overfitting / discipline (sealed)
- Pre-registered thresholds above are frozen; sensitivity params are informational and do NOT move verdicts.
- Beware our standing lesson "**high move freq ≠ edge**" (`project_inverted_fly_standalone_candidates`): a signal that merely finds volatile names is not an edge — Gate 5 enforces incremental lift.
- Option-volume and equity-volume arms are scored independently; if they disagree, report both, promote neither on the strength of the other.

---

## What runs now vs. needs ingest
- **Now (existing data):** Study A full backtest; Study B option-volume arm.
- **Parallel ingest:** equity daily volume (yfinance) for the universe over 2013→2026 → then Study B equity arm.
- **Combination step (only if BOTH A gates 2-5 pass AND B promotes a signal):** re-run Study A entries filtered to dates where the volume signal is active on the name; gate = combined beats unfiltered long-dated IF on cohort mean + walk-forward. Separate addendum, not sealed here.

## Future addenda (explicitly OUT of scope now — do not build)

- **Tax-loss-harvest layer (parked, per user 2026-06-03).** Burry's long-put philosophy is "I'm right about the AI meltdown, I don't know *when*; the small premiums I lose while waiting come back many-fold when I'm right" — and meanwhile the realized losses on rolled/expired puts **defray tax on gains elsewhere.** That tax-defrayal materially changes the after-tax economics of carrying a long-vol book. **Revisit only once the system throws off consistent, meaningful (after-paper) profits** — there is nothing to offset until then, and we are gross-only through the paper window (`feedback_paper_pnl_gross_only`, paper-test through ~2026-08-19). When live: model after-tax P/L of the long-dated IF / long-put sleeve including loss-harvest credit against the credit-spread income book. See `feedback_tax_loss_harvest_keep_thesis`.

## New artifacts (to be created)
- `scripts/backtest/longdated_if_backtest.py` → `data/profile/longdated_if_results.parquet`
- `scripts/backtest/volume_signal_study.py` → `data/profile/volume_signal_results.parquet`
- `scripts/research/ingest_equity_volume.py` → equity-volume table/parquet
- Findings memos on completion; gate-config / alert wiring only if promoted.

## Cross-references
- `project_burry_predictions_ledger.md` — origin (volume tells, MAR-2027 timeframe)
- `project_if_phase_a_batch_findings` / `_phase_b` / `_phase_c_findings` — IF prior work (45-DTE, wing widths, term-inv gate, 50%-only winner)
- `project_inverted_fly_wide_wings_findings` — wing-width precedent
- `project_200dma_ivhv_v1_findings` / `_v2_findings` — extension→magnitude baseline (Gate B5)
- `feedback_backtest_slip_assumption_validation` — Gate A1 (live-execution before verdict)
- `feedback_backtest_held_to_expiry_lower_bound` — exit-variant framing
- `project_high_price_underlying_option_friction` — long-dated wide-spread risk
- Code (existing): `open_inverted_fly` in `scripts/backtest/structures.py`; ORATS `data/orats/by_ticker/`

## Results log

### Study A, Gate 1 — feasibility frontier: **PASS (2026-06-03)**
`scripts/backtest/longdated_if_feasibility.py` → `data/profile/longdated_if_feasibility.parquet` (64,092 rows, 35 names, monthly entries 2013→2026). Burry's wide-leg worry does **not** bind for this (liquid) cohort:

| Wing | 180 DTE pass% | 270 | 290 (MAR-27) | 365 |
|---|---|---|---|---|
| 5% | 82% | 80% | 80% | 72% |
| 10% | 93% | 91% | 90% | 86% |
| 15% | 95% | 94% | 94% | 92% |

**DTE\* = 365** (longest rung tested; all clear the 70%/40%-of-debit bar). MAR-2027 (~290) is comfortably feasible — median width/debit ratio just **0.07–0.10** at 10–15% wings. Mania targets all clear at 290: NVDA 99–100%, PLTR 95–100%, AMD 95%, AVGO 80–92%, TSLA/GOOGL/AMZN 98–100%. **Six-month floor cleared by a wide margin.** Note: wider wings are *more* feasible at long DTE (bigger debit → smaller ratio); 5% wing is the marginal case at 365.

**Decision:** run the structure study (Gates 2–5) at **290 DTE primary** (MAR-2027 — matches the book's long puts + Burry's timeframe), with 365 as a feasible extension and 180 as a shorter-carry comparator.

### Study A, Gates 2–5 — structure study, HELD-TO-EXPIRY lower bound (2026-06-03)
`scripts/backtest/longdated_if_backtest.py` → `data/profile/longdated_if_results.parquet` (39,148 settled cycles). **Lower bound only — no verdict.**
- Engine sanity: wing width is the dominant lever (5% ≈ 0/neg, 10% +, 15% best), replicating prior IF wing findings → settlement math trusted.
- At 10–15% wings, **all DTEs positive** and walk-forward stable (15% wing: 4/4 splits positive at every DTE).
- **Gate 5 on the lower bound: 45-DTE beats 290** on mean P/L-per-debit (15% wing: +0.061 vs +0.051; gap widens annualized since 45d recycles ~8×/yr vs ~1.3×). BUT held-to-expiry structurally understates long-dated: payoff caps at the wings, so the 59.5% avg move at 290 DTE is "wasted" at expiry though it spikes the mark mid-cycle. Verdict DEFERRED to managed pass.
- **Mania sub-cohort (NVDA/PLTR/AMD/AVGO/TSLA) @10% wing:** win% climbs 67%(45d)→82%(290d), mean|move| 16.8%→59.5%. Best long-dated behavior; capped payoff is the constraint managed exits should relieve.

### Study A, Gates 2–5 — MANAGED / 50%-only pass: **COMPLETE — Gate 5 FAILED for long-dated (2026-06-03)**
`scripts/backtest/longdated_if_managed.py` → `data/profile/longdated_if_managed.parquet` (41,768 cycles). Use the **50%-only** policy (validated; the "full managed" stop+60-DTE-time-stop is degenerate for short DTE and negative everywhere — replicates prior IF "stops hurt").
- **Gates 2–4 PASS:** IF positive + walk-forward stable (≥3/4, mostly 4/4) at 10–15% wings, all DTEs, on 50%-only.
- **Mechanism confirmed:** at 290 DTE 50%-only (+0.039) ≈ 2× held-to-expiry (+0.021) — managed exits do capture the mid-cycle spike the lower bound wastes.
- **Gate 5 FAILS:** per-cycle 290≈45 (45 ahead at 15%: +0.056 vs +0.046); **annualized 45-DTE wins 6–8×** (45 recycles ~5.6×/yr vs ~0.9×). 15% wing: 45 +0.311/yr vs 290 +0.040/yr. Long-dated does NOT beat short-dated on return-on-capital.

### Study A — VERDICT (2026-06-03)
Long-dated IF is **viable but not capital-efficient vs 45-DTE.** The "long vs short maturity" question collapses to **"do we have an entry-timing signal?"**:
- **No signal →** long-dated is the rational expression (bounded cost, survives ~10mo to catch the move, no whipsaw) — i.e. Burry's timing-agnostic situation.
- **Good signal →** run **signal-gated 45-DTE IF** instead (capture +0.038–0.056/cycle without the ~6×/yr calm-regime bleed). Strictly better than long-dated.
→ **Study B is now the linchpin**, not a complement: it decides whether the recommendation is long-dated (fallback) or signal-gated short-dated (preferred). Wing 10–15% confirmed; stops OFF; 50%-only exit.
### Study B — OPTION-volume arm: **REJECTED (2026-06-03)**
`scripts/backtest/volume_signal_study.py` → `data/profile/volume_signal_results.parquet` (110,354 name-days).
- **Gate 1 (magnitude) FAIL:** declining-vol vs control move-lift is only +1.8–2.5pp (need ≥5pp). Even at the steeper 0.75 cutoff, peaks at +4.0pp (E1/25d) — sub-threshold.
- **Gate 2 (downside) FAIL:** P(ret<−10%) lift is −1.5 to +1.3pp (need ≥3pp); often NEGATIVE — declining option-vol does NOT predict more downside. Burry's "top and drop" is not corroborated by option volume.
- **Gate 5 (incremental):** declining cohort only +1.6–1.7pp over the plain-extension baseline — extension itself drives the move-propensity; volume adds almost nothing ("high move freq ≠ edge" confirmed).
- **Directionality verdict: neither gate passes → option-volume signal REJECTED.** A faint, sign-consistent magnitude tilt at 60d (WF 3/4) exists but is far too weak to gate trades.
- Caveat: this is OPTION volume; Burry's literal claim is EQUITY (share) volume — the equity arm (pending ingest) is the more faithful test and scored independently.

### Study B — EQUITY-volume arm: **REJECTED (2026-06-03)**
`scripts/research/ingest_equity_volume.py` (34 names, 114,730 daily-vol rows, yfinance) + `scripts/backtest/volume_signal_equity.py` → `volume_signal_equity_results.parquet` (106,982 name-days). Burry's *literal* share-volume tell — and it's **weaker than the option arm**:
- **Gate 1 (magnitude) FAIL:** lifts +1.0/+0.7/−2.0/+0.7pp across (E1/E2 × 25/60d) — need ≥5pp.
- **Gate 2 (downside) FAIL:** lifts +0.1/+1.3/−0.6/−2.2pp — need ≥3pp; several negative.
- **Both gates fail → equity-volume signal REJECTED.** Extension still dominates (baseline-increment +0.6–2.2pp).

### STUDY B — VERDICT: REJECTED (both arms)
Neither OPTION nor EQUITY volume decline is a tradeable predictor of forward move magnitude or downside on extended names in the IF universe. The faint, sign-consistent magnitude tilt (~+1–2.5pp at 60d) is real but sub-threshold and dominated by extension itself. **Burry's volume tell does not survive systematic, cohort-wide mechanical gating** — consistent with it being a discretionary synthesis signal (specific names + valuation + accounting + narrative), not a mechanical rule.

## THREAD CONCLUSION (2026-06-03) — CLOSED
- **Study A:** long-dated IF is viable (positive, WF-stable at 10–15% wings, 50%-only exit, stops off) but **not capital-efficient vs 45-DTE** (45 wins 6–8× annualized). Long-dated only rational for timing-agnostic conviction (Burry's situation).
- **Study B:** the candidate timing gate (volume decline) is **REJECTED by both measures.**
- **Net: no new tradeable signal. The book is unchanged.** The existing term-inversion IF gate remains the operative entry signal; the volume-decline hypothesis is closed both ways. Two clean negative results that prevent bolting a weak signal onto the system.
- **Reusable assets:** `longdated_if_feasibility.py` (DTE feasibility frontier — useful for any future long-dated structure question), `longdated_if_backtest.py` / `_managed.py` (IF engine across DTE/wing/exit), `equity_volume.parquet` (now on disk if revisited).
- **Mild positive aside:** EXTENSION itself (≥8% over 200-DMA) gives ~55%/73% chance of a >5% move in 25d/60d — re-confirms `project_200dma_ivhv_v1_findings`; volume adds ~nothing on top.

## Status
**SEALED 2026-06-03. CLOSED 2026-06-03.** Study A: long-dated not capital-efficient (45-DTE wins). Study B: volume signal REJECTED (both arms). Book unchanged; hypotheses closed.
