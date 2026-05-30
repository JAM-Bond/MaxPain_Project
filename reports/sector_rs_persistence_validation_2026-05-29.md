# Sector RS Persistence — Validation Report (2026-05-29)

**Verdict: NULL**

Sealed pre-reg: `docs/SECTOR_RS_PERSISTENCE_PREREG.md`. Split-clean closes via `lib.adjusted_close`. No option pricing.

- Sample dates: 161 | observations: 1595 | sectors: 11 | span 2013-08-06→2026-03-03


## Forward 45td relative return vs SPY, by trailing-RS tercile

| Tercile | mean fwd-RS | n |
|---|---|---|
| BOTTOM | -0.84% | 538 |
| MID | -0.44% | 453 |
| TOP | -0.46% | 604 |

**TOP − BOTTOM spread: +0.38%** (gradient non-monotonic)

Directional reliability — BOTTOM<0: 58% | TOP>0: 50% (mirror BOTTOM>0 42% | TOP<0 50%)


## Walk-forward (TOP−BOTTOM spread)

| Window | spread | min tercile n |
|---|---|---|
| 2021-2023 | +0.25% | 108 |
| 2022-2024 | +0.73% | 108 |
| 2023-2025 | -0.44% | 108 |
| 2024-2026 | -0.98% | 81 |

windows spread>0: 2/4 · spread<0: 2/4


## Gate scorecard

| Branch | A (spread) | B (monotonic) | C (reliability) | D (walk-fwd) | E (adequacy) |
|---|---|---|---|---|---|
| Persistence | False | False | False | False | True |
| Reversion | False | False | False | False | True |

## XLV as-of read (2026-05-28)

XLV tercile **BOTTOM** (rank 1/11, 1=weakest). verdict NULL → XLV's tercile (BOTTOM) carries no validated 45d signal; read stays discretionary.


Artifact: `data/profile/sector_rs_persistence.parquet` (per-observation date/sector/rs_trail/tercile/rs_fwd).
