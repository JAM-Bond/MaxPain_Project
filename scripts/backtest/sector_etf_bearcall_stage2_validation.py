"""Sector-ETF Stage-2 bear-call entry trigger — validation runner.

Implements the sealed methodology from
`docs/SECTOR_ETF_STAGE2_BEARCALL_PREREG.md` §5 and §6.

Inputs:
  - data/profile/bear_call_moneyness_results.parquet (cycle-level bear_call
    P&L; OTM/ATM/ITM at 45-DTE entry, slip=0.50)
  - lib/sector_etf_stage2.py (signal definition)

Outputs:
  - data/profile/sector_etf_bearcall_stage2_validation.parquet
      Per-gate verdicts + per-sector cell rollup
  - reports/sector_etf_bearcall_stage2_validation_YYYY-MM-DD.md
      Human-readable summary including GO/NO-GO decision

Reproducibility: rerun anytime to get fresh results as ORATS data grows.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.sector_etf_stage2 import (  # noqa: E402
    SECTOR_ETF_COHORT,
    stage2_series,
)

CYCLES_IN = ROOT / "data/profile/bear_call_moneyness_results.parquet"
OUT_PARQUET = ROOT / "data/profile/sector_etf_bearcall_stage2_validation.parquet"
REPORTS_DIR = ROOT / "reports"

# Sealed gate thresholds (pre-reg §6)
GATE_A_MIN_MEAN_PER_SHARE = 0.02
GATE_B_MIN_WIN_RATE = 0.78
GATE_C_MIN_POSITIVE_SECTORS = 6
GATE_C_WORST_SECTOR_FLOOR = -0.10
GATE_D_MIN_WINDOWS = 3
GATE_E_MIN_CYCLES = 100

# Sealed walk-forward windows (4-split convention, pre-reg §6 Gate D)
WALK_FORWARD_WINDOWS = [
    ("2021-01-01", "2023-12-31"),
    ("2022-01-01", "2024-12-31"),
    ("2023-01-01", "2025-12-31"),
    ("2024-01-01", "2026-12-31"),
]


def assemble_cycles_with_signal() -> pd.DataFrame:
    """Load bear_call OTM cycles for the 12 cohort ETFs and tag each cycle
    with whether Stage-2 was active at its entry_date."""
    cycles = pd.read_parquet(CYCLES_IN)
    cycles["entry_date"] = pd.to_datetime(cycles["entry_date"])
    sub = cycles[
        cycles["ticker"].isin(SECTOR_ETF_COHORT)
        & (cycles["moneyness"] == "OTM")
    ].copy()
    print(f"Sector-ETF OTM cycles: {len(sub):,} across "
          f"{sub['ticker'].nunique()} of {len(SECTOR_ETF_COHORT)} cohort tickers")

    tagged_frames = []
    for etf in SECTOR_ETF_COHORT:
        etf_cycles = sub[sub["ticker"] == etf].copy()
        if etf_cycles.empty:
            print(f"  {etf}: 0 cycles (skipped — no by_ticker bear_call data)")
            continue
        ser = stage2_series(etf)
        if ser is None:
            print(f"  {etf}: stage2_series returned None — skipping")
            continue
        flag = ser[["stage2_active"]]
        etf_cycles = etf_cycles.merge(flag, left_on="entry_date",
                                        right_index=True, how="left")
        etf_cycles["stage2_active"] = etf_cycles["stage2_active"].fillna(0).astype(int)
        tagged_frames.append(etf_cycles)
    return pd.concat(tagged_frames, ignore_index=True) if tagged_frames else pd.DataFrame()


# ─── Gate evaluations ────────────────────────────────────────────────────

def evaluate_gate_a(trigger_cycles: pd.DataFrame) -> tuple[bool, dict]:
    """Gate A — pooled mean per-share P/L ≥ +$0.02."""
    if trigger_cycles.empty:
        return False, {"mean": np.nan, "threshold": GATE_A_MIN_MEAN_PER_SHARE,
                       "n": 0, "reason": "no trigger cycles"}
    mean = float(trigger_cycles["mgd50_pnl"].mean())
    return (mean >= GATE_A_MIN_MEAN_PER_SHARE,
            {"mean_per_share": round(mean, 4),
             "threshold": GATE_A_MIN_MEAN_PER_SHARE,
             "n": int(len(trigger_cycles))})


def evaluate_gate_b(trigger_cycles: pd.DataFrame) -> tuple[bool, dict]:
    """Gate B — pooled win rate ≥ 78%."""
    if trigger_cycles.empty:
        return False, {"win_rate": np.nan, "threshold": GATE_B_MIN_WIN_RATE,
                       "n": 0}
    wr = float((trigger_cycles["mgd50_pnl"] > 0).mean())
    return (wr >= GATE_B_MIN_WIN_RATE,
            {"win_rate": round(wr, 4),
             "threshold": GATE_B_MIN_WIN_RATE,
             "n": int(len(trigger_cycles))})


def evaluate_gate_c(trigger_cycles: pd.DataFrame) -> tuple[bool, dict]:
    """Gate C — ≥6 of 12 sectors positive AND worst sector ≥ -$0.10/share.

    Sectors with zero trigger cycles count as non-positive (don't help, don't fail).
    """
    per_sector = []
    for etf in SECTOR_ETF_COHORT:
        sub = trigger_cycles[trigger_cycles["ticker"] == etf]
        if sub.empty:
            per_sector.append({"ticker": etf, "n": 0, "mean": np.nan})
        else:
            per_sector.append({"ticker": etf,
                                "n": int(len(sub)),
                                "mean": round(float(sub["mgd50_pnl"].mean()), 4)})
    df = pd.DataFrame(per_sector)
    has_cycles = df[df["n"] > 0]
    n_positive = int((has_cycles["mean"] > 0).sum())
    worst = float(has_cycles["mean"].min()) if not has_cycles.empty else np.nan
    pass_count = n_positive >= GATE_C_MIN_POSITIVE_SECTORS
    pass_floor = (np.isnan(worst) or worst >= GATE_C_WORST_SECTOR_FLOOR)
    passes = bool(pass_count and pass_floor)
    return passes, {
        "n_positive_sectors": n_positive,
        "min_positive": GATE_C_MIN_POSITIVE_SECTORS,
        "worst_sector_mean": worst,
        "worst_sector_floor": GATE_C_WORST_SECTOR_FLOOR,
        "n_sectors_with_cycles": int(len(has_cycles)),
        "per_sector": per_sector,
    }


def evaluate_gate_d(trigger_cycles: pd.DataFrame) -> tuple[bool, dict]:
    """Gate D — walk-forward stability across 4 standard windows."""
    window_results = []
    n_pass = 0
    for start, end in WALK_FORWARD_WINDOWS:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        sub = trigger_cycles[(trigger_cycles["entry_date"] >= s)
                                & (trigger_cycles["entry_date"] <= e)]
        if sub.empty:
            window_results.append({"window": f"{start}..{end}",
                                    "n": 0, "mean": np.nan, "passes": False})
            continue
        m = float(sub["mgd50_pnl"].mean())
        p = m > 0
        if p:
            n_pass += 1
        window_results.append({"window": f"{start}..{end}",
                                "n": int(len(sub)),
                                "mean": round(m, 4),
                                "passes": bool(p)})
    return (n_pass >= GATE_D_MIN_WINDOWS,
            {"n_windows_pass": n_pass,
             "min_windows": GATE_D_MIN_WINDOWS,
             "window_results": window_results})


def evaluate_gate_e(trigger_cycles: pd.DataFrame) -> tuple[bool, dict]:
    """Gate E — ≥100 total trigger cycles across all 12 ETFs."""
    n = int(len(trigger_cycles))
    return (n >= GATE_E_MIN_CYCLES,
            {"n_cycles": n, "threshold": GATE_E_MIN_CYCLES})


# ─── Main ────────────────────────────────────────────────────────────────

def run_validation() -> dict:
    print("=" * 80)
    print("Sector-ETF Stage-2 Bear-Call — Validation (pre-reg sealed 2026-05-18)")
    print("=" * 80)
    df = assemble_cycles_with_signal()
    if df.empty:
        return {"verdict": "FAIL", "reason": "no cycles assembled"}

    triggers = df[df["stage2_active"] == 1].copy()
    print(f"\nTotal trigger cycles (Stage-2 ON at entry): {len(triggers):,}")
    print(f"Baseline (ALL sector-ETF OTM cycles, gated + ungated): {len(df):,}")
    baseline_mean = float(df["mgd50_pnl"].mean())
    baseline_wr = float((df["mgd50_pnl"] > 0).mean())
    print(f"Baseline mean P/L:  ${baseline_mean:+.4f}/sh  win rate {baseline_wr:.3f}")

    # Evaluate the five sealed gates
    results = {}
    print()
    for gate_name, evaluator in [
        ("A", evaluate_gate_a),
        ("B", evaluate_gate_b),
        ("C", evaluate_gate_c),
        ("D", evaluate_gate_d),
        ("E", evaluate_gate_e),
    ]:
        passed, detail = evaluator(triggers)
        results[f"gate_{gate_name.lower()}"] = {"passed": passed, **detail}
        flag = "✓ PASS" if passed else "✗ FAIL"
        print(f"  Gate {gate_name}: {flag}  {detail}")

    all_pass = all(results[f"gate_{g}"]["passed"] for g in "abcde")
    results["verdict"] = "PROMOTE" if all_pass else "REJECT"
    results["trigger_n"] = int(len(triggers))
    results["baseline_n"] = int(len(df))
    results["baseline_mean"] = round(baseline_mean, 4)
    results["baseline_win_rate"] = round(baseline_wr, 4)
    results["trigger_mean"] = round(float(triggers["mgd50_pnl"].mean()), 4) if not triggers.empty else None
    results["trigger_win_rate"] = round(float((triggers["mgd50_pnl"] > 0).mean()), 4) if not triggers.empty else None
    results["per_sector"] = results["gate_c"].get("per_sector", [])

    print()
    print("=" * 80)
    print(f"VERDICT: {results['verdict']}")
    print("=" * 80)

    return results


def write_outputs(results: dict, run_date: date) -> None:
    # Flatten for parquet — one row per gate + a verdict row
    rows = []
    for g in "abcde":
        det = results[f"gate_{g}"]
        rows.append({
            "gate": g.upper(),
            "passed": det["passed"],
            "summary": str({k: v for k, v in det.items()
                              if k not in ("per_sector", "window_results")}),
        })
    rows.append({
        "gate": "VERDICT",
        "passed": results["verdict"] == "PROMOTE",
        "summary": (f"verdict={results['verdict']}; "
                     f"trigger_n={results['trigger_n']}; "
                     f"trigger_mean={results['trigger_mean']}; "
                     f"trigger_win={results['trigger_win_rate']}"),
    })
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(OUT_PARQUET, index=False)
    print(f"\nWrote {OUT_PARQUET}")

    # Markdown report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"sector_etf_bearcall_stage2_validation_{run_date.isoformat()}.md"
    lines = [
        f"# Sector-ETF Stage-2 Bear-Call — Validation Report ({run_date.isoformat()})",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        "Pre-reg: `docs/SECTOR_ETF_STAGE2_BEARCALL_PREREG.md` (sealed 2026-05-18)",
        "Validation code: `scripts/backtest/sector_etf_bearcall_stage2_validation.py`",
        "",
        "## Headline",
        "",
        f"- Baseline: N={results['baseline_n']:,} sector-ETF OTM bear-call cycles · "
        f"mean ${results['baseline_mean']:+.4f}/sh · win {results['baseline_win_rate']:.3f}",
        f"- Trigger cycles (Stage-2 active at entry): N={results['trigger_n']} · "
        f"mean ${results['trigger_mean']:+.4f}/sh · win {results['trigger_win_rate']:.3f}",
        "",
        "## Gate verdicts",
        "",
        "| Gate | Threshold | Observed | Pass |",
        "|---|---|---|---|",
    ]
    gate_a = results["gate_a"]
    lines.append(f"| A — pooled mean ≥ +$0.02/sh | +${GATE_A_MIN_MEAN_PER_SHARE} | "
                  f"${gate_a.get('mean_per_share', 0):+.4f} (N={gate_a.get('n', 0)}) | "
                  f"{'✓' if gate_a['passed'] else '✗'} |")
    gate_b = results["gate_b"]
    lines.append(f"| B — win rate ≥ 78% | {GATE_B_MIN_WIN_RATE:.0%} | "
                  f"{gate_b.get('win_rate', 0):.3f} (N={gate_b.get('n', 0)}) | "
                  f"{'✓' if gate_b['passed'] else '✗'} |")
    gate_c = results["gate_c"]
    lines.append(f"| C — ≥6/12 sectors positive + worst ≥ -$0.10 | ≥6 + worst ≥ -$0.10 | "
                  f"{gate_c['n_positive_sectors']} positive of {gate_c['n_sectors_with_cycles']} "
                  f"with cycles · worst = ${gate_c.get('worst_sector_mean', 0):+.4f} | "
                  f"{'✓' if gate_c['passed'] else '✗'} |")
    gate_d = results["gate_d"]
    lines.append(f"| D — walk-forward mean >0 in ≥3/4 windows | ≥3 windows | "
                  f"{gate_d['n_windows_pass']}/4 windows | "
                  f"{'✓' if gate_d['passed'] else '✗'} |")
    gate_e = results["gate_e"]
    lines.append(f"| E — ≥100 trigger cycles | 100 | "
                  f"{gate_e['n_cycles']} | "
                  f"{'✓' if gate_e['passed'] else '✗'} |")
    lines.append("")

    # Per-sector breakdown
    lines.append("## Per-sector trigger cells")
    lines.append("")
    lines.append("| Sector | Trigger N | Mean per-share | Notes |")
    lines.append("|---|---|---|---|")
    for row in results["per_sector"]:
        notes = "no trigger cycles" if row["n"] == 0 else ""
        mean_s = "—" if row["n"] == 0 else f"${row['mean']:+.4f}"
        lines.append(f"| {row['ticker']} | {row['n']} | {mean_s} | {notes} |")
    lines.append("")

    # Walk-forward windows
    lines.append("## Walk-forward windows (Gate D)")
    lines.append("")
    lines.append("| Window | Cycles | Mean | Pass |")
    lines.append("|---|---|---|---|")
    for wr in gate_d["window_results"]:
        mean_s = "—" if wr["n"] == 0 else f"${wr['mean']:+.4f}"
        lines.append(f"| {wr['window']} | {wr['n']} | {mean_s} | "
                      f"{'✓' if wr['passes'] else '✗'} |")
    lines.append("")

    # Interpretation
    lines.append("## Interpretation")
    lines.append("")
    if results["verdict"] == "PROMOTE":
        lines.append("All five sealed gates pass. The Stage-2 break trigger promotes "
                      "to live deployment as an additive bear-call entry path on the "
                      "12 sector ETFs in the cohort, independent of the H1 broad-market gate.")
        lines.append("")
        lines.append("Integration steps per pre-reg §7:")
        lines.append("1. Add `COHORT_BEAR_CALL_STAGE2_SECTOR_ETF` to `scripts/qualifier/gate_config.py`")
        lines.append("2. Add new bear-call verdict path in `scripts/qualifier/cycle_qualifier.py`")
        lines.append("3. Add SECTOR-STAGE2 annotation in `scripts/monitor/daily_alert.py`")
        lines.append("4. Queue TRADING_PLAN.rtf v2.5 update")
    else:
        failed = [g.upper() for g in "abcde"
                   if not results[f"gate_{g}"]["passed"]]
        lines.append(f"Gate(s) **{', '.join(failed)}** failed. No promotion per pre-reg §9.")
        lines.append("")
        lines.append("Per §9: no immediate variant retest is permitted. A future variant "
                      "pre-reg requires a distinct conceptual rationale + sealing BEFORE "
                      "looking at variant results.")

    lines.append("")
    lines.append("## Cross-references")
    lines.append("")
    lines.append("- `docs/SECTOR_ETF_STAGE2_BEARCALL_PREREG.md` — sealed pre-reg")
    lines.append("- `lib/sector_etf_stage2.py` — signal definition")
    lines.append("- `data/profile/sector_etf_bearcall_stage2_validation.parquet` — per-gate parquet")
    lines.append("- `data/profile/bear_call_moneyness_results.parquet` — cycle-level input")
    lines.append("- `project_sector_etf_stage2_bearcall_prereg.md` — memory (pre-seal)")
    report_path.write_text("\n".join(lines))
    print(f"Wrote {report_path}")


def main() -> int:
    results = run_validation()
    write_outputs(results, date.today())
    return 0 if results["verdict"] in ("PROMOTE", "REJECT") else 1


if __name__ == "__main__":
    sys.exit(main())
