# H2 Weakness Gate Phase 2 — Pre-Registration

**Status: DRAFT awaiting user seal (2026-05-18).**
**Builds on:** `docs/H2_PREREG.md` (Phase 1, rejected on Gate C 2026-05-15) + `project_h2_phase1_rejected.md` (rejection diagnosis).
**Purpose:** Find a rotation-aware operational definition of "weak" that catches the live failure modes Phase 1 missed (WFC, KRE). Promote ONE definition as a bull_put exclusion gate if it passes the rigorous gates below. If none pass, the conceptual case survives but no integration occurs.

**Pairs with:** `docs/AUTO_PROMOTION_PIPELINE_PREREG.md` — pipeline decides cohort membership; H2 decides daily exclusion within the cohort. Orthogonal; either can ship without the other.

---

## 1. Conceptual basis (inherited from Phase 1)

H2 stays an **EXCLUSION FILTER**, not a short signal. Conceptual claim unchanged:

> Names matching the definition have elevated tail-risk relative to SPX, sufficient to justify excluding them from the bull_put cohort even when they otherwise pass per-name gates.

Phase 1's W3 (RS bottom 10% + below 200dma + ≥30% off 52w high) caught chronic distress (XLU-style) but missed rotation-driven weakness (WFC, KRE). Phase 2 tests 5 candidate definitions against the SAME pre-reg discipline used in Phase 1.

**Out-of-scope** (do not test, do not retrofit):
- Short-the-weak generation (D&M momentum-crash logic forbids it)
- Per-position management cues (close/roll based on weakness signal) — separate study
- Application to non-bull_put structures (bear_call, IF, ZEBRA, anti-ZEBRA) — separate per-structure analysis if pursued

---

## 2. Five candidate operational definitions (sealed)

### R1 — Rotation 60d
A symbol matches **R1** on date D if BOTH:
- 60-day return rank ≤ **0.20** within the 326-name (will become 5,500+ post-pipeline) cross-section
- 60-day return < SPY 60-day return by ≥ **10pp**

Captures recent capital flow flip without requiring chronic distress.

### R2 — Sector-relative weakness 60d
A symbol matches **R2** on date D if BOTH:
- 60-day return < own GICS sector ETF's 60-day return by ≥ **8pp**
- Symbol's GICS sector tag in `lib/sector_map.py` is not `_ETF` or `_UNKNOWN`

(Sector ETF mapping: financials → XLF, energy → XLE, utilities → XLU, tech → XLK, etc. List in §3.)

Catches names lagging their own sector — more granular than universe rank. Names with missing sector tags are excluded from R2 evaluation (no gate fires).

### R3 — Stage-2 momentum break
A symbol matches **R3** on date D if BOTH:
- Close price was **above** its 200-DMA on date `D - 30 trading days`
- Close price is **below** its 200-DMA on date D

Catches the moment of trend break. Pure Weinstein Stage-2 → Stage-3 transition.

### R4 — Compound W3 ∪ R3
A symbol matches **R4** on date D if EITHER:
- W3 fires (Phase 1 definition; chronic distress)
- OR R3 fires (Stage-2 break; rotation)

Pre-registered as a *test of compound* per Phase 1's "simplest-rule-that-passes wins" principle. R4 only promotes if no single-condition rule (R1/R2/R3) passes alone.

### R5 — Sector-load (cohort-level, not per-name)
A bull_put GO verdict is **BLOCKED** for sector S on date D if:
- ≥ **40%** of sector ETF S's tracked constituents in the broader universe ALSO match R1, R2, or R3 on date D
- AND total tracked constituents per sector ≥ 8 (require min N to evaluate)

This is a different shape from R1-R4: it gates COHORT verdicts at the sector level, not individual names. A healthy name in a broken sector gets the bull_put blocked even though the name itself doesn't match weakness. R5 only promotes if at least one of R1-R4 also promotes (compound gating requires a per-name signal as the base).

---

## 3. Sector ETF mapping (for R2 + R5)

| GICS Sector | ETF |
|---|---|
| Information Technology | XLK |
| Financials | XLF |
| Health Care | XLV |
| Communication Services | XLC |
| Consumer Discretionary | XLY |
| Consumer Staples | XLP |
| Energy | XLE |
| Industrials | XLI |
| Utilities | XLU |
| Materials | XLB |
| Real Estate | XLRE |

Sectors not in this table (or symbols flagged `_ETF` / `_UNKNOWN` in `lib/sector_map.py`) are excluded from R2/R5.

---

## 4. Live-failure event set (sealed)

The full universe of CLOSED LOSING TRADES on placed=1 positions as of 2026-05-18 — N=15:

```
XLE    stock              2026-04-07 → 2026-04-14  -$152
XOM    stock              2026-04-07 → 2026-04-14  -$564
GOOGL  bear_call          2026-04-16 → 2026-04-28  -$147
CSCO   bear_call          2026-04-16 → 2026-04-28  -$100
B      bull_put           2026-04-16 → 2026-04-28  -$53
NOC    inverted_fly       2026-04-17 → 2026-04-28  -$25
JPM    bear_call          2026-04-17 → 2026-05-01  -$2
KRE    bear_call          2026-04-16 → 2026-05-05  -$22
PFE    bull_put           2026-04-16 → 2026-05-05  -$18
CRM    bear_call          2026-04-17 → 2026-05-05  -$128
FCX    bull_put           2026-04-16 → 2026-05-06  -$204
XLU    bull_put           2026-04-17 → 2026-05-06  -$18
EEM    bear_call          2026-04-17 → 2026-05-07  -$23
MSFT   bear_call          2026-05-05 → 2026-05-07  -$115
WFC    bull_put           2026-05-05 → 2026-05-12  -$180
```

**Scope filtering for H2 validation:**
- H2 is a bull_put exclusion gate → only bull_put losers count in event set
- Stock + non-bull_put losers are CONTEXT but not the validation target

**Bull_put losers in scope:**
```
B      2026-04-16  -$53
PFE    2026-04-16  -$18
FCX    2026-04-16  -$204
XLU    2026-04-17  -$18
WFC    2026-05-05  -$180
```

N=5 bull_put losers. **This is the H2 Phase 2 live-failure event set.**

Worth noting: KRE was the famous live failure in Phase 1's Gate C, but KRE's losing trade was a bear_call (-$22), not a bull_put. KRE is therefore NOT in this scope. The Phase 1 rejection cited KRE as evidence W3 was mis-tuned — that rationale carries forward, but the validation target shifts to the bull_put-specific losers.

---

## 5. Bull_put winner event set (sealed)

The same event-set window (entries 2026-04-16 onward), closed bull_puts with `final_pnl > 0`. Counted for the false-positive-rate gate. As of 2026-05-18:

```sql
SELECT symbol, entry_date, exit_date, final_pnl FROM spread_score_trades
WHERE status='closed' AND placed=1 AND spread_type='bull_put' AND final_pnl > 0;
```

This list is computed at validation time, not enumerated in the pre-reg, because (a) it grows weekly and (b) the rule applies uniformly. Validation will read this set from the live ledger at evaluation date.

Estimated current N: ~20-25 bull_put winners (subject to verification at validation time).

---

## 6. Decision rule (sealed — applies to each of R1-R5 independently)

A candidate definition **PROMOTES** to live deployment as a bull_put exclusion gate IF AND ONLY IF all four of the following pass:

### Gate A — Pooled tail-risk asymmetry (carries forward from Phase 1)
- Definition-firing-day forward-75d crash rate (price return ≤ **-20%**) ≥ **2.0×** SPY baseline crash rate on the same dates
- Evaluated across the full 13-year ORATS history (or available history for sector ETFs)
- Match Phase 1's pooled-mean methodology exactly

### Gate B — Walk-forward stability
- Crash-rate ratio ≥ **1.5×** in ≥ **3 of 4** validation windows:
  - 2021-2023, 2022-2024, 2023-2025, 2024-2026
- ∞ ratios (SPY baseline = 0 in a window) count as PASS

### Gate C — Live-failure hit rate
- ≥ **60%** of the bull_put losers in §4 had the definition firing on their actual entry date
- N=5 → at least **3 of 5** must match (B, PFE, FCX, XLU, WFC)

### Gate D — False-positive rate
- ≤ **15%** of the bull_put winners in §5 had the definition firing on their actual entry date
- This is the cost-of-protection: a too-loose definition that fires on lots of winners is worse than no filter at all

**The rule sealed:** PROMOTE the simplest single-condition definition (R1, R2, R3) that passes ALL four gates. If multiple single-condition definitions pass, prefer the one with HIGHEST hit rate × (1 - false-positive rate). Compound (R4) and cohort-level (R5) only evaluated if NO single-condition definition passes (R1/R2/R3 all fail Gates A-D). This enforces the "simplest-rule-that-passes wins" principle.

---

## 7. What promotion looks like

If a definition (call it `RX`) passes:

1. `lib/h2_weakness.py` — add `is_weak_rx(symbol, asof_date)` reading from ORATS by-ticker parquets (and `data/orats/by_ticker/SPY.parquet` / sector-ETF parquets for the comparators)
2. `scripts/qualifier/gate_config.py` — add `H2_PHASE2_DEFINITION = "RX"` plus tunables for that definition's thresholds
3. `scripts/qualifier/cycle_qualifier.py` — bull_put / bull_put_earnings verdicts only: if RX matches at run_date, downgrade verdict to **SKIP_H2_WEAKNESS**
4. `scripts/monitor/daily_alert.py` — surface H2 status in any bull_put candidate card; SKIP_H2_WEAKNESS rows are already filtered out of the actionable set so the annotation is for PENDING rows

R5 (sector-load) promotion path differs: it modifies the qualifier's COHORT iteration to skip whole sectors when the cohort-level gate fires. Only attempted if R1-R4 all fail.

---

## 8. What we are NOT testing

- Whether H2 should expand to bear_call / IF / ZEBRA — would require independent per-structure event sets and validation (queued post-Phase-2 if R[X] promotes)
- Whether different RS lookback periods (30d, 90d, 120d) change R1's behavior — testing all permutations converts hypothesis-testing into hypothesis-searching (Phase 1 lesson)
- Whether sector ETF as the comparator is the right granularity for R2 (vs industry, factor, etc.) — same reason
- Whether the 75-day forward crash horizon (inherited from Phase 1) is the right window for rotation signals (could argue rotation plays out faster, ~30-45 days)

These are out-of-scope. Post-hoc tuning is forbidden.

---

## 9. Negative-result plan

If ALL of R1-R5 fail Gates A-D:
1. NO filter is promoted. The bull_put cohort remains un-H2-filtered.
2. A new memo records the rejection per-definition.
3. **The conceptual case** (tail-risk asymmetry → defensive filter) is now **rejected at the operational level for two consecutive phases** (Phase 1 W3 chronic; Phase 2 rotation variants). At that point the conclusion shifts: existing per-name gates + sector concentration cap + STP LMT GTC at 2× entry credit are the framework's tail-risk defenses, and no additional H2-style filter is justified by available data.
4. No Phase 3. The watchlist item is closed.

If exactly one of R1-R5 passes:
- Promote it per §7.

If multiple of R1-R5 pass:
- Promote the one with highest hit-rate × (1 - false-positive-rate) per §6.

---

## 10. Build artifacts (to be created post-seal)

- `scripts/backtest/h2_phase2_validation.py` — runs all 5 definitions through Gates A-D; outputs structured per-definition results
- `lib/h2_phase2_definitions.py` — pure-function definitions of R1-R5 (called by validation AND by live qualifier if any pass)
- `data/profile/h2_phase2_validation.parquet` — per-definition × per-gate results
- `reports/h2_phase2_validation_YYYY-MM-DD.md` — human-readable summary including the simplest-rule decision

If a definition promotes, additional:
- `lib/h2_weakness.py` updates (already exists from Phase 1 as a stub)
- `gate_config.py` constants
- `cycle_qualifier.py` verdict logic
- `daily_alert.py` annotation

---

## 11. Effort estimate

- Validation code (`h2_phase2_validation.py` + definitions): ~3-4 hours
- Sector ETF historical data check / fetch (XLF, XLE, etc. should already be in ORATS by_ticker — verify): ~30 min
- Run + interpret + write findings memo: ~1-2 hours
- Promotion integration (only if a definition passes): ~2-3 hours

Total if all 5 reject: ~5 hours. Total if one promotes: ~7-8 hours.

---

## 12. Sign-off

**Drafted by:** Claude Opus 4.7
**Drafted on:** 2026-05-18
**Sealed-by:** [pending user seal]
**Sealed-on:** [pending]

Once sealed, no validation code is written before the seal date. Build artifacts in §10 may be implemented after seal.

---

## 13. Cross-references

- `docs/H2_PREREG.md` — Phase 1 sealed pre-reg (rejected)
- `project_h2_phase1_rejected.md` — Phase 1 rejection diagnosis + 5 candidate definitions origin
- `docs/AUTO_PROMOTION_PIPELINE_PREREG.md` — companion pre-reg; pipeline + H2 are orthogonal
- `project_post_june_opex_watchlist.md` — item 11b (this work)
- `lib/sector_map.py` — sector mapping for R2/R5
- `data/orats/by_ticker/` — historical price data source
