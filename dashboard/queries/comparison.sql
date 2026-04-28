-- MaxPain head-to-head comparison query
-- Grain: one row per (symbol, opex_date)
-- Compares spread_cycle_summary (Original / Metal spread_evaluator)
--      vs spread_score_trades     (Score / independent line)
--
-- Units: entry_credit and final_pnl as stored in source tables. Both are
-- per-contract decimal dollars/share (multiply by 100 for $/contract).
-- Dashboard applies the multiplier once at display time.
--
-- Execute with pandas.read_sql_query against the live DB:
--   ~/Metal_Project/data/shared/metal_project.db (read-only during bake-off)

WITH aggr_orig AS (
    SELECT
        symbol,
        opex_date,
        GROUP_CONCAT(spread_type, ',')      AS orig_types,
        COUNT(*)                            AS orig_n_legs,
        MIN(tier)                           AS orig_tier,
        AVG(rank_score)                     AS orig_rank_score,
        SUM(entry_credit)                   AS orig_entry_credit,
        SUM(final_pnl)                      AS orig_final_pnl,
        AVG(final_pnl_pct)                  AS orig_final_pnl_pct,
        SUM(mc_expected_pnl)                AS orig_mc_expected_pnl,
        SUM(mc_max_loss)                    AS orig_mc_max_loss,
        MAX(won)                            AS orig_any_won,
        MIN(mark_date)                      AS orig_mark_date,
        MIN(liquidity_flag)                 AS orig_liquidity_flag
    FROM spread_cycle_summary
    GROUP BY symbol, opex_date
),
aggr_score AS (
    SELECT
        symbol,
        opex_date,
        GROUP_CONCAT(spread_type, ',')      AS score_types,
        COUNT(*)                            AS score_n_legs,
        MIN(tier)                           AS score_tier,
        AVG(rank_score)                     AS score_rank_score,
        SUM(entry_credit)                   AS score_entry_credit,
        SUM(width)                          AS score_total_width,
        SUM(final_pnl)                      AS score_final_pnl,
        AVG(entry_composite)                AS score_avg_composite,
        AVG(entry_iv_rank)                  AS score_avg_iv_rank,
        AVG(entry_vrp)                      AS score_avg_vrp,
        AVG(entry_short_delta)              AS score_avg_short_delta,
        MAX(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS score_any_open,
        MIN(entry_date)                     AS score_first_entry,
        MAX(COALESCE(target_hit_date, ''))  AS score_any_target_hit
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

    -- coverage flags
    CASE WHEN o.symbol IS NOT NULL THEN 1 ELSE 0 END AS in_original,
    CASE WHEN s.symbol IS NOT NULL THEN 1 ELSE 0 END AS in_score,
    CASE WHEN o.symbol IS NOT NULL AND s.symbol IS NOT NULL THEN 1 ELSE 0 END AS both_covered,
    CASE
        WHEN o.orig_types IS NOT NULL AND s.score_types IS NOT NULL
             AND o.orig_types = s.score_types THEN 1
        ELSE 0
    END AS structural_agree,

    -- original (Metal spread_evaluator)
    o.orig_types,
    o.orig_n_legs,
    o.orig_tier,
    o.orig_rank_score,
    o.orig_entry_credit,
    o.orig_final_pnl,
    o.orig_final_pnl_pct,
    o.orig_mc_expected_pnl,
    o.orig_mc_max_loss,
    o.orig_any_won,
    o.orig_mark_date,
    o.orig_liquidity_flag,

    -- score (independent line)
    s.score_types,
    s.score_n_legs,
    s.score_tier,
    s.score_rank_score,
    s.score_entry_credit,
    s.score_total_width,
    s.score_final_pnl,
    s.score_avg_composite,
    s.score_avg_iv_rank,
    s.score_avg_vrp,
    s.score_avg_short_delta,
    s.score_any_open,
    s.score_first_entry,
    CASE WHEN s.score_any_target_hit <> '' THEN 1 ELSE 0 END AS score_any_target_hit,

    -- winner classification
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
