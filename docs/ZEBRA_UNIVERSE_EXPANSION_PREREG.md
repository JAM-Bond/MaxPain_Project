# ZEBRA Universe Expansion — Pre-Registration

**Sealed:** 2026-05-01
**Author:** Joseph Morris (with Claude Code)
**Predecessor:** `ZEBRA_PREREG.md` (sealed 2026-04-25, promoted into TRADING_PLAN.rtf v1.6)

## Question

Does ZEBRA validate on a broader sub-$100 quality bull-mode universe outside the v1.5 cohort?

## Hypothesis

ZEBRA's edge — slight delta leverage at entry, positive gamma from ITM longs, theta neutrality — is universal to liquid bullish names. The original cohort selection bias (premium-selling cohort inherited unchanged) under-explored sub-$100 quality names. The bottom 8 of the original test (CAR, BABA, RRC, CNC, CLF, NUE, GOLD) all had `median_capture ≥ 1.0` — the mechanism worked, but the names didn't trend up. Therefore the binding constraint is "is this name in a sustained uptrend" not "is this a mega-cap."

## Universe seed

- 163 tickers with full ORATS history at `data/orats/by_ticker/`
- **Subtract:** current ZEBRA Tier 1 (SPY, QQQ, MSFT, NVDA, GOOGL, META, AMZN) + Tier 2 (DIA, IWM, GLD, TJX, GE, WMT, AMD, PLTR)
- Remaining candidate pool: ~148 tickers

If post-screen pool < 15 candidates, expand by ingesting more S&P 500 / Russell 1000 names into ORATS (separate work item).

## Hard pre-screen (gate before backtest, today's snapshot)

| # | Gate | Threshold | Source |
|---|---|---|---|
| 1 | Spot | $20 ≤ stkPx ≤ $100 | latest ORATS by_ticker stkPx |
| 2 | 20-day avg volume | > 1M shares | yfinance 1-year history |
| 3 | 200dma + 50dma | spot above both | yfinance |
| 4 | 6-month return | > 0% | yfinance |
| 5 | Strike density at 75-DTE | ≥ 5 strikes within ±15% of spot | latest ORATS chain |
| 6 | No earnings in next 75 days | clean window | yfinance calendar |
| 7 | ORATS coverage | ≥ 5 years of trade_date history | by_ticker parquet date range |

Names failing any hard gate are excluded. Output is the post-screen candidate list — printed for user review BEFORE the backtest run.

## Backtest run (post user-OK only)

- Engine: `scripts/backtest/zebra_backtest.py` unchanged
- 75-DTE entry, held to OpEx
- Slip: 0.50 (primary) and 0.25 (sensitivity)
- Output: cycle parquet + daily-MTM parquet, same schema as original

## Promotion gates (per-ticker, must pass all)

| Gate | Threshold | Source |
|---|---|---|
| H1 fire rate | ≥ 30% | original pre-reg |
| H2 flat-day MTM | ≥ -$0.01/day | original |
| H3 median capture (upside cycles) | ≥ 0.85 | original |
| H4 capital efficiency | ≤ 0.50 | original |
| Walk-forward (train ≤2022, val 2023-2026) | both windows positive AND capture ≥ 1.0 in both | original |
| Min train sample | N_train ≥ 22 cycles | original (PLTR floor) |
| Min total sample | N_total ≥ 50 cycles | NEW |

## Tier assignment + diversification

- **Tier 2 expansion** = any sub-$100 name passing all promotion gates
- **Sector cap:** no single GICS sector > 25% of final Tier 2 (cap applied at promotion stage, not screen stage). Sector source: yfinance `Ticker.info["sector"]`.
- **Tier 1 unchanged.** No reshuffling of the existing 7-name Tier 1.

## User decisions (2026-05-01)

- Universe-print checkpoint before backtest run: **YES**
- Sector cap for Tier 2 diversification: **YES, 25% per GICS sector**
- Walk-forward N_train floor: **same as original (≥ 22)**

## Curation after universe-print checkpoint (2026-05-01)

Screen produced 12 candidates → 11 after Schwab live-spot re-check (RIO dropped at $100.32). User curated to **7 names** for backtest:

**INCLUDED (7):** HAL, SLB, STM, KO, INTC, XLE, KRE

**EXCLUDED with reasons:**
- RIO — Schwab live spot $100.32 fails $100 budget gate (was passing on stale ORATS spot)
- KDP — marginal trend (spot $29.02 vs ma200 $28.51 = 1.8% above; 6mo return only +8%)
- EWG — marginal trend (spot $42.40 vs ma200 $41.84 = 1.3% above; 6mo return +2.4%)
- EEM — concentrated geographic bet (emerging markets, dropped per user judgment)
- EWZ — concentrated geographic bet (Brazil, dropped per user judgment)

User accepted INTC at the budget edge ($99.12 live spot, 5 strikes in band — both at the floors).

Backtest cohort is locked: **HAL, SLB, STM, KO, INTC, XLE, KRE**.

## Results (2026-05-01, slip=0.50, held to OpEx)

1538 cycle rows across 7 tickers. Per-ticker scorecard at slip=0.50:

| ticker | N   | win% | mean_zebra | capture | cap_eff | WF train | WF val  | verdict |
|--------|-----|------|------------|---------|---------|----------|---------|---------|
| KRE    | 96  | 60%  | +$1.10     | 1.13    | 0.20    | +$0.70   | +$1.91  | ✓ PROMOTED |
| STM    | 27  | 63%  | +$2.25     | 1.18    | 0.29    | +$1.75   | +$2.70  | ✗ N=27<50 (watchlist) |
| INTC   | 153 | 55%  | +$0.50     | 1.14    | 0.20    | -$0.14   | +$2.57  | ✗ WF train negative |
| HAL    | 144 | 58%  | +$0.19     | 1.18    | 0.25    | +$0.38   | -$0.42  | ✗ WF val negative |
| KO     | 129 | 55%  | -$0.04     | 1.18    | 0.14    | -$0.07   | +$0.06  | ✗ WF mean ~0 |
| XLE    | 122 | 52%  | -$0.25     | 1.12    | 0.17    | -$0.10   | -$0.62  | ✗ WF both negative |
| SLB    | 96  | 53%  | -$0.72     | 1.27    | 0.26    | -$0.44   | -$1.66  | ✗ WF both negative |

### Promotion: KRE only

Added to `COHORT_ZEBRA_TIER2` in `scripts/qualifier/gate_config.py` 2026-05-01. No sector cap binds (single promotion). Qualifier will pick KRE up on its next 9:25 ET run.

### Watchlist: STM

Strong signals (mean +$2.25, walk-forward positive in both windows) but only 27 cycles available — post-2014 IPO history is too thin to clear N≥50. Re-evaluate when N reaches 50 (estimated late 2026 / early 2027).

### Lesson learned: bull-trend at the screen moment is necessary but not sufficient

Walk-forward was the binding constraint on 5 of 7 names. The hypothesis "ZEBRA's edge is universal to liquid bullish names" needed a stronger persistence condition. Energy-sector names (HAL, SLB, XLE) all rejected because the 2020-2022 commodity boom did not persist into 2023-2026. KO had no convexity (defensive, low capture). INTC was train-negative / val-explosive — regime-conditional, not stable.

**Implication for future screens:** add a "persistence proxy" filter — e.g. require positive return in BOTH the most recent 3 years AND the prior 3 years, not just the recent 6 months. Energy names would have been screened out pre-test under that rule.

## Outputs

- `docs/ZEBRA_UNIVERSE_EXPANSION_PREREG.md` (this file, sealed pre-run)
- `data/profile/zebra_universe_expansion_candidates.parquet` (post-screen, pre-backtest)
- `data/profile/zebra_universe_expansion_results.parquet` (post-backtest)
- `data/profile/zebra_universe_expansion_walkforward.parquet`
- `data/profile/zebra_universe_expansion_promoted.parquet` (final Tier 2 expansion list, sector-cap applied)
