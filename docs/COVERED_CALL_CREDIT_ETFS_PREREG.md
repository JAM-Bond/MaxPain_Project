# Covered-Call on Credit ETFs — Pre-Registration

**Sealed: 2026-04-30 before any code runs.**

This pre-registration tests whether a covered-call income strategy on
range-bound credit ETFs with wide options bid-ask can produce positive
risk-adjusted return that exceeds the bull_put credit-spread alternative.

The trigger observation: BKLN trades in a $20.50–$23.50 range for 10+ years
(structural floor from floating-rate senior loan mechanics + monthly coupon
resets), pays ~7% annualized distributions monthly, and has $1 strike spacing
with wide bid-ask on options. Bull_put credit verticals are uneconomic on
BKLN because round-trip slippage eats most of the credit. A covered call
held to expiry only pays the bid-ask cost ONCE (at the call sale, not the
round trip), so the wide-bid-ask drag is structurally lighter.

## Universe

- **BKLN** (wide bid-ask, $1 strikes, 7% yield) — primary subject
- **TLT** (wide bid-ask, varying yield) — secondary
- **JNK** (wide bid-ask, ~6% yield) — secondary
- **HYG** (tight bid-ask, ~6% yield) — control: HYG is in the bull_put
  cohort already, so its covered-call result vs bull_put result is the
  cleanest comparison of execution-friction sensitivity.

## Data

- ORATS `data/orats/by_ticker/{TICKER}.parquet`, 2013-01 → 2026-04
- yfinance dividend history per ticker for cash-dividend collection
- `lib.opex_calendar` for monthly OpEx Fridays

## Cycle definition

- **Entry**: close on the trading day after prior monthly OpEx
- **Expiry**: next monthly OpEx (3rd Friday)
- **Window length**: ~21 trading days
- ~155 cycles per ticker over 13 years

## Structure

- Long 100 shares (per contract baseline)
- Short 1 call at 30Δ, expiring at next monthly OpEx
- Held to expiry; no managed exit, no rolling

## Per-cycle P&L (per share, ÷100 from the contract level)

```
P&L = (min(S₁, K) − S₀) + premium_collected + dividends_in_[entry, expiry)
```

Where:
- `S₀` = stock close at entry
- `S₁` = stock close at expiry
- `K` = short call strike (30Δ closest)
- `premium_collected` = (cBidPx + cAskPx) / 2 − slip
- `dividends_in_[entry, expiry)` = sum of cash distributions with ex-div
  date in the cycle window

If `S₁ ≥ K`: stock called away at K, capture premium + dividends.
If `S₁ < K`: keep stock at `S₁`, capture premium + dividends.

## Frictions tested

- **slip = 0.05** (typical with patient limit-order discipline; bid-ask
  midpoint as realistic fill on a single sale)
- **slip = 0.10** (pessimistic; takes 2× the realistic slip)

## Comparison baseline: same-name bull_put 30Δ at slip=0.10

For each ticker, simulate a 30Δ short put / 0.5%-of-spot wing (with $1
floor) credit vertical at slip=0.10, held to expiry. Same cycle definition.
Mean per-cycle P&L scaled to comparable capital outlay so we can compare:

- **CC capital**: 100 × S₀ per contract (fully funded)
- **bull_put capital**: max loss = wing − credit, per contract (small)
- **Capital-adjusted return**: `mean_pnl_per_cycle / capital × 12 cycles/year`

## Hypothesis (H1 — primary)

For each of {BKLN, TLT, JNK}, the covered-call strategy at slip=0.05
produces **positive mean per-cycle P&L** AND **higher capital-adjusted
annualized return than the same-name bull_put baseline at slip=0.10**.

## Hypothesis (H2 — control)

For HYG, the bull_put baseline at slip=0.10 should **beat or tie** the
covered-call strategy on a capital-adjusted basis. (Tight bid-ask on HYG
options means the wide-bid-ask thesis doesn't apply; the spread should
remain more capital-efficient.)

## Hypothesis (H3 — walk-forward)

H1 result must hold across all four 4-year sub-windows
(2013-2016, 2017-2020, 2021-2024, 2025-2026). Any window with negative
mean per-cycle P&L falsifies stability.

## Falsification

The strategy is dropped if any of:

- Mean per-cycle P&L ≤ 0 at slip=0.05 (universe of {BKLN, TLT, JNK}, mean
  across cohort)
- Capital-adjusted return below same-name bull_put baseline at slip=0.10
  for **all** of {BKLN, TLT, JNK} — the wide-bid-ask thesis isn't
  load-bearing
- Walk-forward shows any window with negative cohort mean P&L

## Caveats

- Single-cycle independence assumption: real-world holds the stock across
  cycles when not assigned. The per-cycle P&L approach correctly accounts
  for stock movement within each cycle, but assumes re-entry at S₁ at the
  start of the next cycle (true when assigned, equivalent when held).
- Ex-dividend mechanics on short ITM calls: real-world early-exercise
  risk on the day before ex-div. Backtest assumes European-style exercise
  (settlement at expiry). Selling 30Δ OTM calls keeps this concern small;
  the short call goes ITM only when stock rallies past strike, and
  early-exercise mostly matters for deep ITM calls right before ex-div.
- Single bad print observed on BKLN at $17.24 may be a data artifact
  (per user observation 2026-04-30). Findings should flag any cycle whose
  outcome is dominated by that single date.
- Dividend reinvestment NOT modeled — dividends are collected as cash,
  not compounded into share count.

## Output

`data/profile/covered_call_credit_etfs.parquet` (one row per cycle per
ticker per slip):
- columns: ticker, cycle_open, cycle_expiry, S0, S1, K, premium_mid,
  premium_collected_at_slip, dividends, assigned (bool), pnl_per_share,
  slip

Aggregate scorecard printed to stdout + saved to:
`data/profile/covered_call_credit_etfs_scorecard.parquet`
