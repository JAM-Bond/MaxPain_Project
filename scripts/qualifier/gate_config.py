"""
Cycle qualifier — gate constants from TRADING_PLAN.rtf v1.7

This module is the SINGLE SOURCE for all gate parameters: cohort lists,
entry DTEs, sizing factors, per-name overrides. When the trading plan
changes, this is the file that gets updated; the qualifier reads from
here and never from the .rtf directly.

Cross-reference each constant to the plan section and version:
- Cohorts: each structure's "Symbols that respond well" section
- Window DTEs: each structure's "Trade mechanics" block
- Soft-downsize and hard-pause triggers: Regime Overrides section
- Per-name overrides: Regime Transition section + structure-specific notes
"""
from __future__ import annotations

# ─── Structure cohorts (TRADING_PLAN.rtf v1.7 — verbatim from the
#     "Symbols that respond well" sections) ──────────────────────────

COHORT_BULL_PUT = [
    "MSFT", "TJX", "WMT", "QQQ", "CNC", "RIO", "SPY", "DAL",
    "INTC", "WFC", "XLU", "HYG",
    # v2 expansion 2026-05-02 (Tier A + top Tier B; see UNIVERSE_EXPANSION_V2_PREREG.md)
    "AVGO", "JPM", "GS", "GNRC", "SMH", "RCL", "FSLR", "AMAT",
]

COHORT_BEAR_CALL = [
    "SPX", "SPY", "QQQ", "DIA", "IWM", "WMT",
    # v2 expansion 2026-05-02 (Tier B; deploy when H1 fires)
    "EL", "TGT", "BA",
]

COHORT_INVERTED_FLY_PAIR = [
    "SPX", "SPY", "QQQ", "GLD", "EFA", "WMT", "NEM", "XOM",
    "PG", "WFC", "GE", "INTC", "BABA",
]

COHORT_INVERTED_FLY_SINGLE = [
    "TSLA", "AMD", "NVDA", "CAR", "AMZN", "META", "GOOGL", "BABA",
    "SCCO", "GOLD", "CLF",
    # v2 expansion 2026-05-02 (Tier A; standalone IF — methodology was per-ticker walk-forward without bull_put pairing)
    "ISRG", "XLK", "PEP", "STX",
]

COHORT_ZEBRA_TIER1 = [
    "SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
]

COHORT_ZEBRA_TIER2 = [
    "DIA", "IWM", "GLD", "TJX", "GE", "WMT", "AMD", "PLTR",
    "KRE",  # promoted 2026-05-01 via universe expansion (see ZEBRA_UNIVERSE_EXPANSION_PREREG.md)
]

# Earnings — promoted in v1.6
COHORT_EARNINGS_BULL_PUT = [
    "GOOGL", "NUE", "META", "KO", "WFC", "RRC", "SCCO", "CNQ",
]
COHORT_EARNINGS_BEAR_CALL = ["INTC"]   # single-name carve-out
COHORT_EARNINGS_INVERTED_FLY = ["PLTR"]  # single-name carve-out

# Covered call on credit ETFs — DEMOTED 2026-04-30 on live-execution
# falsification. Backtest validated BKLN / JNK / HYG at slip=0.05 (mean +7.7%
# / +7.2% / +4.1% annualized), but live attempts on the May 2026 chain showed
# $0 bids on OTM calls across all three names — no takers at any limit price
# ≥ mid − $0.125. Realistic slip is $0.30+ below mid, which flips the
# strategy negative on every name in the cohort. See:
#   - project_covered_call_credit_etfs_findings.md (live falsification section)
#   - feedback_backtest_slip_assumption_validation.md
# Cohort kept as an empty list so the qualifier wiring remains intact; can be
# re-populated if the strategy is rehabilitated on a different universe.
COHORT_COVERED_CALL = []


# ─── Entry windows (calendar/trading days, per plan) ──────────────────

WINDOW_BULL_PUT_45DTE = 45         # Window A: managed first-trigger
WINDOW_BULL_PUT_T5 = 5             # Window B: T-5 trading days
WINDOW_BEAR_CALL_45DTE = 45
WINDOW_INVERTED_FLY_45DTE = 45
WINDOW_ZEBRA_75DTE = 75
# Covered call: enter the trading day AFTER prior monthly OpEx, hold to
# next monthly OpEx (~21 trading days). Window is "first 1-2 trading days
# after prior OpEx" — a single-shot entry per cycle.
WINDOW_COVERED_CALL_AFTER_OPEX_TOLERANCE = 2  # trading days after prior OpEx

# Earnings entries in TRADING DAYS before earnings event
WINDOW_EARNINGS_T3 = 3             # default T-3
WINDOW_EARNINGS_T1 = 1             # exception: SCCO and CNQ

# Earnings T-1 names (use T-1 instead of T-3 per per-ticker results in plan)
EARNINGS_T1_NAMES = {"SCCO", "CNQ"}

# Window tolerance: accept entries within +/- this many trading days
ENTRY_WINDOW_TOLERANCE = 1


# ─── Sizing factors ───────────────────────────────────────────────────

SIZE_DEFAULT = 1.0
SIZE_DOWNSIZE = 0.5      # soft-downsize trigger fires
SIZE_PAUSE = 0.0         # hard-pause trigger fires


# ─── Budget caps (capital-outlay structures only) ─────────────────────
#
# Per feedback_expensive_names_verticals_only.md (2026-04-28): stocks at or
# above this spot price get credit verticals only. ZEBRA and IF reserve
# capital outlay for sub-cap names. Credit verticals are NOT gated here —
# bull_put on SPY at $580 with $0.50 width is fine.
#
# Threshold is a parameter, not a constant of nature: bump if book equity
# grows.

MAX_SPOT_ZEBRA = 100.0
MAX_SPOT_INVERTED_FLY = 100.0

BUDGET_CAPS = {
    "zebra_tier1": MAX_SPOT_ZEBRA,
    "zebra_tier2": MAX_SPOT_ZEBRA,
    "inverted_fly_pair": MAX_SPOT_INVERTED_FLY,
    "inverted_fly_single": MAX_SPOT_INVERTED_FLY,
    "inverted_fly_earnings": MAX_SPOT_INVERTED_FLY,
}


# ─── Per-name overrides ───────────────────────────────────────────────

# GOOGL inverted_fly runs WITHOUT the term-inversion gate.
# Source: plan v1.6 IF section + project_if_universe_expansion findings
# (gate-hurt puzzle: -$2.29/cycle lift when gate applied to GOOGL).
IF_NO_GATE_NAMES = {"GOOGL"}

# SPX excluded from any structure that needs Schwab quotes (live capture
# limitation; the backtest-validated cohort still includes SPX but the
# qualifier excludes SPX from per-day verdicts since we can't get a live
# quote for index strike pricing).
SPX_EXCLUDED_FROM_QUALIFIER = True


# ─── Verdict labels ───────────────────────────────────────────────────

VERDICT_GO = "GO"                   # place this trade today
VERDICT_DOWNSIZE = "DOWNSIZE"       # place at half size (soft-downsize active)
VERDICT_PENDING = "PENDING"         # entry window upcoming
VERDICT_SKIP = "SKIP"               # gate not satisfied this cycle
VERDICT_PAUSE = "PAUSE"             # hard pause active for this structure
VERDICT_NOT_IN_COHORT = "NOT_IN_COHORT"


# ─── Helpers ──────────────────────────────────────────────────────────

def is_in_cohort(symbol: str, structure: str) -> bool:
    """True if symbol is in the deployable cohort for structure."""
    cohorts = {
        "bull_put": COHORT_BULL_PUT,
        "bear_call": COHORT_BEAR_CALL,
        "inverted_fly_pair": COHORT_INVERTED_FLY_PAIR,
        "inverted_fly_single": COHORT_INVERTED_FLY_SINGLE,
        "zebra_tier1": COHORT_ZEBRA_TIER1,
        "zebra_tier2": COHORT_ZEBRA_TIER2,
        "bull_put_earnings": COHORT_EARNINGS_BULL_PUT,
        "bear_call_earnings": COHORT_EARNINGS_BEAR_CALL,
        "inverted_fly_earnings": COHORT_EARNINGS_INVERTED_FLY,
        "covered_call": COHORT_COVERED_CALL,
    }
    return symbol in cohorts.get(structure, [])


# ALL_STRUCTURES is the canonical iteration order for qualifier output
ALL_STRUCTURES = [
    "bull_put", "bear_call",
    "inverted_fly_pair", "inverted_fly_single",
    "zebra_tier1", "zebra_tier2",
    "bull_put_earnings", "bear_call_earnings", "inverted_fly_earnings",
    "covered_call",
]
