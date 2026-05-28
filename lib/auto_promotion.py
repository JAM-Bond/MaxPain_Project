"""Auto-promotion pipeline — shared helpers.

Implements:
  - BH-FDR (Benjamini-Hochberg) cutoff for multiple-comparisons correction
  - Ledger I/O for the scan_ledger.parquet (per-ticker last-evaluated state)
  - Gate evaluators for the sealed promotion / demotion rules
  - cohort_changes DB table (actionable subset of decisions, query-friendly)

Used by:
  - scripts/maintenance/auto_promotion_liquidity_scan.py
  - scripts/maintenance/auto_promotion_nightly.py
  - scripts/maintenance/auto_promotion_gate_check.py
  - scripts/maintenance/auto_promotion_gate_config_writer.py

See docs/AUTO_PROMOTION_PIPELINE_PREREG.md for the sealed pre-reg.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
AUTO_PROMOTION_DIR = ROOT / "data/profile/auto_promotion"
LEDGER_PATH = AUTO_PROMOTION_DIR / "scan_ledger.parquet"

# ── Sealed thresholds (from AUTO_PROMOTION_PIPELINE_PREREG.md §2 + §3 + §4) ─

# Stage 1: liquidity gates
LIQ_FRONT_MONTH_OI_MIN = 10_000
LIQ_AVG_DAILY_VOL_MIN = 1_000
LIQ_ATM_BIDASK_PCT_MAX = 0.10
LIQ_SPOT_MIN = 5.0
LIQ_SPOT_MAX = 1000.0

# Stage 2: nightly batch size
NIGHTLY_BATCH_SIZE = 500

# §3: promotion gates
PROMO_GATE_B_MIN_POSITIVE_SPLITS = 3   # of 4
PROMO_GATE_B_MIN_VAL_N = 12
PROMO_GATE_B_MIN_MEAN_VERTICAL = 5.0   # $/contract for bull_put + bear_call
PROMO_GATE_B_MIN_MEAN_IF = 10.0        # $/contract for IF
# ZEBRA-specific promotion gates (mirror zebra_universe_expansion_backtest.py).
# History:
#   - Original pre-reg used a single threshold ("+5% median capture") which was
#     scale-mismatched against the metric (5.0% vs metric units of ratio×100).
#   - 2026-05-19: bumped to 85.0 to align with live MIN_MEDIAN_CAPTURE = 0.85.
#   - 2026-05-20: single-metric gate still passed 78 names because ZEBRA's
#     ~+1.4 effective long delta structurally beats 85% capture on most names.
#     Aligned the full 5-gate set with the live expansion script. See
#     project_zebra_pipeline_gate_alignment.md.
PROMO_ZEBRA_MIN_MEDIAN_CAPTURE_PCT = 85.0   # val median capture × 100 (H3 in live)
PROMO_ZEBRA_MAX_CAP_EFFICIENCY = 0.50       # H4 (median over val)
PROMO_ZEBRA_MIN_FLAT_DAY_MTM = -0.01        # H2 (mean over val)
PROMO_ZEBRA_MIN_WF_CAPTURE_BOTH_PCT = 100.0 # train AND val median capture × 100
PROMO_ZEBRA_REQUIRE_BOTH_WF_MEAN_POSITIVE = True  # train_mean_zebra AND val_mean_zebra > 0
PROMO_ZEBRA_MIN_N_TOTAL = 50                # lifetime cycle floor
PROMO_ZEBRA_MIN_N_TRAIN = 22                # most-recent split train cycle floor
# Legacy alias kept so older imports / audits still resolve.
PROMO_GATE_B_MIN_MEDIAN_ZEBRA_PCT = PROMO_ZEBRA_MIN_MEDIAN_CAPTURE_PCT
PROMO_GATE_D_MAX_YEAR_FRACTION = 0.50  # concentration cap
PROMO_GATE_E_BH_FDR_Q = 0.10           # BH-FDR cutoff

# §4: demotion gates
DEMO_GATE_F_MAX_POSITIVE_SPLITS = 1    # fail-promote inverse threshold
DEMO_GATE_F_MIN_VALID_SPLITS = 3       # require ≥3/4 splits with valid data
                                         # before Gate F can demote (added
                                         # 2026-05-19 after first-night audit
                                         # showed "1/2 valid splits" triggering
                                         # demotion on insufficient sample).
DEMO_GATE_G_CONSECUTIVE_LIQ_FAILS = 3

# §8 falsification triggers — steady-state
SAFETY_MAX_PROMOTIONS_PER_NIGHT = 50
SAFETY_MAX_DEMOTIONS_PER_NIGHT = 5
SAFETY_MAX_COHORT_SIZE = 200

# ── First-pass discovery ramp-up window (2026-05-20 → 2026-06-09) ──
# Rationale: the pipeline's first full-universe scan exposes a large set of
# eligible names that have never been in the manually-curated cohorts (e.g.,
# 78 ZEBRA candidates with ≥85% historical capture). The steady-state caps
# (50 / 5) reject those by design as a "first-night flood." A time-bounded
# widening allows the discovery phase to land; caps auto-revert on the sunset
# date below. Pre-reg §8 thresholds are restored automatically.
#
# This is NOT a gate change. The gates (A-E) that decide what passes
# remain at the sealed thresholds. Only the safety BRAKE is widened.
#
# See: project_pipeline_safety_caps_rampup_20260520.md
RAMP_UP_PROMOTIONS_PER_NIGHT = 200
RAMP_UP_DEMOTIONS_PER_NIGHT = 10
RAMP_UP_END_DATE = date(2026, 6, 9)


def _current_safety_caps(as_of: date | None = None) -> tuple[int, int, int]:
    """Return today's (max_promotions, max_demotions, max_cohort_size) caps.

    During the ramp-up window (≤ RAMP_UP_END_DATE), uses the elevated caps
    to let first-pass discovery land. Auto-reverts to pre-reg §8 values
    after the sunset date.
    """
    today = as_of or date.today()
    if today <= RAMP_UP_END_DATE:
        return (RAMP_UP_PROMOTIONS_PER_NIGHT,
                RAMP_UP_DEMOTIONS_PER_NIGHT,
                SAFETY_MAX_COHORT_SIZE)
    return (SAFETY_MAX_PROMOTIONS_PER_NIGHT,
            SAFETY_MAX_DEMOTIONS_PER_NIGHT,
            SAFETY_MAX_COHORT_SIZE)


# ─── BH-FDR ───────────────────────────────────────────────────────────────

def benjamini_hochberg(pvalues: Sequence[float], q: float = PROMO_GATE_E_BH_FDR_Q) -> np.ndarray:
    """Return boolean array same length as pvalues: True where survives BH-FDR cutoff at level q.

    Standard Benjamini-Hochberg: sort p-values ascending, find largest k where
    p_(k) <= (k/m) * q, all p-values with rank ≤ k survive.

    NaN p-values are treated as fail (do not survive).
    """
    p = np.asarray(pvalues, dtype=float)
    m = len(p)
    if m == 0:
        return np.array([], dtype=bool)

    # Mask NaNs out for ranking; they fail by default
    valid_mask = ~np.isnan(p)
    if not valid_mask.any():
        return np.zeros(m, dtype=bool)

    valid_p = p[valid_mask]
    n_valid = len(valid_p)

    # Sort with original indices
    order = np.argsort(valid_p)
    sorted_p = valid_p[order]
    ranks = np.arange(1, n_valid + 1)
    threshold = (ranks / n_valid) * q  # BH using N valid only — standard practice
    significant = sorted_p <= threshold
    if not significant.any():
        k = 0
    else:
        # Largest k with significant
        k = np.max(np.where(significant)[0]) + 1

    survives_valid = np.zeros(n_valid, dtype=bool)
    survives_valid[order[:k]] = True

    survives = np.zeros(m, dtype=bool)
    survives[valid_mask] = survives_valid
    return survives


# ─── Ledger I/O ───────────────────────────────────────────────────────────

def load_ledger() -> pd.DataFrame:
    """Read scan_ledger.parquet (ticker, last_evaluated_date, last_walkforward_status).

    Returns empty DataFrame with correct columns if file doesn't exist.
    """
    if not LEDGER_PATH.exists():
        return pd.DataFrame(columns=["ticker", "last_evaluated_date",
                                       "last_walkforward_status"])
    df = pd.read_parquet(LEDGER_PATH)
    if "last_evaluated_date" in df.columns:
        df["last_evaluated_date"] = pd.to_datetime(df["last_evaluated_date"]).dt.date
    return df


def save_ledger(df: pd.DataFrame) -> None:
    """Write scan_ledger.parquet atomically (tmpfile + rename)."""
    AUTO_PROMOTION_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(LEDGER_PATH)


def update_ledger(ticker_status_pairs: list[tuple[str, str]],
                   run_date: date | None = None) -> pd.DataFrame:
    """Update ledger with (ticker, status) pairs evaluated on run_date.

    Returns the updated DataFrame. Persists to disk.
    """
    if run_date is None:
        run_date = date.today()
    led = load_ledger()
    led = led.set_index("ticker") if not led.empty else led
    for ticker, status in ticker_status_pairs:
        if ticker in led.index:
            led.loc[ticker, "last_evaluated_date"] = run_date
            led.loc[ticker, "last_walkforward_status"] = status
        else:
            led.loc[ticker] = {
                "last_evaluated_date": run_date,
                "last_walkforward_status": status,
            }
    led = led.reset_index()
    save_ledger(led)
    return led


def pick_nightly_batch(eligible_tickers: list[str],
                        batch_size: int = NIGHTLY_BATCH_SIZE,
                        liquidity_scores: dict[str, float] | None = None,
                        ) -> list[str]:
    """Pick batch_size tickers from eligible (= liquidity-passing today), ordered by:
       1. last_evaluated_date ASC (oldest first; NULL = never evaluated = ranked first)
       2. Tiebreak: liquidity_score DESC (higher OI first)

    Returns up to batch_size ticker symbols.
    """
    led = load_ledger().set_index("ticker") if not load_ledger().empty else None
    rows = []
    for t in eligible_tickers:
        if led is not None and t in led.index:
            last_dt = led.loc[t, "last_evaluated_date"]
            # Pandas sometimes returns numpy datetime; normalize
            if hasattr(last_dt, 'date'):
                last_dt = last_dt.date() if hasattr(last_dt, 'date') else last_dt
            never = False
        else:
            last_dt = None
            never = True
        score = (liquidity_scores or {}).get(t, 0.0)
        rows.append({"ticker": t, "last_dt": last_dt, "never": never, "score": score})
    df = pd.DataFrame(rows)
    # NEVER-evaluated names first (never=True sorts to top via ascending=False on `never`),
    # then by last_dt ascending (oldest first), then by score descending (higher OI first)
    df = df.sort_values(
        by=["never", "last_dt", "score"],
        ascending=[False, True, False],
        na_position="first",
    )
    return df.head(batch_size)["ticker"].tolist()


# ─── Promotion / demotion gate evaluators ─────────────────────────────────

def evaluate_promotion_gate_b(walkforward_rows: pd.DataFrame,
                                structure: str) -> tuple[bool, dict]:
    """Gate B: walk-forward stability gate.

    For verticals + inverted_fly: ≥3 of 4 splits positive AND most-recent split
    mean ≥ threshold AND val_N ≥ 12.

    For ZEBRA: dispatches to `_evaluate_promotion_zebra_5gate` which mirrors
    the live `zebra_universe_expansion_backtest.py` gate set (median capture +
    cap_efficiency + flat-day MTM + walk-forward symmetry + sample sizes).

    walkforward_rows: DataFrame with one row per validation split. For ZEBRA
                       rows must also include train_median_capture_pct,
                       train_mean_zebra, val_mean_zebra, val_cap_efficiency,
                       val_flat_day_mtm, total_cycles.
    Returns (passes, detail_dict).
    """
    if walkforward_rows.empty:
        return False, {"reason": "no walkforward rows"}

    if structure == "zebra":
        return _evaluate_promotion_zebra_5gate(walkforward_rows)

    splits_positive = (walkforward_rows["mean_pnl"] > 0).sum()
    most_recent = walkforward_rows.sort_values("split").iloc[-1]
    most_recent_mean = most_recent["mean_pnl"]
    most_recent_val_n = most_recent.get("val_n", 0)

    if structure in ("bull_put", "bear_call"):
        threshold = PROMO_GATE_B_MIN_MEAN_VERTICAL
    elif structure == "inverted_fly":
        threshold = PROMO_GATE_B_MIN_MEAN_IF
    else:
        raise ValueError(f"Unknown structure: {structure}")

    passes = (
        splits_positive >= PROMO_GATE_B_MIN_POSITIVE_SPLITS
        and most_recent_mean >= threshold
        and most_recent_val_n >= PROMO_GATE_B_MIN_VAL_N
    )

    return passes, {
        "splits_positive": int(splits_positive),
        "splits_threshold": PROMO_GATE_B_MIN_POSITIVE_SPLITS,
        "most_recent_mean": float(most_recent_mean),
        "mean_threshold": threshold,
        "most_recent_val_n": int(most_recent_val_n),
        "val_n_threshold": PROMO_GATE_B_MIN_VAL_N,
        "passes": bool(passes),
    }


def _evaluate_promotion_zebra_5gate(walkforward_rows: pd.DataFrame
                                       ) -> tuple[bool, dict]:
    """ZEBRA promotion: all 5 sub-gates must pass, mirroring
    `scripts/backtest/zebra_universe_expansion_backtest.py`:

      Z1 (n_total)             — lifetime cycles ≥ 50
      Z2 (n_train)             — most-recent split train_n ≥ 22
      Z3 (median capture)      — val median capture ≥ 85% (×100)
      Z4 (walk-forward sym)    — train AND val median capture both ≥ 100% (×100)
                                  AND train_mean_zebra > 0 AND val_mean_zebra > 0
      Z5 (operational risk)    — val cap_efficiency ≤ 0.50 AND val flat-day MTM ≥ −0.01

    Returns (passes, detail_dict). The detail dict keeps a top-level
    `most_recent_mean` field for compatibility with the gate_check.py reason
    string and the cohort_changes audit row.
    """
    most_recent = walkforward_rows.sort_values("split").iloc[-1]

    n_total = int(most_recent.get("total_cycles", 0))
    n_train = int(most_recent.get("train_n", 0))
    most_recent_mean = float(most_recent.get("mean_pnl", float("nan")))
    train_capture_pct = float(most_recent.get("train_median_capture_pct",
                                                float("nan")))
    train_mean_zebra = float(most_recent.get("train_mean_zebra", float("nan")))
    val_mean_zebra = float(most_recent.get("val_mean_zebra", float("nan")))
    cap_efficiency = float(most_recent.get("val_cap_efficiency", float("nan")))
    flat_day_mtm = float(most_recent.get("val_flat_day_mtm", float("nan")))

    z1_pass = n_total >= PROMO_ZEBRA_MIN_N_TOTAL
    z2_pass = n_train >= PROMO_ZEBRA_MIN_N_TRAIN
    z3_pass = (pd.notna(most_recent_mean)
                and most_recent_mean >= PROMO_ZEBRA_MIN_MEDIAN_CAPTURE_PCT)
    z4_capture_pass = (
        pd.notna(train_capture_pct)
        and train_capture_pct >= PROMO_ZEBRA_MIN_WF_CAPTURE_BOTH_PCT
        and pd.notna(most_recent_mean)
        and most_recent_mean >= PROMO_ZEBRA_MIN_WF_CAPTURE_BOTH_PCT
    )
    z4_mean_pass = (
        pd.notna(train_mean_zebra) and train_mean_zebra > 0
        and pd.notna(val_mean_zebra) and val_mean_zebra > 0
    ) if PROMO_ZEBRA_REQUIRE_BOTH_WF_MEAN_POSITIVE else True
    z4_pass = z4_capture_pass and z4_mean_pass
    z5_cap_pass = (pd.notna(cap_efficiency)
                    and cap_efficiency <= PROMO_ZEBRA_MAX_CAP_EFFICIENCY)
    z5_flat_pass = (pd.notna(flat_day_mtm)
                     and flat_day_mtm >= PROMO_ZEBRA_MIN_FLAT_DAY_MTM)
    z5_pass = z5_cap_pass and z5_flat_pass

    passes = z1_pass and z2_pass and z3_pass and z4_pass and z5_pass

    fails = []
    if not z1_pass: fails.append(f"Z1 n_total={n_total}<{PROMO_ZEBRA_MIN_N_TOTAL}")
    if not z2_pass: fails.append(f"Z2 n_train={n_train}<{PROMO_ZEBRA_MIN_N_TRAIN}")
    if not z3_pass: fails.append(f"Z3 cap={most_recent_mean:.1f}<{PROMO_ZEBRA_MIN_MEDIAN_CAPTURE_PCT}")
    if not z4_capture_pass:
        fails.append(f"Z4 wf-cap train={train_capture_pct:.1f}/val={most_recent_mean:.1f}<{PROMO_ZEBRA_MIN_WF_CAPTURE_BOTH_PCT}")
    if not z4_mean_pass:
        fails.append(f"Z4 wf-mean train={train_mean_zebra:.2f}/val={val_mean_zebra:.2f}")
    if not z5_cap_pass: fails.append(f"Z5 cap_eff={cap_efficiency:.2f}>{PROMO_ZEBRA_MAX_CAP_EFFICIENCY}")
    if not z5_flat_pass: fails.append(f"Z5 flat_mtm={flat_day_mtm:.3f}<{PROMO_ZEBRA_MIN_FLAT_DAY_MTM}")

    return passes, {
        # Compatibility fields for gate_check.py PROMOTE reason + audit table.
        "splits_positive": int((walkforward_rows["mean_pnl"] > 0).sum()),
        "splits_threshold": PROMO_GATE_B_MIN_POSITIVE_SPLITS,
        "most_recent_mean": most_recent_mean,
        "mean_threshold": PROMO_ZEBRA_MIN_MEDIAN_CAPTURE_PCT,
        "most_recent_val_n": int(most_recent.get("val_n", 0)),
        "val_n_threshold": 0,  # ZEBRA uses n_train/n_total floors instead
        "passes": bool(passes),
        # ZEBRA-specific detail
        "zebra_gates": {
            "z1_n_total": {"value": n_total, "min": PROMO_ZEBRA_MIN_N_TOTAL, "passes": z1_pass},
            "z2_n_train": {"value": n_train, "min": PROMO_ZEBRA_MIN_N_TRAIN, "passes": z2_pass},
            "z3_median_capture_pct": {"value": most_recent_mean, "min": PROMO_ZEBRA_MIN_MEDIAN_CAPTURE_PCT, "passes": z3_pass},
            "z4_wf_capture": {
                "train_pct": train_capture_pct, "val_pct": most_recent_mean,
                "min_both": PROMO_ZEBRA_MIN_WF_CAPTURE_BOTH_PCT, "passes": z4_capture_pass,
            },
            "z4_wf_mean_zebra": {
                "train": train_mean_zebra, "val": val_mean_zebra,
                "require_both_positive": PROMO_ZEBRA_REQUIRE_BOTH_WF_MEAN_POSITIVE,
                "passes": z4_mean_pass,
            },
            "z5_cap_efficiency": {"value": cap_efficiency, "max": PROMO_ZEBRA_MAX_CAP_EFFICIENCY, "passes": z5_cap_pass},
            "z5_flat_day_mtm": {"value": flat_day_mtm, "min": PROMO_ZEBRA_MIN_FLAT_DAY_MTM, "passes": z5_flat_pass},
        },
        "fail_summary": "; ".join(fails) if fails else "all 5 gates pass",
    }


def evaluate_concentration_gate_d(per_year_pnl: dict[int, float]) -> tuple[bool, dict]:
    """Gate D: no single year contributes > 50% of total |P/L| across all splits.

    Computed on absolute values to handle mixed-sign years (otherwise a small positive
    sum could fail trivially when one year is large negative + one year is large positive).
    """
    total_abs = sum(abs(v) for v in per_year_pnl.values())
    if total_abs == 0:
        return True, {"max_year_fraction": 0.0, "reason": "all years zero"}
    max_frac = max(abs(v) / total_abs for v in per_year_pnl.values())
    passes = max_frac <= PROMO_GATE_D_MAX_YEAR_FRACTION
    return passes, {
        "max_year_fraction": float(max_frac),
        "threshold": PROMO_GATE_D_MAX_YEAR_FRACTION,
        "passes": bool(passes),
    }


def evaluate_demotion_gate_f(walkforward_rows: pd.DataFrame) -> tuple[bool, dict]:
    """Gate F: ≤1 of 4 splits show mean P/L > 0 → DEMOTE (symmetric inverse of Gate B promote).

    Requires ≥DEMO_GATE_F_MIN_VALID_SPLITS valid (non-NaN) splits before demotion
    can fire. Insufficient data → don't demote (conservative on the demotion side;
    the system should err toward preserving cohort membership when uncertain).
    """
    if walkforward_rows.empty:
        return False, {"reason": "no walkforward rows — insufficient data to demote",
                       "valid_splits": 0, "splits_positive": 0,
                       "min_valid_required": DEMO_GATE_F_MIN_VALID_SPLITS,
                       "passes": False}
    valid_mask = walkforward_rows["mean_pnl"].notna()
    valid_splits = int(valid_mask.sum())
    if valid_splits < DEMO_GATE_F_MIN_VALID_SPLITS:
        return False, {
            "reason": f"only {valid_splits}/{DEMO_GATE_F_MIN_VALID_SPLITS} valid splits — insufficient data",
            "valid_splits": valid_splits,
            "splits_positive": int((walkforward_rows.loc[valid_mask, "mean_pnl"] > 0).sum()),
            "min_valid_required": DEMO_GATE_F_MIN_VALID_SPLITS,
            "passes": False,
        }
    splits_positive = int((walkforward_rows.loc[valid_mask, "mean_pnl"] > 0).sum())
    passes = splits_positive <= DEMO_GATE_F_MAX_POSITIVE_SPLITS
    return passes, {
        "splits_positive": splits_positive,
        "valid_splits": valid_splits,
        "threshold": DEMO_GATE_F_MAX_POSITIVE_SPLITS,
        "min_valid_required": DEMO_GATE_F_MIN_VALID_SPLITS,
        "passes": bool(passes),
    }


def has_open_position(ticker: str, structure: str) -> bool:
    """Gate H protection: True if there's an open placed=1 position on this (ticker, structure)."""
    from lib.db import DB_PATH
    # Map our structure name to spread_score_trades.spread_type
    structure_to_spread_type = {
        "bull_put": "bull_put",
        "bear_call": "bear_call",
        "inverted_fly": "inverted_fly",
        "zebra": "zebra_tier1",  # could also be zebra_tier2 — we check both below
    }
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if structure == "zebra":
            spread_types = ["zebra_tier1", "zebra_tier2"]
        else:
            spread_types = [structure_to_spread_type.get(structure, structure)]
        placeholders = ",".join("?" for _ in spread_types)
        params = [ticker] + spread_types
        rows = conn.execute(f"""
            SELECT 1 FROM spread_score_trades
            WHERE status='open' AND placed=1
              AND symbol=? AND spread_type IN ({placeholders})
            LIMIT 1
        """, params).fetchall()
    finally:
        conn.close()
    return len(rows) > 0


# ─── Safety checks (called by the gate_config writer before mutating) ────

def check_safety_thresholds(n_promotions: int, n_demotions: int,
                              cohort_sizes_after: dict[str, int],
                              as_of: date | None = None,
                              ) -> tuple[bool, list[str]]:
    """Return (ok, list of violation messages). If any safety threshold is breached,
    the writer should HALT and require human review.

    Caps depend on the date (ramp-up window vs steady-state); see
    `_current_safety_caps()` for the time-based selection.
    """
    max_prom, max_dem, max_cohort = _current_safety_caps(as_of)
    in_rampup = (as_of or date.today()) <= RAMP_UP_END_DATE
    cap_label = " (ramp-up)" if in_rampup else ""
    violations = []
    if n_promotions > max_prom:
        violations.append(
            f"Promotions={n_promotions} exceeds nightly safety cap "
            f"{max_prom}{cap_label}"
        )
    if n_demotions > max_dem:
        violations.append(
            f"Demotions={n_demotions} exceeds nightly safety cap "
            f"{max_dem}{cap_label}"
        )
    for structure, size in cohort_sizes_after.items():
        if size > max_cohort:
            violations.append(
                f"Cohort {structure} would be size {size} > cap {max_cohort}"
            )
    return len(violations) == 0, violations


# ─── cohort_changes DB table ──────────────────────────────────────────────
# Thin operational table holding the actionable subset of nightly decisions
# (PROMOTE / DEMOTE / DEMOTE_DEFERRED only — never NO_CHANGE or SKIP).
# Full per-row audit lives in parquet; this table exists for SQL querying,
# dashboard surfacing, and cross-referencing with trades and qualifier runs.

COHORT_CHANGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS cohort_changes (
    run_date            TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    structure           TEXT NOT NULL,
    action              TEXT NOT NULL,
    cohort_name         TEXT,
    reason              TEXT,
    splits_positive     INTEGER,
    valid_splits        INTEGER,
    most_recent_mean    REAL,
    mean_threshold      REAL,
    most_recent_val_n   INTEGER,
    most_recent_p       REAL,
    max_year_fraction   REAL,
    n_consecutive_liq_fails INTEGER,
    applied             INTEGER NOT NULL DEFAULT 0,
    safety_halt_reason  TEXT,
    detail_json         TEXT,
    created_at          TEXT NOT NULL,
    PRIMARY KEY (run_date, ticker, structure, action)
);
"""

COHORT_CHANGES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_cohort_changes_ticker ON cohort_changes(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_cohort_changes_run_date ON cohort_changes(run_date)",
    "CREATE INDEX IF NOT EXISTS idx_cohort_changes_structure ON cohort_changes(structure)",
    "CREATE INDEX IF NOT EXISTS idx_cohort_changes_applied ON cohort_changes(applied)",
]

# Structure → COHORT_* constant name (mirror of writer's mapping).
# Defined here so this module doesn't import the writer (which has heavier deps).
_STRUCTURE_TO_COHORT_NAME = {
    "bull_put": "COHORT_BULL_PUT",
    "bear_call": "COHORT_BEAR_CALL",
    "inverted_fly": "COHORT_INVERTED_FLY_SINGLE",
    "zebra": "COHORT_ZEBRA_TIER2",
}


def init_cohort_changes_table(conn: sqlite3.Connection | None = None) -> None:
    """Create cohort_changes table + indexes if missing. Idempotent."""
    from lib.db import DB_PATH
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(COHORT_CHANGES_SCHEMA)
        for ddl in COHORT_CHANGES_INDEXES:
            conn.execute(ddl)
        conn.commit()
    finally:
        if own_conn:
            conn.close()


def record_cohort_changes(decisions: list,
                            run_date: date,
                            applied: bool,
                            safety_halt_reason: str | None = None,
                            ) -> int:
    """Persist actionable decisions (PROMOTE/DEMOTE/DEMOTE_DEFERRED) to the
    cohort_changes table. NO_CHANGE / SKIP rows are filtered out — those live
    in parquet only.

    Args:
      decisions: list of GateDecision (from auto_promotion_gate_check).
      run_date: the nightly cron run date.
      applied: True if the writer actually applied changes to gate_config.py;
               False if the safety brake halted (or dry-run).
      safety_halt_reason: populated when applied=False due to safety violations.

    Returns the number of rows inserted.
    """
    from lib.db import DB_PATH
    actionable_actions = {"PROMOTE", "DEMOTE", "DEMOTE_DEFERRED"}
    rows_to_insert = []
    now_iso = datetime.now().isoformat(timespec="seconds")

    for d in decisions:
        if d.action not in actionable_actions:
            continue
        cohort_name = _STRUCTURE_TO_COHORT_NAME.get(d.structure)
        gb = d.detail.get("gate_b", {}) if isinstance(d.detail, dict) else {}
        gd = d.detail.get("gate_d", {}) if isinstance(d.detail, dict) else {}
        gf = d.detail.get("gate_f", {}) if isinstance(d.detail, dict) else {}
        # For DEMOTE_DEFERRED, applied is always 0 by definition
        row_applied = 0 if d.action == "DEMOTE_DEFERRED" else (1 if applied else 0)
        rows_to_insert.append((
            run_date.isoformat(),
            d.ticker,
            d.structure,
            d.action,
            cohort_name,
            d.reason,
            gb.get("splits_positive") if gb else gf.get("splits_positive"),
            gb.get("valid_splits") if gb else gf.get("valid_splits"),
            gb.get("most_recent_mean"),
            gb.get("mean_threshold"),
            gb.get("most_recent_val_n"),
            d.detail.get("most_recent_p") if isinstance(d.detail, dict) else None,
            gd.get("max_year_fraction"),
            d.detail.get("n_liq_fails") if isinstance(d.detail, dict) else None,
            row_applied,
            safety_halt_reason,
            json.dumps(d.detail, default=str) if isinstance(d.detail, dict) else None,
            now_iso,
        ))

    if not rows_to_insert:
        return 0

    conn = sqlite3.connect(str(DB_PATH))
    try:
        init_cohort_changes_table(conn)
        # INSERT OR REPLACE so same-day re-runs (e.g., manual reruns) overwrite
        # the prior row rather than failing on PRIMARY KEY violation.
        conn.executemany("""
            INSERT OR REPLACE INTO cohort_changes
              (run_date, ticker, structure, action, cohort_name, reason,
               splits_positive, valid_splits, most_recent_mean, mean_threshold,
               most_recent_val_n, most_recent_p, max_year_fraction,
               n_consecutive_liq_fails, applied, safety_halt_reason,
               detail_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows_to_insert)
        conn.commit()
    finally:
        conn.close()
    return len(rows_to_insert)
