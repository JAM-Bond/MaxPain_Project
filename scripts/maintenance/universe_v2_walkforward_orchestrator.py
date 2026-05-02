#!/usr/bin/env python3.11
"""
Universe expansion v2 — walk-forward orchestrator (pre-reg Sections 3-5).

Workflow:
  1. Snapshot live recommendation parquets (bull_put / bear_call / IF wing)
  2. Run the 6 backtest+walkforward scripts (overwrites live with the
     expanded universe = 163 existing + 163 new candidates)
  3. Read the new walkforward outputs
  4. Filter to NEW candidates only (universe_v2_liquidity_pool tickers)
  5. Apply BH-FDR q<0.10 per structure to val_p
  6. Apply per-structure val P/L threshold gate
  7. Emit candidate survivor parquet + markdown report
  8. RESTORE live recommendation parquets from before-snapshot
     (live alert behavior unchanged after a run)

The user reviews the candidate parquet and manually adds names to
gate_config.py + reruns the quarterly_cohort_refresh to fold them into
live recommendations.

Per-structure val P/L thresholds (per pre-reg Section 5):
  - bull_put: val_mean_winner ≥ +$0.05/share (slip=0.50)
  - bear_call: val_mean_winner ≥ +$0.05/share
  - inverted_fly: val_mean_winner ≥ +$0.10/share
  (per-share is the schema convention; per-contract = ×100)

Usage:
    python3.11 -m scripts.maintenance.universe_v2_walkforward_orchestrator
    python3.11 -m scripts.maintenance.universe_v2_walkforward_orchestrator --skip-rerun
        # (skip the heavy backtest re-runs; analyze whatever's in the
        #  current walkforward parquets — useful if you've already run them)
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PROFILE = ROOT / "data/profile"
POOL = PROFILE / "universe_v2_liquidity_pool.parquet"
REPORTS_DIR = ROOT / "reports"
SANDBOX_ROOT = PROFILE / "universe_v2_sandbox"

# (label, recommendation_parquet, walkforward_parquet, val_mean_threshold_per_share, scripts_in_order)
STUDIES = [
    (
        "bull_put",
        "bull_put_moneyness_recommendation.parquet",
        "bull_put_moneyness_walkforward.parquet",
        0.05,
        [
            "scripts/backtest/bull_put_moneyness_backtest.py",
            "scripts/backtest/bull_put_moneyness_walkforward.py",
        ],
    ),
    (
        "bear_call",
        "bear_call_moneyness_recommendation.parquet",
        "bear_call_moneyness_walkforward.parquet",
        0.05,
        [
            "scripts/backtest/bear_call_moneyness_backtest.py",
            "scripts/backtest/bear_call_moneyness_walkforward.py",
        ],
    ),
    (
        "inverted_fly",
        "inverted_fly_wing_recommendation.parquet",
        "inverted_fly_wing_walkforward.parquet",
        0.10,
        [
            "scripts/backtest/inverted_fly_wing_backtest.py",
            "scripts/backtest/inverted_fly_wing_analyze.py",
        ],
    ),
]


def snapshot_live(out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    snap = {}
    for label, rec_fname, wf_fname, *_ in STUDIES:
        for fname in (rec_fname, wf_fname):
            src = PROFILE / fname
            if src.exists():
                shutil.copy2(src, out_dir / fname)
                snap[fname] = out_dir / fname
    return snap


def restore_live(snap: dict[str, Path]) -> None:
    for fname, path in snap.items():
        live = PROFILE / fname
        shutil.copy2(path, live)


def run_script(rel_path: str) -> tuple[bool, float]:
    mod = rel_path.removesuffix(".py").replace("/", ".")
    print(f"  → {mod}", flush=True)
    t0 = time.time()
    res = subprocess.run([sys.executable, "-m", mod], cwd=ROOT,
                         capture_output=True, text=True)
    el = time.time() - t0
    if res.returncode != 0:
        print(f"    ✗ FAILED in {el:.0f}s")
        print(f"    stderr tail:\n{res.stderr[-1500:]}")
        return False, el
    print(f"    ✓ ok in {el:.0f}s")
    return True, el


def bh_fdr(pvals: list[float], q: float = 0.10) -> tuple[list[bool], float]:
    """Benjamini-Hochberg FDR control. Returns (rejected_flags, cutoff_p).

    rejected_flags[i] = True if H_i is rejected (passes FDR control).
    cutoff_p = the largest p-value that passes; pvals < cutoff_p are
    significant under BH at level q.
    """
    n = len(pvals)
    if n == 0:
        return [], 0.0
    arr = np.asarray(pvals, dtype=float)
    order = np.argsort(arr)
    ranks = np.arange(1, n + 1)
    sorted_p = arr[order]
    crit = q * ranks / n
    passes = sorted_p <= crit
    if not passes.any():
        return [False] * n, 0.0
    k = np.where(passes)[0].max()
    cutoff_p = sorted_p[k]
    out = [False] * n
    for i in order[: k + 1]:
        out[i] = True
    return out, float(cutoff_p)


def best_promoted_per_ticker(wf_df: pd.DataFrame, structure: str) -> pd.DataFrame:
    """For each ticker, find the most-significant promoted pair. Returns
    DataFrame with columns: ticker, recommended_moneyness/wing, val_p,
    val_mean_winner, train_n, val_n, pair."""
    df = wf_df[wf_df["promoted"] == True].copy()  # noqa: E712
    if df.empty:
        return pd.DataFrame(columns=["ticker", "recommended", "val_p",
                                     "val_mean_winner", "train_n", "val_n", "pair"])
    df["val_p_safe"] = df["val_p"].fillna(1.0)

    # Compute val_mean_winner depending on schema
    if "val_mean_a" in df.columns and "val_mean_b" in df.columns:
        # Bull_put / bear_call schema: pair = "A vs B", winner is one of them
        def mean_winner(r):
            a, b = r["pair"].split(" vs ")
            return r["val_mean_a"] if r["val_winner"] == a else r["val_mean_b"]
        df["val_mean_winner"] = df.apply(mean_winner, axis=1)
    else:
        # IF wing schema has no val_mean cols — leave NaN (threshold check disabled)
        df["val_mean_winner"] = np.nan

    # Best (smallest val_p) per ticker
    df = df.sort_values(["ticker", "val_p_safe"]).drop_duplicates("ticker", keep="first")
    df["recommended"] = df["val_winner"]
    return df[["ticker", "recommended", "val_p", "val_mean_winner",
               "train_n", "val_n", "pair"]].reset_index(drop=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-rerun", action="store_true",
                   help="Skip backtest+walkforward re-runs; analyze current "
                        "walkforward parquets in place")
    p.add_argument("--no-restore", action="store_true",
                   help="Don't restore live recs from snapshot after run "
                        "(default: restore)")
    args = p.parse_args()

    if not POOL.exists():
        print(f"FATAL: liquidity pool not found at {POOL}", file=sys.stderr)
        return 1

    pool = pd.read_parquet(POOL)
    new_candidates = set(pool["ticker"].tolist())
    run_date = date.today().isoformat()
    sandbox = SANDBOX_ROOT / run_date
    before_dir = sandbox / "before"
    after_dir = sandbox / "after"

    print(f"Universe v2 walk-forward orchestrator — {run_date}")
    print(f"New candidates: {len(new_candidates)}")
    print(f"Sandbox: {sandbox}")
    print()

    print("[1/5] Snapshotting live recommendation + walkforward parquets...")
    before_snap = snapshot_live(before_dir)
    print(f"  Snapshotted {len(before_snap)} files")
    print()

    timings = {}
    if not args.skip_rerun:
        print("[2/5] Running backtests + walkforwards (this is the heavy step)...")
        for label, _rec, _wf, _thr, scripts in STUDIES:
            print(f"  ── {label} ──")
            timings[label] = []
            for script in scripts:
                ok, el = run_script(script)
                timings[label].append(el)
                if not ok:
                    print(f"    aborting {label}")
                    break
        print()
    else:
        print("[2/5] Skipping re-run; using current walkforward outputs")
        print()

    print("[3/5] Snapshotting post-run parquets to sandbox...")
    after_snap = snapshot_live(after_dir)
    print(f"  Snapshotted {len(after_snap)} files")
    print()

    print("[4/5] Applying BH-FDR (q<0.10) + val_mean threshold per structure...")
    all_survivors = []
    summary_rows = []
    for label, _rec, wf_fname, val_threshold, _scripts in STUDIES:
        wf_path = PROFILE / wf_fname
        if not wf_path.exists():
            print(f"  {label}: walkforward parquet missing — skipping")
            continue
        wf = pd.read_parquet(wf_path)
        # Filter to NEW candidates only
        new_wf = wf[wf["ticker"].isin(new_candidates)].copy()
        n_new_total = new_wf["ticker"].nunique()
        n_promoted_pairs = (new_wf["promoted"] == True).sum()  # noqa: E712

        # Best promoted pair per ticker
        per_ticker = best_promoted_per_ticker(new_wf, label)
        n_walkforward_pass = len(per_ticker)

        # Apply BH-FDR on val_p
        if n_walkforward_pass > 0:
            pvals = per_ticker["val_p"].fillna(1.0).tolist()
            bh_pass, bh_cutoff = bh_fdr(pvals, q=0.10)
            per_ticker["bh_fdr_pass"] = bh_pass
        else:
            bh_cutoff = 0.0
            per_ticker["bh_fdr_pass"] = False

        # Apply val_mean threshold (only relevant where val_mean_winner is non-NaN)
        if per_ticker["val_mean_winner"].notna().any():
            per_ticker["val_mean_pass"] = per_ticker["val_mean_winner"] >= val_threshold
        else:
            per_ticker["val_mean_pass"] = True  # no val_mean (IF) → don't gate

        # Final survivor
        per_ticker["promoted_v2"] = per_ticker["bh_fdr_pass"] & per_ticker["val_mean_pass"]
        per_ticker["structure"] = label
        survivors = per_ticker[per_ticker["promoted_v2"]].copy()

        n_bh_pass = int(per_ticker["bh_fdr_pass"].sum())
        n_threshold_pass = int(per_ticker["val_mean_pass"].sum())
        n_final = len(survivors)

        print(f"  {label}: {n_new_total} new candidates evaluated · "
              f"walkforward pass {n_walkforward_pass} · "
              f"BH-FDR pass {n_bh_pass} (cutoff p={bh_cutoff:.4f}) · "
              f"val_mean pass {n_threshold_pass} · "
              f"FINAL {n_final}")

        all_survivors.append(per_ticker)
        summary_rows.append({
            "structure": label,
            "n_new_candidates": n_new_total,
            "n_walkforward_pass": n_walkforward_pass,
            "n_bh_fdr_pass": n_bh_pass,
            "bh_fdr_cutoff_p": bh_cutoff,
            "n_val_mean_pass": n_threshold_pass,
            "n_final_survivors": n_final,
            "val_mean_threshold": val_threshold,
        })

    print()
    print("[5/5] Writing outputs...")
    if all_survivors:
        full = pd.concat(all_survivors, ignore_index=True)
        out_path = PROFILE / "universe_expansion_v2_candidates.parquet"
        full.to_parquet(out_path, index=False)
        print(f"  ✓ Candidates parquet: {out_path}  ({len(full)} rows)")

        # Markdown report
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        report = REPORTS_DIR / f"universe_expansion_v2_{run_date}.md"
        lines = [
            f"# Universe Expansion v2 — Candidate Survivors ({run_date})",
            "",
            "_Pre-reg: docs/UNIVERSE_EXPANSION_V2_PREREG.md (sealed 2026-05-02)_",
            "",
            "## Per-structure summary",
            "",
            "| Structure | New cands | WF pass | BH-FDR pass | BH cutoff p | val_mean pass | **Final** |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in summary_rows:
            lines.append(
                f"| {r['structure']} | {r['n_new_candidates']} | "
                f"{r['n_walkforward_pass']} | {r['n_bh_fdr_pass']} | "
                f"{r['bh_fdr_cutoff_p']:.4f} | "
                f"{r['n_val_mean_pass']} | **{r['n_final_survivors']}** |"
            )
        lines.append("")

        for label, _rec, _wf, _thr, _scripts in STUDIES:
            survivors = full[(full["structure"] == label) & full["promoted_v2"]]
            if survivors.empty:
                lines.append(f"## {label} — 0 survivors")
                lines.append("")
                continue
            lines.append(f"## {label} — {len(survivors)} survivors")
            lines.append("")
            lines.append(f"| Ticker | Rec | val_p | val_mean | train_n | val_n | Pair |")
            lines.append("|---|---|---|---|---|---|---|")
            for _, s in survivors.sort_values("val_p").iterrows():
                vm = f"{s['val_mean_winner']:+.4f}" if pd.notna(s["val_mean_winner"]) else "—"
                lines.append(
                    f"| {s['ticker']} | {s['recommended']} | "
                    f"{s['val_p']:.4f} | {vm} | "
                    f"{int(s['train_n'])} | {int(s['val_n'])} | {s['pair']} |"
                )
            lines.append("")

        # Falsification triggers (per pre-reg Section 6)
        lines.append("## Falsification triggers (pre-reg Section 6)")
        lines.append("")
        for r in summary_rows:
            n = r["n_final_survivors"]
            structure = r["structure"]
            if structure == "bull_put":
                upper = 80
            elif structure == "bear_call":
                upper = 60
            elif structure == "inverted_fly":
                upper = 40
            else:
                upper = 60
            if n > upper * 1.5:
                lines.append(f"- ⚠ {structure}: {n} survivors > 1.5× upper bound ({upper}). "
                             "Methodology may be leaking — investigate before promoting.")
        if all(r["n_final_survivors"] == 0 for r in summary_rows):
            lines.append("- ⚠ ALL structures produced zero survivors. "
                         "BH-FDR cutoff may be too aggressive; revisit.")
        lines.append("")

        report.write_text("\n".join(lines))
        print(f"  ✓ Report: {report}")

    if not args.no_restore:
        print()
        print("Restoring live recommendation parquets from before-snapshot...")
        restore_live(before_snap)
        print(f"  ✓ Restored {len(before_snap)} files")

    return 0


if __name__ == "__main__":
    sys.exit(main())
