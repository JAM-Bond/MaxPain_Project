#!/bin/bash
# Daily macro-sensitivity profile refresh.
#
# Refreshes the 4-step pipeline so the daily alert + post-mortem read
# current betas/tags/profile:
#   1. FRED full-history backfill (29 series, ~5s)
#   2. Prices spine from ORATS by_ticker (cohort union ~162, ~15s)
#   3. FRED × prices wide join (~5s)
#   4. 252d rolling β + regime stability tags + per-name profile (~30s)
#
# 63d rolling β is NOT refreshed daily — secondary artifact for stress
# spot-checks, re-run on demand. Total runtime ~60s.
#
# Schedule: ~19:30 ET weekdays (chains after the 19:00 ORATS pipeline).
# Idempotent — safe to re-run.
set -euo pipefail

cd "$HOME/MaxPain_Project"
PYTHON=/opt/homebrew/bin/python3.11

echo "=========================================================="
echo "Macro-sensitivity refresh — $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================================="

echo ""
echo "[1/5] build_fred_daily.py"
$PYTHON scripts/macro/build_fred_daily.py

echo ""
echo "[2/5] build_prices_daily.py"
$PYTHON scripts/macro/build_prices_daily.py

echo ""
echo "[3/5] build_macro_join.py"
$PYTHON scripts/macro/build_macro_join.py

echo ""
echo "[4/5] build_betas_rolling.py (252d)"
$PYTHON scripts/macro/build_betas_rolling.py --window 252

echo ""
echo "[5/6] build_beta_stability.py + build_regime_axes.py"
$PYTHON scripts/macro/build_beta_stability.py
$PYTHON scripts/macro/build_regime_axes.py

echo ""
echo "[6/7] build_macro_profile.py (merges regime-axis loadings)"
$PYTHON scripts/macro/build_macro_profile.py

echo ""
echo "[7/7] build_thematic_beta.py (SOXX/QQQ thematic-concentration overlay)"
$PYTHON scripts/macro/build_thematic_beta.py

echo ""
echo "Macro-sensitivity refresh complete — $(date '+%Y-%m-%d %H:%M:%S')"
