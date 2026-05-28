# Sector-ETF Stage-2 Bear-Call — Validation Report (2026-05-18)

**Verdict: REJECT**

Pre-reg: `docs/SECTOR_ETF_STAGE2_BEARCALL_PREREG.md` (sealed 2026-05-18)
Validation code: `scripts/backtest/sector_etf_bearcall_stage2_validation.py`

## Headline

- Baseline: N=1,335 sector-ETF OTM bear-call cycles · mean $-0.1339/sh · win 0.745
- Trigger cycles (Stage-2 active at entry): N=140 · mean $-0.0501/sh · win 0.779

## Gate verdicts

| Gate | Threshold | Observed | Pass |
|---|---|---|---|
| A — pooled mean ≥ +$0.02/sh | +$0.02 | $-0.0501 (N=140) | ✗ |
| B — win rate ≥ 78% | 78% | 0.779 (N=140) | ✗ |
| C — ≥6/12 sectors positive + worst ≥ -$0.10 | ≥6 + worst ≥ -$0.10 | 5 positive of 12 with cycles · worst = $-0.4688 | ✗ |
| D — walk-forward mean >0 in ≥3/4 windows | ≥3 windows | 0/4 windows | ✗ |
| E — ≥100 trigger cycles | 100 | 140 | ✓ |

## Per-sector trigger cells

| Sector | Trigger N | Mean per-share | Notes |
|---|---|---|---|
| XLB | 10 | $-0.4688 |  |
| XLC | 5 | $-0.0115 |  |
| XLE | 11 | $-0.0366 |  |
| XLF | 6 | $+0.2283 |  |
| XLI | 12 | $-0.1073 |  |
| XLK | 8 | $+0.0894 |  |
| XLP | 17 | $-0.2069 |  |
| XLU | 19 | $+0.0592 |  |
| XLV | 16 | $-0.1161 |  |
| XLY | 13 | $-0.1777 |  |
| IYR | 13 | $+0.2062 |  |
| SMH | 10 | $+0.1223 |  |

## Walk-forward windows (Gate D)

| Window | Cycles | Mean | Pass |
|---|---|---|---|
| 2021-01-01..2023-12-31 | 34 | $-0.0566 | ✗ |
| 2022-01-01..2024-12-31 | 35 | $-0.0497 | ✗ |
| 2023-01-01..2025-12-31 | 41 | $-0.0685 | ✗ |
| 2024-01-01..2026-12-31 | 27 | $-0.0288 | ✗ |

## Interpretation

Gate(s) **A, B, C, D** failed. No promotion per pre-reg §9.

Per §9: no immediate variant retest is permitted. A future variant pre-reg requires a distinct conceptual rationale + sealing BEFORE looking at variant results.

## Cross-references

- `docs/SECTOR_ETF_STAGE2_BEARCALL_PREREG.md` — sealed pre-reg
- `lib/sector_etf_stage2.py` — signal definition
- `data/profile/sector_etf_bearcall_stage2_validation.parquet` — per-gate parquet
- `data/profile/bear_call_moneyness_results.parquet` — cycle-level input
- `project_sector_etf_stage2_bearcall_prereg.md` — memory (pre-seal)