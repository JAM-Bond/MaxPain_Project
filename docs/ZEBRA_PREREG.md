# ZEBRA backtest — pre-registration (2026-04-25)

Set BEFORE any code runs. Sealed pre-registration for the ZEBRA (Zero
Extrinsic Back Ratio) study.

## Strategy spec

A ZEBRA is a 3-contract single-underlying structure designed to replicate
long stock exposure with negligible time decay. All three legs share the
same expiration.

- Buy 2× ITM call at ~70Δ
- Sell 1× ATM call at ~50Δ

Net debit at entry. Max loss = the debit (capped if stock crashes below
the long-call strikes). The "secret rule": **the extrinsic value collected
from selling the ATM short call must be ≥ the total extrinsic paid for
the two long ITM calls**, so net theta is zero or positive.

Greek profile (claimed):
- Net delta at entry: 2×0.70 − 0.50 = +0.90 (≈ 1 share equivalent)
- Net theta: ≈ 0 (the rule guarantees this)
- Net gamma: positive
- At expiry, terminal delta is +1.00 above K_50, +2.00 between K_70 and
  K_50, 0 below K_70.

The thesis is that ZEBRA is a stock-replacement vehicle: same upside
exposure as long stock, with capped downside (max loss = debit, typically
30-50% of stock cost), no time decay, and modest positive convexity from
the long ITM gamma.

## Hypotheses

This is a Greek-profile + capital-efficiency study, not a directional alpha
horse race. Three pre-registered hypotheses:

**H1 (extrinsic rule fires on tradeable cadence):** the literal 70/50 spec
satisfies the extrinsic-rule on ≥30% of cycles in the test universe. If
the literal spec fails this threshold, switch to the practitioner variant
("search for the ITM strike that satisfies the rule") and re-test the
fire rate of that variant. Failure of EITHER variant at 30% kills the
strategy as not-tradeable.

**H2 (theta neutrality is real):** measured daily MTM change on
"flat days" (days where the underlying moved <0.5%) has cohort-wide mean
≥ -$0.01 per contract per day. Loosely: the theta drag is empirically
within ~$1 over a 100-day hold per contract. Falsification: cohort flat-
day mean ≤ -$0.05/day per contract.

**H3 (stock replacement captures most of the upside):** for each cycle,
ZEBRA P&L at expiry / (long-stock P&L over the same period × 100) ≥ 0.85
on cycles where the stock finished above K_50 (the upside zone where the
strategy is supposed to track). Falsification: cohort median ratio < 0.70.

**H4 (capital efficiency):** net-debit-at-entry / (100 × spot_at_entry)
≤ 0.50 on average — i.e., ZEBRA costs at most half what 100 shares cost.
Falsification: cohort mean ratio > 0.65.

## Universe

The v1.5 deployable cohort, 37 names, where directional bullishness is
already empirically supported by the existing backtests:

- bull_put cohort: MSFT, TJX, WMT, QQQ, CNC, RIO, SPY, DAL, INTC, WFC,
  XLU, HYG (12)
- bear_call cohort: SPX, SPY, QQQ, DIA, IWM (regime-gated indices; 5)
- inverted_fly pair: SPX, SPY, QQQ, GLD, EFA, WMT, NEM, XOM, PG, WFC, GE,
  INTC, BABA (13)
- inverted_fly singles: TSLA, AMD, NVDA, CAR, AMZN, META, GOOGL, BABA,
  SCCO, GOLD, CLF (11)
- earnings T1: GOOGL, NUE, META, KO, WFC, RRC, SCCO, CNQ, INTC, PLTR (10)

Dedup → 37 unique. Read from
`data/profile/research_cohort_v15.parquet`. SPX excluded (Schwab
equity-chain limitations, but its backtest works on the ORATS data).

## Test matrix

- Entry DTE: 75 days before expiration (snap to nearest trading day on
  or before). Captures the "longer-dated stock replacement" use case.
- Expiration: monthly OpEx (third-Friday) anchor.
- Held to expiration. No managed exit (the structure is held for its
  long-stock-equivalent payoff at expiry, not for theta capture).
- Slip: 0.25 AND 0.50 on the option fills.
- Stock comparison: hypothetical 100-share long position on entry day,
  marked at expiration close. Same period, same name.

## Output target

Per-cycle scorecard at `data/profile/zebra_results.parquet`:
- ticker, expiration, entry_date, dte_at_entry
- long_strike (70Δ), short_strike (50Δ)
- entry_debit, capital_outlay (= 100 × spot_entry)
- entry_delta, entry_theta_bs, entry_extrinsic_long_total, entry_extrinsic_short
- spot_entry, spot_exit
- pnl_zebra (intrinsic at expiry − debit)
- pnl_stock (= (spot_exit − spot_entry) × 100)
- daily_flat_mean (mean MTM change on days where |return| < 0.5%)
- capture_ratio (pnl_zebra / pnl_stock when stock_pnl > 0)
- capital_efficiency (entry_debit / capital_outlay)

Aggregate scorecard at `data/profile/zebra_cohort_scorecard.parquet`:
- Cohort + per-ticker means for each H1-H4 metric, with falsification
  flag per row.

## Falsification criteria

Strategy stays parked if ANY of:

1. H1: extrinsic-rule fire rate < 30% on both literal and practitioner
   variants.
2. H2: cohort flat-day mean MTM change ≤ -$0.05/day per contract.
3. H3: cohort median upside-capture ratio < 0.70.
4. H4: cohort mean capital-outlay ratio > 0.65.

## Promotion criteria

For ZEBRA to be promoted into TRADING_PLAN.rtf:

1. H1: fire rate ≥ 50% on the test universe.
2. H2: flat-day mean ≥ -$0.01/day (claimed property essentially holds).
3. H3: cohort median capture ratio ≥ 0.85 on upside-finishing cycles.
4. H4: cohort mean capital-outlay ratio ≤ 0.50.
5. Walk-forward validates (train ≤2022, validate 2023-2026) on the top
   names.

If 3-4 of the above hold but not all, document as "informative but not
deployable" and skip the plan update. If 1-2 hold, document as null
result.

## Methodology discipline

- Pre-registered (this doc, sealed before code).
- ZEBRA is policy-compliant by construction (defined risk = debit).
- Theta computed via Black-Scholes from ORATS midIv at entry; daily
  flat-day MTM is the empirical complement.
- Slip 0.25 AND slip 0.50 reported.
- Output written even on null result.
- Findings memory written either way; nothing in TRADING_PLAN.rtf unless
  ALL promotion criteria hold.
