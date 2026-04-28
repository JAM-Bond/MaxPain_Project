"""Paths and parameters for the Track A backtest engine."""
from pathlib import Path

PROJECT_ROOT = Path.home() / "MaxPain_Project"
PARQUET_ROOT = PROJECT_ROOT / "data" / "orats" / "parquet"
BY_TICKER_ROOT = PROJECT_ROOT / "data" / "orats" / "by_ticker"
BACKTEST_ROOT = PROJECT_ROOT / "data" / "backtest"
RESULTS_PATH = BACKTEST_ROOT / "results_v1.parquet"
RESULTS_V2_PATH = BACKTEST_ROOT / "results_v2.parquet"
UNIVERSE_PATH = PROJECT_ROOT / "data" / "profile" / "universe_v1.parquet"
LOG_PATH = PROJECT_ROOT / "logs" / "backtest.log"

# Structure parameters per Track A v1 spec (project_backtest_v1_scope.md)
IC_SHORT_DELTA = 0.30      # target call delta for short legs (put side uses 1-0.30=0.70)
IC_WING_WIDTH = 1.0        # dollars — absolute floor
VERTICAL_SHORT_DELTA = 0.30
VERTICAL_WING_WIDTH = 2.0
STRANGLE_SHORT_DELTA = 0.15
BFLY_WING_WIDTH = 1.0

# Jade Lizard — long call sits at the 15Δ strike (defines the call wing)
JADE_LONG_CALL_DELTA = 0.15
JADE_LONG_CALL_TOL = 0.05

# ZEBRA — buy 2x ITM call ~70Δ, sell 1x ATM call ~50Δ
ZEBRA_LONG_DELTA = 0.70
ZEBRA_LONG_TOL = 0.10
ZEBRA_SHORT_DELTA = 0.50
ZEBRA_SHORT_TOL = 0.08

# Entry windows (days to expiration)
ENTRY_DTE_NEAR = 7
ENTRY_DTE_LONG = 45

# Exit rules
EXIT_T_MINUS = 3           # close N days before expiry
EXIT_DTE_RULE = 21         # close when DTE <= this
EXIT_PROFIT_FRAC = 0.50    # close when profit >= this fraction of max profit

# Tolerances for leg selection — if no strike within tolerance, skip cycle
DELTA_TOLERANCE = 0.05
MIN_IV_FOR_PRICING = 0.01

# ─── v2 configuration (project_backtest_v1_results.md recommendation) ─────
# Wings scaled to % of spot — floor at the absolute width above.
IC_WING_PCT_SPOT_V2 = 0.0025       # 0.25% of spot
BFLY_WING_PCT_SPOT_V2 = 0.0025
VERTICAL_WING_PCT_SPOT_V2 = 0.0050 # 0.50% of spot

# Runtime toggles — v1 defaults keep the v1 engine behavior unchanged.
PRICING_MODE = "bidask"           # "bidask" (v1), "mid" (v2), or "slip"
PRICING_SLIP_FRAC = 0.0           # only used when PRICING_MODE == "slip":
                                  #   0.0 = mid (equals v2), 1.0 = full bid-ask (equals v1)
IC_WING_PCT_SPOT = 0.0
BFLY_WING_PCT_SPOT = 0.0
VERTICAL_WING_PCT_SPOT = 0.0


def activate_v2() -> None:
    """Switch module-level parameters to the v2 configuration (mid pricing + scaled wings)."""
    global PRICING_MODE, IC_WING_PCT_SPOT, BFLY_WING_PCT_SPOT, VERTICAL_WING_PCT_SPOT
    PRICING_MODE = "mid"
    IC_WING_PCT_SPOT = IC_WING_PCT_SPOT_V2
    BFLY_WING_PCT_SPOT = BFLY_WING_PCT_SPOT_V2
    VERTICAL_WING_PCT_SPOT = VERTICAL_WING_PCT_SPOT_V2


def activate_slip(slip_frac: float) -> None:
    """v2 wing scaling with slippage-aware pricing. slip_frac in [0, 1]."""
    global PRICING_MODE, PRICING_SLIP_FRAC, IC_WING_PCT_SPOT, BFLY_WING_PCT_SPOT, VERTICAL_WING_PCT_SPOT
    PRICING_MODE = "slip"
    PRICING_SLIP_FRAC = float(slip_frac)
    IC_WING_PCT_SPOT = IC_WING_PCT_SPOT_V2
    BFLY_WING_PCT_SPOT = BFLY_WING_PCT_SPOT_V2
    VERTICAL_WING_PCT_SPOT = VERTICAL_WING_PCT_SPOT_V2
