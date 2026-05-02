# Bull Put Moneyness Backtest — Pre-Registration

**Sealed:** 2026-05-02
**Author:** Joseph Morris (with Claude Code)

## Question

Across the full ORATS-historical universe (163 tickers), is there a statistically significant P/L advantage to entering bull-put credit verticals at OTM (30Δ short), ATM (50Δ short), or ITM (70Δ short) moneyness?

## Hypothesis (broader than current TRADING_PLAN)

The current plan defaults to OTM short (30Δ, the conventional spec). This test asks whether moneyness alone — holding all else constant — is itself an edge variable, on a wider universe than the validated bull_put cohort. Three null hypotheses:

- **H₀-A:** OTM and ATM have equal mean P/L per cycle (paired)
- **H₀-B:** OTM and ITM have equal mean P/L per cycle (paired)
- **H₀-C:** ATM and ITM have equal mean P/L per cycle (paired)

Two-sided Wilcoxon signed-rank test on cycles where all three moneyness levels opened a position. α = 0.05, **Bonferroni-corrected to α' = 0.0167** for the 3 paired tests.

## Backtest design (locked)

| Parameter | Value |
|---|---|
| Universe | 163 ORATS-historical tickers (no cohort or gate filter) |
| Moneyness — OTM | short_put_delta target = -0.30 (call delta 0.70) |
| Moneyness — ATM | short_put_delta target = -0.50 (call delta 0.50) |
| Moneyness — ITM | short_put_delta target = -0.70 (call delta 0.30) |
| Delta tolerance | ±0.05 (per existing `select_by_delta`) |
| Entry | 45-DTE on monthly OpEx (Window A) |
| Wing width | `_vertical_wing(spot)` — spot-scaled (0.50% of spot in v2 mode) |
| Pricing mode | `slip` with `slip_frac=0.50` (matches validated standard) |
| Exit rule A | held-to-expiry |
| Exit rule B | 50% managed: close on first daily mark where `mark_credit ≤ 0.5 × entry_credit` |
| Earnings filter | OFF (broader-picture run) |
| Time window | full ORATS history (2013-01-02 → 2026-05-01) |

## Promotion gates (per moneyness, per exit rule)

For each (moneyness, exit_rule) cell, report:
- N cycles
- Mean P/L per cycle
- Median P/L per cycle
- Win rate
- Worst 5% (left tail)
- Largest single-cycle loss
- Bootstrap 95% CI on mean (B=1000)

## Statistical tests (sealed before run)

For each exit_rule independently:
1. Paired Wilcoxon on (OTM vs ATM) per cycle — only cycles where both legs opened
2. Paired Wilcoxon on (OTM vs ITM)
3. Paired Wilcoxon on (ATM vs ITM)
4. Bonferroni: α' = 0.05 / 3 = 0.0167

**Decision rule:** any pair with p < 0.0167 is "significantly different at α = 0.05 family-wise." Sign of the median paired-difference indicates which direction.

Secondary: per-ticker scorecard — for each ticker, which moneyness wins by mean P/L, by win rate, by worst-5%. Aggregate distribution: how many tickers does each moneyness "win" on?

## Output artifacts

- `data/profile/bull_put_moneyness_results.parquet` — cycle-level (ticker × cycle × moneyness × exit_rule)
- `data/profile/bull_put_moneyness_scorecard.parquet` — per-cohort metrics + Wilcoxon p-values
- `data/profile/bull_put_moneyness_per_ticker.parquet` — per-ticker scorecard

## What this test cannot do

- Establish causal mechanism (moneyness × IV, moneyness × regime, etc.) — those are follow-on tests
- Predict live execution slippage (slip=0.50 assumed; ITM wide-spread names may execute worse)
- Recommend a single moneyness for the live book — passing significance at the universe level says something about the average ticker, not about whether you should change your specific cohort's spec

## What this test CAN do

- Establish whether moneyness is an edge variable at all (vs. random)
- Identify direction (does deeper moneyness improve or hurt mean P/L)
- Surface ticker-class effects (does ITM work on some clusters, hurt on others)
- Quantify the held-to-expiry vs. 50%-managed delta on this dimension
