# Bull-Put 200-DMA Cross-Exit — Validation Report (2026-05-19)

**Verdict: REJECT**

Pre-reg: `docs/BULL_PUT_MA200_CROSS_EXIT_PREREG.md` (sealed 2026-05-19)
Simulation: `scripts/backtest/bull_put_ma200_cross_simulation.py`
Validation: `scripts/backtest/bull_put_ma200_cross_validation.py`

## Cohort

- Universe: OTM bull-put cycles entered above own 200-DMA: **21,583**
- Cross-exit fired: **5,042** (23.4%)
- Cross was the binding exit (fired earlier than mgd50): **4,080**

## Per-gate verdicts

| Gate | Threshold | Observed | Pass |
|---|---|---|---|
| A — improvement in firing cohort | ≥ +$0.2/sh | $-0.2423/sh | ✗ |
| B — no harm in non-firing cohort | ≥ $-0.05/sh | $+0.0000/sh | ✓ |
| C — walk-forward (≥3/4 windows) | ≥3 | 0/4 | ✗ |
| D — year-concentration cap | max ≤ 50% | 13.5% | ✓ |
| E — sample adequacy | ≥ 500 binding | 4080 | ✓ |

## Walk-forward windows (Gate C)

| Window | N firing | Improvement/sh | Pass |
|---|---|---|---|
| 2021-01-01..2023-12-31 | 1612 | $-0.2133 | ✗ |
| 2022-01-01..2024-12-31 | 1624 | $-0.1346 | ✗ |
| 2023-01-01..2025-12-31 | 1576 | $-0.2167 | ✗ |
| 2024-01-01..2026-12-31 | 1232 | $-0.3295 | ✗ |

## Interpretation

Gate(s) **A, C** failed. No promotion per pre-reg §8.

Per §8: no immediate variant retest is permitted. A future variant pre-reg requires a fresh conceptual rationale, not a tweak.

## Cross-references

- `docs/BULL_PUT_MA200_CROSS_EXIT_PREREG.md` — sealed pre-reg
- `data/profile/bull_put_ma200_cross_results.parquet` — per-cycle simulation
- `data/profile/bull_put_ma200_cross_validation.parquet` — per-gate output
- `scripts/backtest/bull_put_ma_cross_during_hold.py` — Phase 1 exploratory