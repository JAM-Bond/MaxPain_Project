# Pre-Registration — Macro-State → GICS Sector Rotation

_Sealed 2026-06-04. Thresholds committed BEFORE looking at out-of-sample results._

## Question
Does the **state of the economy/market — as encapsulated in our macro metrics —
predict next-month GICS-sector-ETF behavior** (which sectors lead/lag)? This is
NOT relative-strength/momentum persistence (already rejected); it's macro-STATE
conditioning. "Money rotating out of one ETF into another" is measured as
**relative monthly returns** (we have no fund-flow/AUM data; relative return is
the tradeable proxy for rotation).

## Universe (13 ETFs)
11 SPDR GICS sectors: XLE XLF XLK XLV XLI XLP XLY XLU XLB XLRE XLC
+ commodity/safe-haven: **GLD, SLV**. Benchmark: SPY.
History: ORATS by_ticker, split-adjusted, monthly month-end, 2013-01→2026-05.
XLRE (from 2015-10) and XLC (from 2018-08) enter on inception; excluded from any
month before they exist.

## Frequency
Monthly, month-end. Daily is too noisy; the macro→rotation link is regime-scale.

## Target
For each ETF, **t+1 monthly EXCESS return vs SPY** (rotation = relative, not beta),
and its cross-sectional **rank** among the available ETFs that month.

## Macro feature panel (month-end t, all from data/macro/fred_daily_13y, 2013+)
- Rates: DGS10 level, Δ3m DGS10 (direction)
- Curve: T10Y2Y level, Δ3m
- Inflation exp: T10YIE level, Δ3m
- Realized inflation (YoY): CPIAUCSL (headline), CPILFESL (core), **CPIUFDSL (food)**
- Energy: DCOILWTICO 3m return (oil), **GASREGW 3m return (gasoline)**
- Dollar: DTWEXBGS Δ3m
- Vol: VIXCLS level
- Credit: (DBAA−DAAA) spread level + Δ3m
- Financial conditions: NFCI level
- Labor: UNRATE level, Sahm gap
- Recession lenses: Estrella–Mishkin probit (from DGS10−DTB3), near-term forward spread

## Two methods (both reported; the better judged against gates)
- **Method A — regime-conditional means (primary, interpretable).** Sealed regime
  scheme = 3 binary axes → 8 regimes:
    1. rate_dir   = sign(Δ3m DGS10)        (rates rising vs falling)
    2. infl_dir   = sign(Δ3m T10YIE)       (inflation accelerating vs not)
    3. risk       = VIXCLS above/below its trailing-12-month median (risk-off vs on)
  In each regime, the prediction = the TRAINING mean t+1 excess return per ETF;
  predicted ranking = those means. (~10 train months/regime — thin, exploratory.)
- **Method B — per-sector OLS (cross-check).** For each ETF, regress t+1 excess
  return on a sealed 6-feature subset: {Δ3m DGS10, Δ3m T10YIE, VIXCLS,
  Δ3m credit, Δ3m DTWEXBGS, oil 3m return}. Fit on train, predict OOS.

## Train / forward-test (your design)
- **Fit/estimate on ≤ 2019-12** (~84 months).
- **Forward-test OOS on 2020-01 → 2026-05** (~77 months). The holdout deliberately
  contains COVID, the 2022 rate shock, and the AI boom — a severe non-stationarity
  stress test. Breaking OOS is an informative result, not a failure.

## Sealed OOS gates
- **Gate A (ranking skill):** mean OOS Spearman corr(predicted ranking, realized
  ranking) **≥ +0.15**.
- **Gate B (top-pick skill):** OOS hit-rate of the top-predicted ETF beating SPY
  **≥ 55%**.
- **Gate C (spread):** OOS mean monthly (top-tercile − bottom-tercile predicted,
  realized excess) **≥ +0.30%/mo** AND positive in **≥ 55%** of OOS months.
- **Power:** ~77 OOS months is adequate → a null here is **terminal** (graveyard),
  not "re-run later."

## Verdict logic
- **PASS:** Gate A AND (Gate B or Gate C), on the better of the two methods.
- **NULL (terminal):** otherwise. Honest expectation given the non-stationary
  holdout + the macro-is-a-descriptor prior: a null is the base case.

## If PASS — use
Feeds the **macro positioning-risk overlay** (project_macro_positioning_overlay):
"in this macro regime, lean verticals toward favored sectors, away from
disfavored." Sector-level, evidence-based, soft — NOT a standalone rotation
strategy unless the OOS edge is large and stable.

## Honesty notes
- Selection-edge hunt → full skepticism (the ~12-deep graveyard). 
- Method A's 8-regime split is thin on ~84 train months; treat A as exploratory and
  lean on whichever method clears the sealed gates OOS.
- v2 refinements if v1 shows life: project onto our PCA regime axes (PC1/2/3)
  instead of the ad-hoc 8-regime scheme; extend history to ~1998 (rich LTCM/Asia/
  dot-com regime variety) — deferred per user until v1 results are seen.
