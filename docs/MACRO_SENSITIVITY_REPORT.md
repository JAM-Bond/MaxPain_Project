# MaxPain Macro-Sensitivity Profile — Report

_Generated from the canonical `data/macro/` pipeline · betas as of 2026-06-02 · current-regime label `PLATEAU_CUTS` · 157 names fit, 157 in trading cohorts._

## 1. What this is

A measured, per-name profile of how each cohort stock responds to the macro environment — interest rates, curve slope, inflation expectations, the dollar, equity vol, oil, and credit. It replaces intuition ("banks like higher rates") with rolling sensitivities from 13 years of daily data, so cohort selection, sizing, and diversification can account for shared macro risk, not just sector labels. This is the live pipeline behind `lib/macro_profile.py`, the daily alert's MACRO CONCENTRATION block, and the qualifier's macro-concentration cap.

## 2. The seven macro factors

Each is a standardized daily *change*, so a beta reads as "daily return response, in basis points, per +1 standard-deviation move" — comparable across factors and market-controlled (sensitivity beyond plain market beta). The rate block is raw (Level = DGS10, Slope = T10Y2Y); there is no curve sub-PCA.

| Factor | Source series | Plain meaning | A **positive** beta means… |
|---|---|---|---|
| **Level** | DGS10 | 10-yr Treasury yield (overall rate level) | the stock rises when long rates rise |
| **Slope** | T10Y2Y | 10yr−2yr curve slope (steepness) | the stock rises when the curve steepens |
| **Inflation** | T10YIE | 10-yr breakeven inflation | the stock rises when inflation expectations rise |
| **Dollar** | DTWEXBGS | broad trade-weighted USD | the stock rises when the dollar strengthens |
| **VIX** | VIXCLS | equity-vol index (risk-off gauge) | the stock rises when volatility spikes |
| **Oil** | DCOILWTICO | WTI crude | the stock rises when oil rises |
| **Credit** | DBAA−DAAA | Moody's Baa−Aaa quality spread | the stock rises when credit spreads widen (risk-off) |

## 3. The three macro regimes

A principal-component analysis *across* the seven standardized factor changes finds the uncorrelated "regimes" the macro world actually moves in. Two names exposed to the same regime are correlated even if they sit in different GICS sectors — this is the axis the macro-concentration cap diversifies across.

- **PC1 (27.1% of factor variation) — +level +infl**  
  factor loadings: level +0.56, slope +0.43, infl +0.55, dollar -0.05, vix -0.33, oil +0.31, credit +0.00  
  cohort names that rise with it: APA, CNQ, DVN, FANG, TBT, USO  
  cohort names that fall with it: CVNA, IEF, NET, ROKU, TMF, TOL
- **PC2 (17.9% of factor variation) — +dollar +vix**  
  factor loadings: level +0.35, slope +0.34, infl -0.13, dollar +0.65, vix +0.47, oil -0.29, credit -0.11  
  cohort names that rise with it: ANET, KR, META, SCHW, TBT, UNH  
  cohort names that fall with it: AG, CNQ, GOLD, KGC, TMF, USO
- **PC3 (14.3% of factor variation) — +credit -oil**  
  factor loadings: level +0.05, slope +0.06, infl -0.04, dollar +0.07, vix -0.10, oil -0.21, credit +0.97  
  cohort names that rise with it: ALK, CMG, HWM, PLTR, TEVA, TJX  
  cohort names that fall with it: AA, APA, COP, NET, RRC, USO

_Top three axes capture 59% of cross-factor variation. Each name's `regime_primary` is its single dominant axis (signed); names with all-small loadings are NEUTRAL._

## 4. Which sensitivities to trust

A beta is only useful if it holds its sign across regimes. Re-estimating within five macro eras tags each (name, factor) beta **STABLE** (sign + magnitude hold), **MAGNITUDE_DEPENDENT** (sign holds, size varies — still directionally usable), or **SIGN_FLIP** (flips — do not trust). Small (immaterial) betas are excluded; we don't size off them anyway.

| Factor | Material betas | Sign holds (STABLE+MAG_DEP) | Sign flips |
|---|---|---|---|
| Market | 156 | 150 (96%) | 6 (4%) |
| Level | 86 | 38 (44%) | 48 (56%) |
| Inflation | 100 | 37 (37%) | 63 (63%) |
| Credit | 74 | 6 (8%) | 68 (92%) |
| Slope | 69 | 15 (22%) | 54 (78%) |
| Dollar | 0 (all betas immaterial) | — | — |
| VIX | 0 (all betas immaterial) | — | — |
| Oil | 0 (all betas immaterial) | — | — |

Plain reading: the **market beta** is the one robustly stable, always-material input (used for sizing). The macro-factor betas tell a humbler story — even when material, **Level, Inflation, Slope and especially Credit flip sign for the majority of names across eras**, so no single macro-factor beta is a trustworthy standalone rule. **Dollar / VIX / Oil** betas are too small to be material for the cohort at all. This is exactly why the system diversifies across the *combined* orthogonal regime tilt (`regime_primary`) and treats the macro profile as a risk/diversification descriptor — **not** a selection or sizing edge (consistent with the rejected regime-conditioning backtest). The handful of bedrock large-beta names — leveraged-Treasury ETFs on rates, gold/metal miners on the dollar — are the exceptions whose sign never moves.

## 5. Cohort macro archetypes

Cohort names grouped by `regime_primary` — their dominant orthogonal macro bucket. This is exactly the dimension the qualifier's macro-concentration cap diversifies across (soft-downsize beyond 3 per bucket per OpEx).

- **PC1+ — reflation / rates-up (energy, banks, cyclicals)** (39): ADM, APA, APO, AXP, BAC, BP, C, CAT, COF, COP, CVX, DVN, EOG, ETN, FANG, GE, GS, HWM, IBM, JPM, KRE, MPC, MS, MTZ, MU, NUE, PSX, PWR, RRC, RTX, SCHW, TBT, USB, USO, VLO, WFC, XLE, XLF, XOM
- **PC1- — anti-reflation / long-duration (mega-cap growth, defensives, gold-ish)** (32): AAPL, AMD, AMZN, CDNS, CMG, COST, CVNA, GNRC, GOOG, GOOGL, IEF, ISRG, LRCX, MRVL, MSFT, NEE, NET, NVDA, PEP, PG, QQQ, ROKU, TER, TMF, TOL, TSLA, TTD, TTWO, WMT, XLK, XLU, ZTS
- **PC2- — weak-dollar / risk-on (commodities, metals, EM)** (44): AA, AG, AZN, BABA, BHP, BX, CAR, CLF, CLS, CNQ, COHR, CRWD, EFA, EIX, EWY, EXPE, FCX, FSLR, GLD, GOLD, HYG, INTC, IWM, KGC, KKR, KO, LIN, LNG, LYV, NEM, NU, OKE, PDD, PM, RCL, RIO, RIOT, RKLB, SCCO, SE, SLV, TEVA, VST, WMB
- **PC2+ — strong-dollar / risk-off** (7): ANET, CNC, JNJ, KR, META, MRK, UNH
- **PC3+ — credit-stress** (10): ALK, AVGO, CSCO, DAL, DELL, MMM, PLTR, TJX, TMUS, UAL
- **PC3- — credit-easing / pro-oil** (3): AFRM, HOOD, SPOT
- **NEUTRAL — low orthogonal macro tilt (≈market-only)** (22): ADI, AMAT, CIEN, DIA, GLW, KEYS, MCD, NFLX, ORCL, RMBS, SBUX, SMH, SOXX, SPX, SPY, STX, STZ, TSEM, TXN, V, XLP, XSP

## 6. Diversification gaps (the actionable part)

Cross-sector cohort pairs whose regime-loading vectors point the same way — **the sector cap treats them as diversified, but they carry the same macro DNA.** These are the correlations the macro-concentration cap exists to catch.

- `+1.00`  **KKR** (financials) ≈ **RIO** (materials)
- `+1.00`  **AVGO** (information_technology) ≈ **DAL** (industrials)
- `+1.00`  **AVGO** (information_technology) ≈ **MMM** (industrials)
- `+1.00`  **SCCO** (materials) ≈ **VST** (utilities)
- `+0.99`  **EXPE** (consumer_discretionary) ≈ **VST** (utilities)
- `+0.99`  **CNQ** (energy) ≈ **FCX** (materials)
- `+0.99`  **CLF** (materials) ≈ **CNQ** (energy)
- `+0.99`  **ISRG** (health_care) ≈ **NVDA** (information_technology)
- `+0.99`  **FCX** (materials) ≈ **KKR** (financials)
- `+0.99`  **GE** (industrials) ≈ **NUE** (materials)

_Cosine on the top-3 regime axes (direction). The live cap uses the discrete `regime_primary` bucket; this table is the continuous view of the same structure._

## 7. How to use it

1. **Macro-concentration cap (LIVE):** the qualifier soft-downsizes names beyond 3 per `regime_primary` bucket per OpEx — diversifying across macro regimes on top of the GICS sector cap. Two PC1+ names in different sectors are still one reflation bet.
2. **Daily alert:** the MACRO CONCENTRATION block surfaces bucket clustering across open positions + candidates before each entry window.
3. **Sizing context:** market beta is the trustworthy sizing input; use the macro-factor betas directionally (via the regime bucket), not as hard coefficients.
4. **Post-mortem substrate:** when a position stops, read its bucket — "PC1-: a long-duration growth name hit as rates backed up" beats "tech rotated." (`report_macro_attribution` in the post-mortem.)

## 8. Methodology & caveats

- Returns are **split-adjusted** (raw ORATS stkPx is split-unadjusted, which would corrupt any 252-day window spanning a split); per-name obs with |daily log return| > 0.80 dropped as data artifacts.
- Betas are **rolling 252-day** multivariate OLS with SPY as a market control (so they are *partial* — incremental to market beta). Factors standardized; betas in bp per 1-SD move.
- Regime axes are a cross-factor PCA over the standardized factor changes; per-name loadings are **market-residual** (SPY regressed out first), so broad-market ETFs land NEUTRAL while sector ETFs keep their factor tilt.
- **Credit** = Moody's Baa−Aaa quality spread (ICE's HY-OAS series is licensing-truncated on FRED to 2023+).
- **Survivorship bias:** today's universe with full history. **Stress betas** (what matters most for risk) rest on rare regimes → smaller effective sample.
- **Correlation ≠ causation**, especially in cyclicals where a third factor may drive both the stock and the rate.
- A one-time FOMC event-study cross-validation (corr +0.45 with the daily Level betas, 31 decisions) was run during research and is preserved in the project memory; it is **not** part of this daily pipeline (only 11 cut events, several COVID-emergency → crash-inflated).

_Data: `data/macro/{regime_axes,regime_loadings,macro_profile,beta_stability_tags,beta_regime_summary,beta_rolling_252d}.parquet`. Refresh with `scripts/macro/daily_refresh.sh`, then regenerate this report with `python3.11 scripts/macro/generate_macro_report.py`._
