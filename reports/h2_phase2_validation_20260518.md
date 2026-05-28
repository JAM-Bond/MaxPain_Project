# H2 Phase 2 Validation — 2026-05-18

**Status: ALL DEFINITIONS REJECTED.** No filter promoted.

Pre-reg: `docs/H2_PHASE2_PREREG.md` (sealed earlier today by user).
Code: `scripts/backtest/h2_phase2_validation.py` + `lib/h2_phase2_definitions.py`.
Results parquet: `data/profile/h2_phase2_validation.parquet`.

## Headline

| Definition | Gate A (pooled ratio ≥2×) | Gate B (WF ≥3/4) | Gate C (≥3/5 losers) | Gate D (FPR ≤15%) | Overall |
|---|---|---|---|---|---|
| **R1** rotation 60d | ✓ 28.4× | ✓ 4/4 | ✗ 2/5 (B, WFC) | ✗ 18.8% | **FAIL** |
| **R2** sector-relative 60d | ✓ 28.6× | ✓ 4/4 | ✗ 2/5 (B, WFC) | ✗ 25.0% | **FAIL** |
| **R3** stage-2 break | ✓ 22.9× | ✓ 4/4 | ✗ 0/5 | ✓ 0.0% | **FAIL** |
| **R4** compound W3 ∪ R3 | ✓ 29.2× | ✓ 4/4 | ✗ 1/5 (XLU only) | ✓ 12.5% | **FAIL** |

R5 (sector-load): qualitative reporting only. Materials fires 1,371 days (41% of the 13-yr panel), communications 857, consumer staples 807 — these fire rates are too high for an exclusion gate to be operationally useful. Even granting R5 a hypothetical pass on Gates C+D, blocking ~41% of trading days for materials would suppress more bull_put activity than the gate's tail-risk reduction would justify. R5 effectively rejected by inspection.

## What the gates that passed tell us

Gate A pooled crash-rate ratios:
- SPY baseline crash rate (75d return ≤ −20%): **0.54%**
- R1 fire-day crash rate: **15.4%** → 28.4× SPY
- R2 fire-day crash rate: **15.5%** → 28.6× SPY
- R3 fire-day crash rate: **12.5%** → 22.9× SPY
- R4 fire-day crash rate: **15.9%** → 29.2× SPY

These are extreme asymmetries. The conceptual claim — "weak names crash more often" — is empirically validated even more strongly than Phase 1 (which produced 34.5× on W3 chronic distress). The signal is real. It's just not actionable for the specific failure modes in our live ledger.

Gate B was a clean sweep: 4/4 walk-forward windows passed for every definition. The signal is regime-robust across 2021-2026.

## Why Gate C failed — the live-failure event set is unfilterable by entry signals

Bull_put loser event set (N=5) and which definitions caught each:

| Loser | Entry | $ loss | R1 | R2 | R3 | R4 |
|---|---|---|---|---|---|---|
| B | 4/16 | −$53 | ✓ | ✓ | — | — |
| PFE | 4/16 | −$18 | — | — | — | — |
| FCX | 4/16 | −$204 | — | — | — | — |
| XLU | 4/17 | −$18 | — | — | — | ✓ (via W3 chronic distress) |
| WFC | 5/5 | −$180 | ✓ | ✓ | — | — |

R1 + R2 catch the same 2 names (B, WFC). R3 catches nothing — no stage-2 break fired at any of the 5 entry dates. R4 only adds XLU (via inherited W3) on top of R3's 0.

PFE and FCX are the unmissable killers. Both showed no pre-entry weakness signature; their losses materialized after entry from market-wide regime shift. No entry-time signal — chronic, rotation, sector-relative, or stage-break — fired on them.

Translated: the framework's bull_put losers are dominated by **post-entry deterioration**, not by **pre-entry weakness that any filter could have caught**. This is a structural feature of the failure mode, not a flaw in the candidate definitions.

## Why Gate D failed for R1 and R2

R1 false positives: 3 of 16 evaluable winners had R1 firing at entry → 18.8% FPR.
R2 false positives: 4 of 16 → 25.0% FPR.

The +60d-return-rotation signal fires on weak names that subsequently recover. That's the cost of looking at 60-day windows — many names that are temporarily weak heal within the 45-DTE bull_put cycle. R3 has 0% FPR because it requires a fresh trend break (rare); but at the cost of 0% hit rate.

No definition gives us the asymmetry needed: high enough hit rate to catch ≥3 of 5 losers without flagging too many winners.

## What this means

Per pre-reg §9 negative-result plan:

> If ALL of R1-R5 fail Gates A-D: NO filter is promoted. The bull_put cohort remains un-H2-filtered. The conceptual case (tail-risk asymmetry → defensive filter) is now rejected at the operational level for two consecutive phases. No Phase 3.

**Tail-risk defense for the bull_put cohort relies on existing mechanics:**
1. Per-name 200-DMA bucket DOWNSIZE rule (Rule #3, shipped) — catches BELOW_10PCT MA names
2. Sector concentration cap (max 2 per GICS sector, shipped)
3. STP LMT GTC at 2× entry credit (shipped) — caps realized loss per position
4. T-21 management cue (shipped) — universal time-exit for credit spreads

These four mechanics ARE the framework's tail-risk defense. H2 doesn't add on top of them.

## Reframe of the pooled finding

Although H2 doesn't promote as an entry filter, the 22-29× crash-rate asymmetry is the strongest single-factor result in the framework's research log alongside Phase 1's W3 34.5×. The signal is real. If a future structure or use-case opens up where the asymmetry IS actionable — e.g. a long-vol structure that PROFITS from crashes — these definitions could come back as TRIGGER signals (not filters). Out of scope today.

## What we are NOT doing

- Not back-fitting any of R1-R5 to catch PFE/FCX/XLU (would convert this from hypothesis-testing to hypothesis-searching — same lesson as Phase 1)
- Not relaxing Gates C or D (the pre-reg's whole point is to lock those bars BEFORE looking at results)
- Not building Phase 3 (the conceptual case is rejected at the operational level for two consecutive phases per pre-reg §9)

## How to apply

- If a future session brings up "let's tweak R1 to catch FCX too" — point at this report. The answer is: that's hypothesis-searching, forbidden by the discipline that lets us trust results
- If the question shifts from FILTER to TRIGGER (e.g., a bear-side structure that profits from the high crash rates) — that's a new pre-reg, new study, separate work
- The watchlist item "H2 Phase 2" is now closed

## Cross-references

- `docs/H2_PREREG.md` — Phase 1 sealed pre-reg (rejected on Gate C 2026-05-15)
- `docs/H2_PHASE2_PREREG.md` — Phase 2 sealed pre-reg (rejected on Gate C 2026-05-18)
- `project_h2_phase1_rejected.md` — Phase 1 rejection diagnosis
- `project_h2_weakness_research.md` — original conceptual basis (still valid)
- `lib/h2_phase2_definitions.py` — definitions library (preserved for potential trigger reuse)
- `scripts/backtest/h2_phase2_validation.py` — validation code
- `data/profile/h2_phase2_validation.parquet` — per-definition gate-pass parquet
