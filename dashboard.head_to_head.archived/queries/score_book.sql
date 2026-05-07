-- Score book — spread_score_tracker (independent line) trades.
-- Grain: one row per trade (spread_score_trades.id).
-- Open trades have NULL exit_* and final_pnl; mtm_* columns carry the latest
-- daily mark from spread_score_daily so the UI can show current P&L.

WITH latest_mark AS (
    SELECT trade_id, mark_date, mark_credit, unrealized_pnl, pnl_pct
    FROM (
        SELECT
            trade_id, mark_date, mark_credit, unrealized_pnl, pnl_pct,
            ROW_NUMBER() OVER (PARTITION BY trade_id ORDER BY mark_date DESC) AS rn
        FROM spread_score_daily
    )
    WHERE rn = 1
)
SELECT
    t.symbol,
    t.opex_date,
    t.spread_type,
    t.short_strike,
    t.long_strike,
    t.width,
    t.status,
    t.tier,
    t.rank_score,
    t.entry_date,
    t.entry_price,
    t.entry_credit,
    t.entry_composite,
    t.entry_iv_rank,
    t.entry_vrp,
    t.entry_short_delta,
    t.entry_charm_sign,
    t.entry_vix,
    t.exit_date,
    t.exit_credit,
    t.final_pnl,
    CASE WHEN t.target_hit_date IS NOT NULL THEN 1 ELSE 0 END AS target_hit,
    t.target_hit_pnl,
    t.target_hit_days_held,
    m.mark_date     AS mtm_date,
    m.mark_credit   AS mtm_credit,
    m.unrealized_pnl AS mtm_pnl,
    m.pnl_pct       AS mtm_pnl_pct
FROM spread_score_trades t
LEFT JOIN latest_mark m ON m.trade_id = t.id
ORDER BY t.opex_date DESC, t.symbol, t.spread_type, t.entry_date;
