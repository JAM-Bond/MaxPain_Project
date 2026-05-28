"""Bear-call 200-DMA cross-exit rule — sealed-gate validation.

Implements the sealed methodology from
`docs/BEAR_CALL_MA200_CROSS_EXIT_PREREG.md` §4-§6:

  Gate A — Combined-rule mean P/L vs mgd50 baseline in the cycles where
            cross-exit FIRED: improvement ≥ +$0.20/share
  Gate B — No harm to the cycles where cross-exit did NOT fire (within
            entered-below cohort): combined ≥ baseline − $0.05/share
  Gate C — Walk-forward: improvement ≥ +$0.10/share in ≥3/4 standard
            validation windows (2021-23, 22-24, 23-25, 24-26)
  Gate D — Concentration: no single calendar year > 50% of total |improvement|
  Gate E — Sample: ≥500 cycles where cross-exit was the binding constraint

Verdict: PROMOTE if all five pass; REJECT otherwise.

Inputs: data/profile/bear_call_ma200_cross_results.parquet
Outputs:
  data/profile/bear_call_ma200_cross_validation.parquet  (per-gate verdicts)
  reports/bear_call_ma200_cross_validation_YYYY-MM-DD.md
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

RESULTS_IN = ROOT / "data/profile/bear_call_ma200_cross_results.parquet"
OUT_PARQUET = ROOT / "data/profile/bear_call_ma200_cross_validation.parquet"
REPORTS_DIR = ROOT / "reports"

# Sealed gate thresholds (pre-reg §5) — identical to bull-put symmetric pre-reg
GATE_A_MIN_IMPROVEMENT = 0.20         # $/share in firing cohort
GATE_B_MAX_HARM = -0.05               # $/share in non-firing cohort
GATE_C_MIN_WINDOW_IMPROVEMENT = 0.10  # $/share in each window
GATE_C_MIN_WINDOWS = 3                # of 4
GATE_D_MAX_YEAR_FRACTION = 0.50
GATE_E_MIN_FIRING = 500

WALK_FORWARD_WINDOWS = [
    ("2021-01-01", "2023-12-31"),
    ("2022-01-01", "2024-12-31"),
    ("2023-01-01", "2025-12-31"),
    ("2024-01-01", "2026-12-31"),
]


def main() -> int:
    if not RESULTS_IN.exists():
        print(f"ERROR: input missing — {RESULTS_IN}")
        return 1
    df = pd.read_parquet(RESULTS_IN)
    df["entry_date"] = pd.to_datetime(df["entry_date"])

    print("=" * 86)
    print(f"Bear-Call 200-DMA Cross-Exit Validation — {date.today().isoformat()}")
    print("=" * 86)
    print(f"Loaded {len(df):,} cycle rows total")

    # Cohort restriction per pre-reg §4: OTM, entered BELOW 200-DMA
    otm_below = df[(df["moneyness"] == "OTM")
                     & (df["entry_below_ma200"] == 1)].copy()
    print(f"OTM cycles entered below 200-DMA: {len(otm_below):,}")

    fired = otm_below[otm_below["cross_exit_triggered"] == 1]
    not_fired = otm_below[otm_below["cross_exit_triggered"] == 0]
    print(f"  cross-exit fired:    {len(fired):,} ({len(fired)/len(otm_below)*100:.1f}%)")
    print(f"  cross-exit not fired: {len(not_fired):,}")

    # Cycles where cross-exit was the BINDING CONSTRAINT
    binding = otm_below[otm_below["combined_exit_type"] == "cross"]
    print(f"  cross was the binding exit (fired earlier than mgd50): {len(binding):,}")
    print()

    # ── Gate A: improvement in the firing cohort ──
    if fired.empty:
        gate_a_pass = False
        gate_a_detail = {"reason": "no firing cycles", "improvement": np.nan,
                         "n": 0}
    else:
        baseline_mean = float(fired["mgd50_pnl"].mean())
        combined_mean = float(fired["combined_pnl"].mean())
        improvement = combined_mean - baseline_mean
        gate_a_pass = improvement >= GATE_A_MIN_IMPROVEMENT
        gate_a_detail = {
            "n_firing": int(len(fired)),
            "baseline_mean_mgd50": round(baseline_mean, 4),
            "combined_mean": round(combined_mean, 4),
            "improvement_per_share": round(improvement, 4),
            "threshold": GATE_A_MIN_IMPROVEMENT,
            "passes": bool(gate_a_pass),
        }

    # ── Gate B: no harm in the non-firing cohort ──
    if not_fired.empty:
        gate_b_pass = True
        gate_b_detail = {"reason": "no non-firing cycles in scope"}
    else:
        baseline_mean = float(not_fired["mgd50_pnl"].mean())
        combined_mean = float(not_fired["combined_pnl"].mean())
        delta = combined_mean - baseline_mean
        gate_b_pass = delta >= GATE_B_MAX_HARM
        gate_b_detail = {
            "n_non_firing": int(len(not_fired)),
            "baseline_mean_mgd50": round(baseline_mean, 4),
            "combined_mean": round(combined_mean, 4),
            "delta_per_share": round(delta, 6),
            "max_harm_threshold": GATE_B_MAX_HARM,
            "passes": bool(gate_b_pass),
        }

    # ── Gate C: walk-forward stability ──
    window_results = []
    n_pass = 0
    for start, end in WALK_FORWARD_WINDOWS:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        win = fired[(fired["entry_date"] >= s) & (fired["entry_date"] <= e)]
        if win.empty:
            window_results.append({"window": f"{start}..{end}", "n": 0,
                                    "improvement": np.nan, "passes": False})
            continue
        b = float(win["mgd50_pnl"].mean())
        c = float(win["combined_pnl"].mean())
        improvement = c - b
        win_pass = improvement >= GATE_C_MIN_WINDOW_IMPROVEMENT
        if win_pass:
            n_pass += 1
        window_results.append({
            "window": f"{start}..{end}",
            "n": int(len(win)),
            "improvement": round(improvement, 4),
            "passes": bool(win_pass),
        })
    gate_c_pass = n_pass >= GATE_C_MIN_WINDOWS
    gate_c_detail = {
        "n_windows_pass": n_pass,
        "min_windows": GATE_C_MIN_WINDOWS,
        "window_results": window_results,
        "passes": bool(gate_c_pass),
    }

    # ── Gate D: concentration cap ──
    if fired.empty:
        gate_d_pass = False
        gate_d_detail = {"reason": "no firing cycles", "passes": False}
    else:
        per_row_improvement = (fired["combined_pnl"]
                                  - fired["mgd50_pnl"]).abs()
        per_year_abs = (fired.assign(year=fired["entry_date"].dt.year,
                                         improvement_abs=per_row_improvement)
                              .groupby("year")["improvement_abs"].sum())
        total_abs = float(per_year_abs.sum())
        if total_abs == 0:
            gate_d_pass = True
            gate_d_detail = {"reason": "zero |improvement|", "passes": True}
        else:
            year_fracs = (per_year_abs / total_abs).to_dict()
            max_year_frac = max(year_fracs.values())
            gate_d_pass = max_year_frac <= GATE_D_MAX_YEAR_FRACTION
            gate_d_detail = {
                "year_fractions": {int(y): round(f, 4) for y, f in year_fracs.items()},
                "max_year_fraction": round(max_year_frac, 4),
                "threshold": GATE_D_MAX_YEAR_FRACTION,
                "passes": bool(gate_d_pass),
            }

    # ── Gate E: sample adequacy ──
    gate_e_pass = len(binding) >= GATE_E_MIN_FIRING
    gate_e_detail = {
        "n_binding": int(len(binding)),
        "threshold": GATE_E_MIN_FIRING,
        "passes": bool(gate_e_pass),
    }

    # ── Headline + verdict ──
    print("Per-gate verdicts:")
    for name, passed, detail in [
        ("A — Improvement in firing cohort",    gate_a_pass, gate_a_detail),
        ("B — No harm in non-firing cohort",    gate_b_pass, gate_b_detail),
        ("C — Walk-forward stability",           gate_c_pass, gate_c_detail),
        ("D — Year-concentration cap",           gate_d_pass, gate_d_detail),
        ("E — Sample adequacy",                  gate_e_pass, gate_e_detail),
    ]:
        flag = "✓ PASS" if passed else "✗ FAIL"
        print(f"  Gate {name:42s} : {flag}")
        for k, v in detail.items():
            if k in ("window_results", "year_fractions"):
                continue
            print(f"       {k}: {v}")
        if "window_results" in detail:
            for wr in detail["window_results"]:
                wflag = "✓" if wr["passes"] else "✗"
                print(f"       {wflag} {wr['window']}  n={wr['n']:>5d}  improvement=${wr['improvement']}")
        if "year_fractions" in detail:
            for y, f in sorted(detail["year_fractions"].items()):
                print(f"       year {y}: {f*100:.1f}% of total")
        print()

    all_pass = all([gate_a_pass, gate_b_pass, gate_c_pass, gate_d_pass, gate_e_pass])
    verdict = "PROMOTE" if all_pass else "REJECT"

    print("=" * 86)
    print(f"VERDICT: {verdict}")
    print("=" * 86)

    # Persist parquet
    rows = []
    for name, passed, detail in [
        ("A", gate_a_pass, gate_a_detail),
        ("B", gate_b_pass, gate_b_detail),
        ("C", gate_c_pass, gate_c_detail),
        ("D", gate_d_pass, gate_d_detail),
        ("E", gate_e_pass, gate_e_detail),
    ]:
        rows.append({
            "gate": name,
            "passes": passed,
            "summary": str({k: v for k, v in detail.items()
                              if k not in ("window_results", "year_fractions")}),
        })
    rows.append({"gate": "VERDICT", "passes": all_pass,
                  "summary": f"verdict={verdict}"})
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(OUT_PARQUET, index=False)
    print(f"\nWrote {OUT_PARQUET}")

    # Markdown report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"bear_call_ma200_cross_validation_{date.today().isoformat()}.md"
    lines = [
        f"# Bear-Call 200-DMA Cross-Exit — Validation Report ({date.today().isoformat()})",
        "",
        f"**Verdict: {verdict}**",
        "",
        "Pre-reg: `docs/BEAR_CALL_MA200_CROSS_EXIT_PREREG.md` (sealed 2026-05-20)",
        "Simulation: `scripts/backtest/bear_call_ma200_cross_simulation.py`",
        "Validation: `scripts/backtest/bear_call_ma200_cross_validation.py`",
        "",
        "## Cohort",
        "",
        f"- Universe: OTM bear-call cycles entered below own 200-DMA: **{len(otm_below):,}**",
        f"- Cross-exit fired: **{len(fired):,}** ({len(fired)/len(otm_below)*100:.1f}%)",
        f"- Cross was the binding exit (fired earlier than mgd50): **{len(binding):,}**",
        "",
        "## Per-gate verdicts",
        "",
        "| Gate | Threshold | Observed | Pass |",
        "|---|---|---|---|",
        f"| A — improvement in firing cohort | ≥ +${GATE_A_MIN_IMPROVEMENT}/sh | "
        f"${gate_a_detail.get('improvement_per_share', 0):+.4f}/sh | "
        f"{'✓' if gate_a_pass else '✗'} |",
        f"| B — no harm in non-firing cohort | ≥ ${GATE_B_MAX_HARM}/sh | "
        f"${gate_b_detail.get('delta_per_share', 0):+.4f}/sh | "
        f"{'✓' if gate_b_pass else '✗'} |",
        f"| C — walk-forward (≥3/4 windows) | ≥3 | "
        f"{gate_c_detail['n_windows_pass']}/4 | "
        f"{'✓' if gate_c_pass else '✗'} |",
        f"| D — year-concentration cap | max ≤ {GATE_D_MAX_YEAR_FRACTION:.0%} | "
        f"{gate_d_detail.get('max_year_fraction', 0)*100:.1f}% | "
        f"{'✓' if gate_d_pass else '✗'} |",
        f"| E — sample adequacy | ≥ {GATE_E_MIN_FIRING} binding | "
        f"{gate_e_detail['n_binding']} | "
        f"{'✓' if gate_e_pass else '✗'} |",
        "",
        "## Walk-forward windows (Gate C)",
        "",
        "| Window | N firing | Improvement/sh | Pass |",
        "|---|---|---|---|",
    ]
    for wr in gate_c_detail.get("window_results", []):
        imp = "—" if pd.isna(wr["improvement"]) else f"${wr['improvement']:+.4f}"
        lines.append(f"| {wr['window']} | {wr['n']} | {imp} | "
                      f"{'✓' if wr['passes'] else '✗'} |")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    if all_pass:
        lines.append("All five sealed gates pass. The 200-DMA cross-exit rule "
                     "promotes to live deployment as a management cue on open "
                     "bear-call positions, layered atop the existing exit stack "
                     "(mgd50 + T-21 + stop-limit + H1 entry gate).")
        lines.append("")
        lines.append("The vol-asymmetry hypothesis (rallies compress vol → cheap exit) "
                     "is empirically supported by this result, in contrast to the bull-put "
                     "symmetric rejection where vol-peak-on-cross-down made the exit expensive.")
        lines.append("")
        lines.append("Integration per pre-reg §6:")
        lines.append("1. Add management cue in `scripts/qualifier/cycle_qualifier.py`")
        lines.append("2. Add `MA200_CROSS_EXIT` annotation in `scripts/monitor/daily_alert.py`")
        lines.append("3. Queue TRADING_PLAN.rtf v2.5 paragraph")
    else:
        failed = [g for g, p in [("A", gate_a_pass), ("B", gate_b_pass),
                                    ("C", gate_c_pass), ("D", gate_d_pass),
                                    ("E", gate_e_pass)] if not p]
        lines.append(f"Gate(s) **{', '.join(failed)}** failed. No promotion per pre-reg §8.")
        lines.append("")
        lines.append("The vol-asymmetry hypothesis is empirically falsified. The bear-call "
                     "cross-exit joins the 'real Stage-2 forensic signal, no tradeable mechanic' "
                     "pile alongside the bull-put rejection (now 2-deep for MA-cross-exit).")
        lines.append("")
        lines.append("Per §8: no immediate variant retest is permitted. A future variant "
                      "pre-reg requires a fresh conceptual rationale, not a tweak.")
    lines.append("")
    lines.append("## Cross-references")
    lines.append("")
    lines.append("- `docs/BEAR_CALL_MA200_CROSS_EXIT_PREREG.md` — sealed pre-reg")
    lines.append("- `data/profile/bear_call_ma200_cross_results.parquet` — per-cycle simulation")
    lines.append("- `data/profile/bear_call_ma200_cross_validation.parquet` — per-gate output")
    lines.append("- `scripts/backtest/bear_call_ma_cross_during_hold.py` — Phase 1 exploratory")
    lines.append("- `project_bull_put_ma200_cross_rejected.md` — symmetric bull-put rejection")
    report_path.write_text("\n".join(lines))
    print(f"Wrote {report_path}")

    return 0 if verdict in ("PROMOTE", "REJECT") else 1


if __name__ == "__main__":
    sys.exit(main())
