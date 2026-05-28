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
    "MSFT", "TJX", "WMT", "QQQ", "CNC", "SPY",
    "INTC", "WFC", "XLU", "HYG", "AVGO", "JPM",
    "GS", "GNRC", "SMH", "RCL", "FSLR", "AMAT",
    "COF", "NET", "CIEN", "GOOG", "MRK", "GLW",
    "COP", "MS", "CMG", "EXPE", "SLV", "XLE",
    "GLD", "AAPL", "MU", "ORCL", "HOOD", "NU",
    "C", "CSCO", "XSP", "RIOT", "RKLB", "NEM",
    "VST", "CVX", "TEVA", "RTX", "TSEM", "COHR",
    "KKR", "STX", "CLS", "VLO", "FANG", "KGC",
    "HWM", "RRC", "TBT", "TOL",
]  # auto-promotion update 2026-05-21

COHORT_BEAR_CALL = [
    "SPX", "SPY", "QQQ", "DIA", "IWM", "WMT",
    "EL", "TGT", "BA", "MMM", "DVN", "HUM",
    "ADBE", "IBM", "SNAP", "XLP", "IEF", "GME",
    "UNH", "DOW", "MRK", "NEE", "LCID", "TMF",
    "ZTS", "STZ",
]  # auto-promotion update 2026-05-28

COHORT_INVERTED_FLY_PAIR = [
    "SPX", "SPY", "QQQ", "GLD", "EFA", "WMT", "NEM", "XOM",
    "PG", "WFC", "GE", "INTC", "BABA",
]

COHORT_INVERTED_FLY_SINGLE = [
    "TSLA", "AMD", "NVDA", "CAR", "AMZN", "GOOGL",
    "BABA", "SCCO", "GOLD", "CLF", "ISRG", "XLK",
    "PEP", "STX", "LRCX", "MCD", "JNJ", "PDD",
    "AG", "DELL", "AFRM",
]  # auto-promotion update 2026-05-21

COHORT_ZEBRA_TIER1 = [
    "SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
]

COHORT_ZEBRA_TIER2 = [
    "DIA", "IWM", "GLD", "TJX", "GE", "WMT",
    "AMD", "PLTR", "KRE", "CMG", "SCHW", "CSCO",
    "TTD", "USB", "XLF", "XLE", "INTC", "TSLA",
    "NFLX", "AAPL", "MU", "BAC", "ORCL", "SMH",
    "USO", "CVNA", "GOOG", "MRVL", "FCX", "EWY",
    "C", "XOM", "XSP", "DVN", "JPM", "DAL",
    "VST", "NEE", "SBUX", "CVX", "JNJ", "NET",
    "ANET", "V", "MS", "COP", "BP", "UAL",
    "AMAT", "TXN", "AA", "IBM", "CRWD", "APA",
    "BX", "APO", "GS", "RIO", "COF", "COHR",
    "CAT", "WMB", "PM", "EOG", "TTWO", "SPOT",
    "KKR", "AXP", "BHP", "MMM", "RCL", "STX",
    "ROKU", "KR", "ETN", "ADI", "ALK", "VLO",
    "OKE", "LYV", "LNG", "MPC", "ADM", "LIN",
    "EIX", "AZN", "PWR", "SCCO", "TBT", "MTZ",
    "KEYS", "SOXX", "XLK", "SE", "RMBS", "TER",
]  # auto-promotion update 2026-05-28

# Per-name overlay AUTO-attach cohort.
# Names where the V3 (10% OTM put) overlay showed positive cohort-level lift
# and walk-forward stability in its tier's backtest. Auto-attaching the
# overlay at ZEBRA entry is mechanically defensible for these names; for
# all other ZEBRA-cohort names the overlay remains a trader-discretion choice
# (the regime-conditional strike rule in zebra_overlay_rule.py still applies
# when invoked, but is not automatic).
#
# Sources:
#   - NVDA/AMZN/GOOGL: project_zebra_put_overlay_phase1_findings.md (tier-1 PASS)
#   - CMG/TTD:          project_zebra_overlay_tier2_findings.md (tier-2 per-name)
#
# CMG carries an explicit caveat: its +$63/cyc lift is concentrated in the
# 2024 Niccol-departure drawdown. TTD is the cleaner per-period distribution.
COHORT_ZEBRA_OVERLAY_AUTO = [
    "NVDA", "AMZN", "GOOGL",   # tier-1, Phase 1 validated
    "CMG", "TTD",              # tier-2, 2026-05-17 validated (CMG concentrated, TTD distributed)
]

# Anti-ZEBRA tier-1 cohort. Bearish synthetic-short structure (buy 2x ITM put
# + sell 1x ATM put). REGIME-GATED — only deployable when H1 is active
# (SPY below 200dma AND IVR_252 > 0.5). Names below passed Phase 2 per-name
# walk-forward criteria; cohort-wide gate failed (only 2/4 walk-forward
# splits positive due to H1 fire-period clustering, not 4 independent samples).
#
# Source: project_anti_zebra_phase2_findings.md + docs/ANTI_ZEBRA_PREREG.md
#
# Per-name caveats:
#   GOOGL — CONCENTRATED 2022 (96% of P/L from one bear); big magnitude when fires
#   AMZN  — CONCENTRATED 2022 (94% of P/L from one bear); big magnitude when fires
#   META  — CONCENTRATED 2022 + THIN N=4 (only the 2022 bear sample exists)
#   CNC   — distributed across 2018/2022/2025 H1 fires; small magnitude (+$12/share)
#   CLF   — distributed across 4 H1 fire periods; very small magnitude (+$0.88/share)
#
# Long-call overlay was tested in Phase 2 and REJECTED (cohort drawdown
# worsened, walk-forward 2/4). No COHORT_ANTI_ZEBRA_OVERLAY_AUTO list.
COHORT_ANTI_ZEBRA_TIER1 = [
    "GOOGL", "AMZN", "META",   # large-magnitude, 2022-concentrated
    "CNC", "CLF",              # distributed, small-magnitude
]

# Earnings — promoted in v1.6
COHORT_EARNINGS_BULL_PUT = [
    "GOOGL", "NUE", "META", "KO", "WFC", "RRC", "SCCO", "CNQ",
]
COHORT_EARNINGS_BEAR_CALL = ["INTC"]   # single-name carve-out
COHORT_EARNINGS_INVERTED_FLY = ["PLTR"]  # single-name carve-out

# T-5 MP-anchored bull put — TABLED 2026-05-03.
# Cohort intentionally emptied. The wiring (opener, qualifier branch, alert
# routing, ledger tag, mark daemon coverage) is left intact so this can be
# re-enabled by repopulating the list. Reasons for tabling:
#   1. Phase 2/3 of the MP test suite (project_mp_directional_gravity_test.md)
#      showed pin is not causal — price does not move toward MP more than
#      secular drift. Two of the proposed paper names (HYG, QQQ) showed
#      anti-convergence on the T-5 directional test.
#   2. The 0.50 credit/width framework floor is structurally unreachable on
#      MP-anchored short puts (2026-05-03 widened-wings backtest:
#      mp_phase2c_widened_wings.py, 0.3% floor pass rate).
#   3. The Phase 2f +$0.019/cycle "edge" comes from the SPY contango+VRP>0
#      signal gate, not from MP anchoring per se — a generic premium-selling
#      regime filter would likely produce the same lift on 30Δ shorts.
# See project_mp_tabled_decision.md for the full reasoning.
COHORT_BULL_PUT_T5_PAPER: list[str] = []


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

# Per-structure paper-test sizing override. When active, entries for the
# named structure are sized at PAPER_SIZE_FACTOR regardless of regime — used
# for one-cycle paper validation before promoting to live.
PAPER_SIZE_FACTOR = 0.5
PAPER_SIZED_STRUCTURES = {"bull_put_mp"}  # remove after paper window closes


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
# Inverted_fly cap raised 2026-05-06 from $100 to $300. Original $100 was
# anchored on a $620 NOC IF example; the policy intent ("avoid NOC-sized MTM
# swings") allows admitting names whose debit/max-loss is comparable to
# already-tolerated structures (KRE zebra ~$900, SOXX hedge ~$1,500). $300 cap
# admits LRCX/MCD/JNJ (debit $13-25 → max loss $1.3-2.5K). Excludes AMAT/TER
# at $400+ which produce NOC-sized debits (~$3.4K). See
# project_universe_expansion_v3.md and feedback_expensive_names_verticals_only.md.
MAX_SPOT_INVERTED_FLY = 300.0

# ZEBRA persistence-trend filter (added 2026-05-03).
# A delta-1 stock-replacement structure shouldn't fire on a name that has been
# in a sustained downtrend. The v2 pre-reg's own lesson learned: "binding
# constraint is *is this name in a sustained uptrend*, not *is this a mega-cap*."
# Suspend a ZEBRA candidate when ≥ ZEBRA_TREND_BELOW_200DMA_THRESHOLD of the
# last ZEBRA_TREND_LOOKBACK_DAYS trading days closed below the 200-DMA. Normal
# cyclical pullbacks (e.g. MSFT/META 80–120 days below) pass; only deeply
# entrenched downtrends (TTD 252/252, CMG 244/252) are filtered out.
ZEBRA_TREND_LOOKBACK_DAYS = 252
ZEBRA_TREND_BELOW_200DMA_THRESHOLD = 200

# Bull_put MA-bucket downsize threshold (added 2026-05-05 from
# project_bullput_below_ma_findings.md). When the underlying's spot is
# more than this percent BELOW its 200-DMA at entry, downgrade GO → DOWNSIZE.
# Universe-scale bull_put expectancy is ~flat across MA buckets at slip=0.50,
# but the BELOW_10PCT × OTM cell loses -$0.045/cycle. Don't SKIP (ITM held-to-
# expiry is positive in this bucket); just half-size to mark regime risk.
BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD = -0.10


# Credit-vertical loss-cap floor — minimum credit / width ratio for a
# tradeable construction. Calibrated 2026-05-05 from actual closed-trade
# win-rate (73% on N=26). Was 0.50 (assumed 67% win rate + strict managed-50%
# exit). Real win-rate buys headroom below the theoretical floor.
# Breakeven win-rate at managed-50% exit by C/W: 0.30→82%, 0.35→78%, 0.40→75%,
# 0.50→67%. At 73% actual, 0.35 is the floor with a small edge buffer.
# Re-evaluate after another 30 closed cycles. See
# feedback_loss_cap_discipline.md for the full derivation.
MIN_CREDIT_WIDTH = 0.35


# ─── Regime-health monitor (system + per-position) ─────────────────────
# Daily warning bands for regime degradation. The thresholds match the
# entry gates; the *_NEAR_BAND values define how close to a violation
# triggers a 🟡 warning. See scripts/monitor/regime_health.py for the
# assessor logic and scripts/monitor/daily_alert.py for the renderer.

TERM_SPREAD_NEAR_BAND = 0.005     # term_spread within 0.005 of 0 = 🟡
VRP_NEAR_BAND = 0.005             # VRP within 0.005 of 0 = 🟡
SPY_MA200_NEAR_PCT = 0.03         # SPY within 3% of 200-DMA = 🟡
IVR_NEAR_BAND = 0.10              # IVR within 0.10 of 0.50 = 🟡
SPOT_MA200_NEAR_PCT = 0.03        # per-position: spot within 3% of 200-DMA = 🟡
TREND_VELOCITY_LOOKBACK_DAYS = 5  # 5d Δ for velocity readout

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
VERDICT_SKIP_CONCENTRATION = "SKIP_CONCENTRATION"  # capped by sector-concentration rule


# ─── Sector-concentration cap ─────────────────────────────────────────
#
# Triggered by 2026-05-12 evidence: WFC + JPM stopped together in the same
# JUN bull_put cohort — a correlation cluster, not two independent risks.
# Cap at 2 single names per GICS sector per OpEx; ETFs exempt. Within an
# over-concentrated sector, rank by verdict tier (GO > DOWNSIZE), then
# alphabetical as deterministic tiebreaker. Lower-ranked candidates →
# SKIP_CONCENTRATION verdict with sector_rank_position annotation.
#
# Detail: project_sector_concentration_cap.md.

SECTOR_CAP_MAX_PER_OPEX = 2


# ─── Helpers ──────────────────────────────────────────────────────────

def is_in_cohort(symbol: str, structure: str) -> bool:
    """True if symbol is in the deployable cohort for structure."""
    cohorts = {
        "bull_put": COHORT_BULL_PUT,
        "bull_put_mp": COHORT_BULL_PUT_T5_PAPER,
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
