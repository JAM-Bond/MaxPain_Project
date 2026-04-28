# Symbol Profile Builder

Computes per-ticker behavioral features from ORATS near-EOD parquet. Two stages:

1. **daily** — for each trading day, produce a small per-ticker summary (~4,000 rows/day vs. millions of contract rows). Idempotent: existing summaries are skipped.
2. **profile** — aggregate daily summaries into per-ticker profile (medians, ranges, realized vol, coverage).

## Feature families (Phase 1 — MVP)

- **Liquidity / structure:** `n_contracts`, `n_expirations`, `total_oi`, `total_volume`, `has_weekly_frac`
- **Vol personality:** `median_atm_iv`, `p10_atm_iv`, `p90_atm_iv`, `iv_regime_range`, `median_iv_skew_10d`, `realized_vol_annualized`
- **Coverage:** `history_days`, `first_date`, `last_date`, `median_stk_px`

Phase 2 additions (later): pin rate, MP drift, structure-fit P&L distributions, SPY beta.

## Run

Single-month prototype (fastest sanity check):
```
python3.11 build.py daily --year 2020 --month 3
python3.11 build.py profile
```

Full year:
```
python3.11 build.py all --year 2020
```

Multi-year (run daily for each, then profile once):
```
python3.11 build.py daily --year 2018
python3.11 build.py daily --year 2020
python3.11 build.py daily --year 2022
python3.11 build.py daily --year 2024
python3.11 build.py profile
```

## Output

- `data/profile/daily_summary/{YYYY-MM-DD}.parquet` — one file per trading day, ~4K rows
- `data/profile/profile_v1.parquet` — one row per ticker, ~15 feature columns

## Extending

Add a new feature: define a function in `features.py` taking a single (ticker, day) DataFrame slice and returning a scalar, then add it to the `FEATURES` dict. It propagates through automatically.

Profile-stage aggregates: edit `stage_profile()` in `build.py` to add a new derived column (e.g., `iv_rank_current` would need a different aggregation than median).
