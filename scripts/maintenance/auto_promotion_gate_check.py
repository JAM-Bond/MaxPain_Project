"""Auto-promotion pipeline — gate evaluator (Stages 5a + 5b).

Given a list of walk-forward runner result dicts (one per (ticker, structure)),
applies the sealed promotion / demotion gates from
`docs/AUTO_PROMOTION_PIPELINE_PREREG.md` §3 and §4:

  Promote ALL of:
    A — liquidity (filtered upstream; presence in batch implies pass)
    B — walk-forward stability (≥3/4 splits positive AND most-recent mean ≥ threshold AND val_n ≥ 12)
    C — slip robustness (baked into walkforward; slip=0.50 default)
    D — concentration cap (no year > 50% of total |P/L|)
    E — BH-FDR q<0.10 on most-recent split p-value

  Demote ANY of (Gate H defers if open position):
    F — walk-forward turned negative (≤1/4 splits positive)
    G — liquidity collapse (3 consecutive nightly liquidity fails)

This module is a pure-function evaluator: no I/O. The nightly driver collects
inputs (walkforward results, current cohort membership, liquidity history)
and feeds them in.

Pre-reg: docs/AUTO_PROMOTION_PIPELINE_PREREG.md
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lib.auto_promotion import (  # noqa: E402
    benjamini_hochberg,
    evaluate_promotion_gate_b,
    evaluate_concentration_gate_d,
    evaluate_demotion_gate_f,
    has_open_position,
    DEMO_GATE_G_CONSECUTIVE_LIQ_FAILS,
)

log = logging.getLogger("auto_promotion_gate_check")


@dataclass
class GateDecision:
    """Outcome of evaluating one (ticker, structure) pair."""
    ticker: str
    structure: str
    action: str   # "PROMOTE" | "DEMOTE" | "DEMOTE_DEFERRED" | "NO_CHANGE" | "SKIP"
    reason: str
    detail: dict = field(default_factory=dict)


def _is_in_cohort(ticker: str, structure: str, cohorts: dict[str, list[str]]) -> bool:
    """Return True if the ticker is currently listed in the per-structure cohort.

    For inverted_fly we treat membership as union of SINGLE + PAIR.
    For zebra we treat membership as union of TIER1 + TIER2.
    """
    if structure == "bull_put":
        return ticker in cohorts.get("bull_put", [])
    if structure == "bear_call":
        return ticker in cohorts.get("bear_call", [])
    if structure == "inverted_fly":
        return (ticker in cohorts.get("inverted_fly_single", [])
                or ticker in cohorts.get("inverted_fly_pair", []))
    if structure == "zebra":
        return (ticker in cohorts.get("zebra_tier1", [])
                or ticker in cohorts.get("zebra_tier2", []))
    return False


def _consecutive_liquidity_fails(ticker: str,
                                   liquidity_history: list[pd.DataFrame]) -> int:
    """Count trailing nightly liquidity scans where this ticker was either
    failing OR absent. `liquidity_history` is ordered oldest → newest."""
    count = 0
    for scan in reversed(liquidity_history):
        if scan is None or scan.empty:
            count += 1
            continue
        row = scan[scan["ticker"] == ticker]
        if row.empty:
            count += 1
        elif not bool(row.iloc[0].get("passes", False)):
            count += 1
        else:
            break
    return count


def evaluate_batch(
    walkforward_results: list[dict],
    current_cohorts: dict[str, list[str]],
    liquidity_history: list[pd.DataFrame] | None = None,
    check_open_positions: bool = True,
) -> list[GateDecision]:
    """Evaluate a list of walk-forward runner results against the sealed gates.

    Args:
      walkforward_results: list of dicts from lib.walkforward_runner.run_walkforward
      current_cohorts: dict mapping cohort key → list of tickers (snapshot from
                       scripts.qualifier.gate_config). Keys we look at:
                       'bull_put', 'bear_call', 'inverted_fly_single',
                       'inverted_fly_pair', 'zebra_tier1', 'zebra_tier2'.
      liquidity_history: list of recent liquidity snapshot DataFrames (oldest
                          → newest). Used for Gate G demotion. If None, skip G.
      check_open_positions: if True, run Gate H deferral check via SQLite.
    """
    liquidity_history = liquidity_history or []

    # ── First pass: evaluate every (ticker, structure) against Gates B + D + F ──
    rows = []
    for r in walkforward_results:
        ticker = r["ticker"]
        structure = r["structure"]
        wf_rows = r.get("walkforward_rows", pd.DataFrame())
        per_year = r.get("per_year_pnl", {})
        most_recent_p = r.get("most_recent_p", float("nan"))
        in_cohort = _is_in_cohort(ticker, structure, current_cohorts)

        gate_b_pass, gate_b_detail = evaluate_promotion_gate_b(wf_rows, structure)
        gate_d_pass, gate_d_detail = evaluate_concentration_gate_d(per_year)
        gate_f_pass, gate_f_detail = evaluate_demotion_gate_f(wf_rows)

        rows.append({
            "ticker": ticker,
            "structure": structure,
            "in_cohort": in_cohort,
            "status": r.get("status", "ok"),
            "gate_b_pass": gate_b_pass,
            "gate_d_pass": gate_d_pass,
            "gate_f_pass": gate_f_pass,
            "most_recent_p": most_recent_p,
            "gate_b_detail": gate_b_detail,
            "gate_d_detail": gate_d_detail,
            "gate_f_detail": gate_f_detail,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return []

    # ── Gate E: BH-FDR on most_recent_p restricted to A+B+D survivors ──
    # Only candidates eligible for promotion enter the FDR pool:
    #   - not currently in cohort
    #   - Gate B + Gate D both pass
    candidate_mask = (~df["in_cohort"]) & df["gate_b_pass"] & df["gate_d_pass"]
    if candidate_mask.any():
        # BH-FDR is applied per-night across all structures combined to control
        # the night's total promotions at FDR q<0.10 (per pre-reg §3 Gate E).
        cand_pvals = df.loc[candidate_mask, "most_recent_p"].to_numpy()
        fdr_survives = benjamini_hochberg(cand_pvals)
        df["gate_e_pass"] = False
        df.loc[candidate_mask, "gate_e_pass"] = fdr_survives
    else:
        df["gate_e_pass"] = False

    # ── Translate to decisions ──
    decisions: list[GateDecision] = []
    for _, row in df.iterrows():
        ticker = row["ticker"]
        structure = row["structure"]
        in_cohort = bool(row["in_cohort"])

        # Defensive: if walk-forward status was not OK, never promote, never demote
        if row["status"] != "ok":
            decisions.append(GateDecision(
                ticker=ticker, structure=structure, action="SKIP",
                reason=f"walkforward status={row['status']}",
                detail={"status": row["status"]},
            ))
            continue

        # ── Demotion path (only for names currently in cohort) ──
        if in_cohort:
            n_fails = _consecutive_liquidity_fails(ticker, liquidity_history)
            gate_g_pass = n_fails >= DEMO_GATE_G_CONSECUTIVE_LIQ_FAILS

            # Bear_call exemption from Gate F (added 2026-05-19):
            # bear_call entries are H1-gated in live deployment (SPY < 200-DMA
            # + IVR > 0.5). The walk-forward runner evaluates un-gated cycles,
            # which look structurally negative for bear_call during bull tape.
            # Demoting on un-gated walk-forward removes names whose live
            # behavior is fine. Skip Gate F for bear_call until the runner
            # supports H1-conditioned evaluation. Manual demotion via
            # auto_promotion_gate_config_writer.py --demote still possible.
            # Gate G (liquidity collapse) remains active for bear_call.
            gate_f_active = row["gate_f_pass"] and structure != "bear_call"

            demote_reason = None
            if gate_f_active:
                gf = row["gate_f_detail"]
                demote_reason = (
                    f"Gate F: {gf.get('splits_positive', '?')}/"
                    f"{gf.get('valid_splits', '?')} valid splits positive "
                    f"(threshold ≤ {gf.get('threshold', '?')})"
                )
            elif gate_g_pass:
                demote_reason = (f"Gate G: {n_fails} consecutive liquidity-fail nights "
                                  f"(threshold {DEMO_GATE_G_CONSECUTIVE_LIQ_FAILS})")

            if demote_reason:
                # Gate H — open-position deferral
                deferred = False
                if check_open_positions:
                    try:
                        deferred = has_open_position(ticker, structure)
                    except Exception as e:
                        log.warning("Gate H check failed for %s/%s: %s — "
                                    "defaulting to defer", ticker, structure, e)
                        deferred = True

                if deferred:
                    decisions.append(GateDecision(
                        ticker=ticker, structure=structure,
                        action="DEMOTE_DEFERRED",
                        reason=f"{demote_reason}; Gate H deferred (open position)",
                        detail={
                            "gate_f": row["gate_f_detail"],
                            "n_liq_fails": n_fails,
                        },
                    ))
                else:
                    decisions.append(GateDecision(
                        ticker=ticker, structure=structure, action="DEMOTE",
                        reason=demote_reason,
                        detail={
                            "gate_f": row["gate_f_detail"],
                            "n_liq_fails": n_fails,
                        },
                    ))
                continue
            # In cohort but no demotion trigger
            decisions.append(GateDecision(
                ticker=ticker, structure=structure, action="NO_CHANGE",
                reason="in cohort; no demotion trigger",
                detail={
                    "gate_b": row["gate_b_detail"],
                    "gate_f": row["gate_f_detail"],
                    "n_liq_fails": n_fails,
                },
            ))
            continue

        # ── Promotion path (only for names NOT currently in cohort) ──
        if (row["gate_b_pass"] and row["gate_d_pass"] and row["gate_e_pass"]):
            decisions.append(GateDecision(
                ticker=ticker, structure=structure, action="PROMOTE",
                reason=(
                    f"Gates A+B+D+E pass: "
                    f"{row['gate_b_detail'].get('splits_positive')}/4 splits, "
                    f"mean={row['gate_b_detail'].get('most_recent_mean'):.2f}, "
                    f"val_n={row['gate_b_detail'].get('most_recent_val_n')}, "
                    f"p={row['most_recent_p']:.4f}"
                ),
                detail={
                    "gate_b": row["gate_b_detail"],
                    "gate_d": row["gate_d_detail"],
                    "most_recent_p": row["most_recent_p"],
                },
            ))
        else:
            fails = []
            if not row["gate_b_pass"]:
                fails.append("B")
            if not row["gate_d_pass"]:
                fails.append("D")
            if not row["gate_e_pass"]:
                fails.append("E")
            decisions.append(GateDecision(
                ticker=ticker, structure=structure, action="NO_CHANGE",
                reason=f"not in cohort; fails gate(s) {','.join(fails)}",
                detail={
                    "gate_b": row["gate_b_detail"],
                    "gate_d": row["gate_d_detail"],
                    "most_recent_p": row["most_recent_p"],
                },
            ))
    return decisions


def decisions_to_dataframe(decisions: list[GateDecision]) -> pd.DataFrame:
    """Flatten decisions for parquet persistence in the audit log."""
    rows = []
    for d in decisions:
        rows.append({
            "ticker": d.ticker,
            "structure": d.structure,
            "action": d.action,
            "reason": d.reason,
            # Stringify the detail dict; full reconstruction is best-effort
            "detail_repr": repr(d.detail),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    # CLI for a one-shot smoke test against the current cohort.
    import argparse
    from lib.walkforward_runner import run_walkforward
    from scripts.qualifier import gate_config

    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="+", required=True)
    ap.add_argument("--structures", nargs="+", default=["bull_put"])
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    results = []
    for t in args.tickers:
        for s in args.structures:
            results.append(run_walkforward(t, s))

    current_cohorts = {
        "bull_put": gate_config.COHORT_BULL_PUT,
        "bear_call": gate_config.COHORT_BEAR_CALL,
        "inverted_fly_single": gate_config.COHORT_INVERTED_FLY_SINGLE,
        "inverted_fly_pair": gate_config.COHORT_INVERTED_FLY_PAIR,
        "zebra_tier1": gate_config.COHORT_ZEBRA_TIER1,
        "zebra_tier2": gate_config.COHORT_ZEBRA_TIER2,
    }

    decisions = evaluate_batch(results, current_cohorts,
                                liquidity_history=[], check_open_positions=False)
    for d in decisions:
        print(f"  {d.action:18s} {d.ticker:6s} {d.structure:14s} — {d.reason}")
