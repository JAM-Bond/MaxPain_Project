-- Original book — Metal spread_evaluator closed outcomes.
-- Grain: one row per (symbol, opex_date, spread_type) leg.
-- Source: spread_cycle_summary (only populated after `close_spread_cycle()`).

SELECT
    symbol,
    opex_date,
    spread_type,
    short_strike,
    long_strike,
    width,
    tier,
    rank_score,
    entry_credit,
    final_mark,
    final_pnl,
    final_pnl_pct,
    won,
    mc_prob_profit,
    mc_expected_pnl,
    mc_sharpe,
    mc_max_loss,
    liquidity_flag,
    strike_spacing,
    mark_date
FROM spread_cycle_summary
ORDER BY opex_date DESC, symbol, spread_type;
