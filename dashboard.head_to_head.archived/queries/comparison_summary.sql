-- Head-to-head summary — one row per (symbol, opex_date), minimal columns.
-- Focused on the "who won?" question. Full-detail join lives in comparison.sql.

WITH aggr_orig AS (
    SELECT
        symbol,
        opex_date,
        GROUP_CONCAT(spread_type, ',') AS orig_types,
        SUM(final_pnl)                 AS orig_final_pnl,
        MAX(won)                       AS orig_any_won
    FROM spread_cycle_summary
    GROUP BY symbol, opex_date
),
aggr_score AS (
    SELECT
        symbol,
        opex_date,
        GROUP_CONCAT(spread_type, ',') AS score_types,
        SUM(final_pnl)                 AS score_final_pnl,
        MAX(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS score_any_open
    FROM spread_score_trades
    GROUP BY symbol, opex_date
),
universe AS (
    SELECT symbol, opex_date FROM aggr_orig
    UNION
    SELECT symbol, opex_date FROM aggr_score
)
SELECT
    u.symbol,
    u.opex_date,
    CASE WHEN o.symbol IS NOT NULL AND s.symbol IS NOT NULL THEN 1 ELSE 0 END AS both_covered,
    o.orig_types,
    o.orig_final_pnl,
    s.score_types,
    s.score_final_pnl,
    CASE
        WHEN o.symbol IS NULL AND s.symbol IS NULL            THEN 'unknown'
        WHEN o.symbol IS NULL                                 THEN 'only_score'
        WHEN s.symbol IS NULL                                 THEN 'only_original'
        WHEN COALESCE(s.score_any_open, 0) = 1                THEN 'pending'
        WHEN o.orig_final_pnl IS NULL OR s.score_final_pnl IS NULL THEN 'pending'
        WHEN ABS(COALESCE(o.orig_final_pnl, 0) - COALESCE(s.score_final_pnl, 0)) < 0.01 THEN 'tie'
        WHEN o.orig_final_pnl > s.score_final_pnl             THEN 'original'
        ELSE                                                       'score'
    END AS winner
FROM universe u
LEFT JOIN aggr_orig  o USING (symbol, opex_date)
LEFT JOIN aggr_score s USING (symbol, opex_date)
ORDER BY u.opex_date DESC, u.symbol;
