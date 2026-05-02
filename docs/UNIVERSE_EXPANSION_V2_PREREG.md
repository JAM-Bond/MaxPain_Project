# Universe Expansion v2 — Pre-Registration

**Status: SEALED 2026-05-02 by user (jmorris1950@yahoo.com).**
**Target completion: ~2026-05-30 (in time for JUL OpEx entry ~6/2 for 7/17 OpEx).**

This document pre-registers the methodology for expanding the deployable cohort from the current 163-ticker `by_ticker/` archive (sampled from a 5,748-ticker partitioned ORATS archive at `data/orats/parquet/`). Pre-registration must be sealed BEFORE any backtest code runs. Falsification criteria are binding.

Background motivation: the JUN OpEx cycle exposed a thin-cohort problem — only 1 of 10 GO names cleared the credit/width 0.50 floor. A larger validated cohort gives the qualifier more candidates to choose from each cycle, so even at low IV the book gets diversified across more positions (TastyTrade "trade small but trade often").

---

## 1. Candidate pool

**Source**: `~/MaxPain_Project/data/orats/parquet/year=YYYY/month=MM/YYYY-MM-DD.parquet` — daily files, 5,748 distinct tickers, ~850K rows/day, 41 columns.

**Excluded from candidate pool** (already in production):
- The 163 tickers currently in `by_ticker/`. These are already evaluated; this pass is for NEW names only.

**Effective candidate pool size**: ~5,585 names.

---

## 2. Liquidity gates (applied at the partitioned archive, before any extraction)

A candidate must pass ALL of these on a robust-aggregate basis. Aggregates computed by sampling 12 trading dates spread across 2024-Q1 through 2025-Q4 (one near the 15th of each month over the last 12 months) and averaging.

| Gate | Threshold | Rationale |
|---|---|---|
| Front-month total OI (call + put) | ≥ 10,000 contracts | Same as universe v1 |
| Average daily contract volume | ≥ 1,000 | Same as universe v1 |
| ATM bid-ask spread | ≤ 10% of mid | Same as universe v1 |
| Spot price | $5 ≤ spot ≤ $1,000 | Drop penny stocks (data quality) and ultra-priced names that violate per-position-size sanity |
| History coverage | ≥ 4 years (≥ 1,000 days in archive) | Same as universe v1 — needed for walk-forward train+val |
| Weekly expirations available | At least one weekly expiration in last 30 days | Same as universe v1 |

**Hard exclusions** (regardless of liquidity):
- Index symbols other than SPX (SPXW, RUT, NDX, VIX) — different option-chain mechanics
- Symbols with `^` or `.` characters (delisted / share class)
- Names with multiple known earnings whipsaws in 2024-2025 (manual exclusion list, TBD during scan)

**Expected pass rate**: 5-10% of candidates. So ~280-560 names should clear the liquidity floor.

---

## 3. Per-ticker walk-forward methodology

Same as the bull_put / bear_call / IF moneyness studies that built the current per-ticker recommendations (see `project_per_ticker_moneyness_studies.md`):

- Train: cycles with `entry_year ≤ 2022`
- Val: cycles with `entry_year ≥ 2023`
- Test statistic: paired Wilcoxon signed-rank between candidate exit-rule cells, p<0.05 BOTH halves, same direction
- N requirements: train N ≥ 22, val N ≥ 12

**Universe-level multiple-comparisons correction** (NEW for v2 — this is stricter than the 163-name studies used):

- **BH-FDR with q < 0.10** at the universe level. Applied to each structure independently. With ~280-560 candidates per structure, BH-FDR catches the inflated false-positive rate that unadjusted p<0.05 would leak.
- The walk-forward two-window discipline (train AND val both p<0.05) already provides one layer of out-of-sample protection. BH-FDR is the second layer at the universe scale.
- Survivors of the walk-forward two-window check are then ranked by combined train+val p-value, and BH-FDR cutoff is computed on that combined p-value series.

Source: standard practice for high-N hypothesis screening. Failure to apply correction at this scale would expect ~14-28 false positives per structure under unadjusted p<0.05.

---

## 4. Structures tested (Phase 2 scope)

| Structure | Spec | Source backtest script |
|---|---|---|
| bull_put 30Δ managed | OTM short put, exit at 50% credit or DTE≤21, slip=0.50 | `bull_put_moneyness_backtest.py` (extend universe) |
| bear_call 30Δ managed | OTM short call, exit at 50% credit or DTE≤21, slip=0.50 | `bear_call_moneyness_backtest.py` (extend universe) |
| inverted_fly medium_5pct | 45-DTE, 5%-of-spot wings, 50%-only exit, slip=0.50 | `inverted_fly_wing_backtest.py` (extend universe) |
| ZEBRA Tier 2 | 75-DTE, practitioner search at 0.55-0.90Δ, held to expiry | `zebra_universe_expansion_backtest.py` (extend universe) |

**NOT in scope** for Phase 2:
- Earnings-track structures — earnings cohorts curated separately by directional-bias scan, different methodology
- Covered call — strategy demoted (`project_covered_call_credit_etfs_findings.md`)
- Jade Lizard — failed defined-risk gate, rejected
- Per-ticker moneyness studies (OTM/ATM/ITM) — only run on existing 163; new candidates default to the universe-level recommendation (OTM 30Δ for verticals, medium_5pct for IF) until they accumulate enough data for per-ticker re-tuning. Quarterly cohort refresh (Phase 1) catches them on the next cycle.

---

## 5. Promotion criteria (per structure)

A candidate is **PROMOTED** to the deployable cohort if:

1. Liquidity gates pass (Section 2)
2. Walk-forward two-window p<0.05 same direction (Section 3)
3. BH-FDR q<0.10 cleared at universe level (Section 3)
4. Mean per-cycle P/L on val window ≥ + threshold per structure:
   - bull_put: val mean ≥ +$5/contract (slip=0.50)
   - bear_call: val mean ≥ +$5/contract (slip=0.50, conditional on H1 gate firing — separate analysis)
   - inverted_fly: val mean ≥ +$10/contract (slip=0.50)
   - ZEBRA: val median capture ratio ≥ 1.05 AND val mean ≥ baseline long-stock return × 1.10
5. Worst-case validation cycle ≤ 2× max-loss threshold (defined-risk only — sanity check)

**Survivor list output**: `data/profile/universe_expansion_v2_candidates.parquet` — columns: ticker, structure, train_n, train_p, val_n, val_p, val_mean, val_worst, bh_fdr_passed, promoted_flag.

---

## 6. Expected survivor count (sanity check)

Based on the 163-name study survival rates:
- bull_put: 36/162 ≈ 22% earn non-default per-ticker recommendations (but ALL 163 are in cohort). Universe-wide bull_put cohort survivors at q<0.10: estimate **30-80 names** out of ~450 candidates.
- bear_call: 27/162 ≈ 17% earn non-default. Survivors at q<0.10: estimate **20-60 names**.
- inverted_fly: 16/162 ≈ 10% earn validated non-default wing. Survivors at q<0.10: estimate **15-40 names**.
- ZEBRA: 16 names in the 13-name v1.5 source → cohort 13 promoted. Universe-wide survivors estimate **25-60 names**.

**Falsification trigger**: If any structure produces dramatically more survivors than the upper bound (e.g., bull_put returns 200+ promotions), methodology is leaking; STOP, investigate, do not promote any names.

**Falsification trigger 2**: If ALL structures produce zero survivors, the BH-FDR cutoff is too aggressive; tighten the methodology rather than relax the cutoff.

---

## 7. Promotion process (manual, human-reviewed)

This pre-reg does NOT auto-promote. Output is a candidate parquet for the user to review. The user manually edits `scripts/qualifier/gate_config.py` to add names to the relevant `COHORT_*` lists, with one batch promotion per structure logged in the trading plan revision log.

Each promoted name carries the train_n / train_p / val_n / val_p / val_mean as audit trail.

---

## 8. What can falsify the work

- **Survivor count out of range** (Section 6) — methodology leak
- **Forward-test under-performance** — promoted names underperform their val_mean prediction by >50% on the first 2 closed live cycles per structure → demote
- **JUL OpEx (first cycle that uses promoted names) shows worse aggregate book-level P/L than JUN** — though confounded by regime, a clear sign would be widespread val-validated names losing money under non-pathological regimes
- **Disk pressure** — if extracting 300-500 candidates exceeds 30 GB additional disk, abort and reconsider scope

---

## 9. Build artifacts (to be created)

- `scripts/maintenance/universe_v2_liquidity_scan.py` — Section 2 implementation
- `scripts/maintenance/universe_v2_extract_candidates.py` — extract liquidity-passing tickers to `by_ticker/`
- `scripts/maintenance/universe_v2_walkforward_orchestrator.py` — runs Section 4 backtests on candidates and applies Section 3 + 5 gates
- `data/profile/universe_v2_liquidity_pool.parquet` — output of Section 2
- `data/profile/universe_expansion_v2_candidates.parquet` — final output, Section 5

---

## 10. Sign-off

**Sealed by**: user (jmorris1950@yahoo.com).
**Sealed-on date**: 2026-05-02.
**Reviewed by AI**: Claude Opus 4.7 (1M context).
**Reference constraints**: `project_universe_expansion_v2_constraints.md` (memory).

After this doc is sealed, the user OR Claude can begin building the artifacts in Section 9. No analysis logic is written before seal.
