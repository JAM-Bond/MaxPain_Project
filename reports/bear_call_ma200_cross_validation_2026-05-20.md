# Bear-Call 200-DMA Cross-Exit — Validation Report (2026-05-20)

**Verdict: REJECT**

Pre-reg: `docs/BEAR_CALL_MA200_CROSS_EXIT_PREREG.md` (sealed 2026-05-20)
Simulation: `scripts/backtest/bear_call_ma200_cross_simulation.py`
Validation: `scripts/backtest/bear_call_ma200_cross_validation.py`

## Cohort

- Universe: OTM bear-call cycles entered below own 200-DMA: **15,269**
- Cross-exit fired: **4,791** (31.4%)
- Cross was the binding exit (fired earlier than mgd50): **3,954**

## Per-gate verdicts

| Gate | Threshold | Observed | Pass |
|---|---|---|---|
| A — improvement in firing cohort | ≥ +$0.2/sh | $-0.0062/sh | ✗ |
| B — no harm in non-firing cohort | ≥ $-0.05/sh | $+0.0000/sh | ✓ |
| C — walk-forward (≥3/4 windows) | ≥3 | 0/4 | ✗ |
| D — year-concentration cap | max ≤ 50% | 14.2% | ✓ |
| E — sample adequacy | ≥ 500 binding | 3954 | ✓ |

## Walk-forward windows (Gate C)

| Window | N firing | Improvement/sh | Pass |
|---|---|---|---|
| 2021-01-01..2023-12-31 | 1367 | $+0.0037 | ✗ |
| 2022-01-01..2024-12-31 | 1562 | $+0.0113 | ✗ |
| 2023-01-01..2025-12-31 | 1726 | $+0.0113 | ✗ |
| 2024-01-01..2026-12-31 | 1256 | $-0.0378 | ✗ |

## Interpretation

Gate(s) **A, C** failed. No promotion per pre-reg §8.

The vol-asymmetry hypothesis is empirically falsified. The bear-call cross-exit joins the 'real Stage-2 forensic signal, no tradeable mechanic' pile alongside the bull-put rejection (now 2-deep for MA-cross-exit).

Per §8: no immediate variant retest is permitted. A future variant pre-reg requires a fresh conceptual rationale, not a tweak.

## Cross-references

- `docs/BEAR_CALL_MA200_CROSS_EXIT_PREREG.md` — sealed pre-reg
- `data/profile/bear_call_ma200_cross_results.parquet` — per-cycle simulation
- `data/profile/bear_call_ma200_cross_validation.parquet` — per-gate output
- `scripts/backtest/bear_call_ma_cross_during_hold.py` — Phase 1 exploratory
- `project_bull_put_ma200_cross_rejected.md` — symmetric bull-put rejection