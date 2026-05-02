#!/usr/bin/env python3.11
"""
Quarterly cohort refresh orchestrator (Phase 1 / Tier A).

Re-runs the per-ticker walk-forward studies on the current
data/orats/by_ticker/ archive (163 tickers as of 2026-05-02) and emits a
markdown diff report comparing new recommendations to the live ones.

Three studies orchestrated, in order:
  1. bull_put moneyness (OTM/ATM/ITM)   — bull_put_moneyness_*.py
  2. bear_call moneyness (OTM/ATM/ITM)  — bear_call_moneyness_*.py
  3. inverted_fly wing width (2/5/10/15%) — inverted_fly_wing_*.py

Default mode is --dry-run (sandbox): snapshot live recs, run the
studies (which overwrite live recs by design), capture the new recs to
a sandbox dir, then RESTORE the live recs from snapshot. Live
recommendation parquets are unchanged after a dry run.

--apply mode: snapshots before/after the run; live recs are NOT
restored. The new recommendations take effect on the next daily-alert
construction-block render.

Output:
  reports/quarterly_refresh_<run_date>.md           — diff report
  data/profile/quarterly_refresh/<run_date>/before/ — pre-run snapshot
  data/profile/quarterly_refresh/<run_date>/after/  — post-run snapshot

Cron: scheduled for 6 AM ET on the first weekday of Jan/Apr/Jul/Oct.
Estimated runtime: 2-3 hours (~30 min per backtest × 3, plus
walk-forwards ~10 min each).

Usage:
    # Dry-run (default — does NOT touch live recommendation parquets)
    python3.11 -m scripts.maintenance.quarterly_cohort_refresh

    # Apply (DOES update live recs after the run)
    python3.11 -m scripts.maintenance.quarterly_cohort_refresh --apply

    # Skip the heavy re-runs and only emit a diff against current state
    # (sanity check the report-generation path)
    python3.11 -m scripts.maintenance.quarterly_cohort_refresh --skip-rerun
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PROFILE = ROOT / "data/profile"
REFRESH_DIR = PROFILE / "quarterly_refresh"
REPORTS_DIR = ROOT / "reports"

# (label, recommendation_parquet, key_columns, value_column, scripts_in_order)
# key_columns is a tuple — bull_put / bear_call have 2 rows per ticker
# (held + mgd50 exit rules); the IF parquet has 1 row per ticker.
STUDIES = [
    (
        "bull_put",
        "bull_put_moneyness_recommendation.parquet",
        ("ticker", "exit_rule"),
        "recommended_moneyness",
        [
            "scripts/backtest/bull_put_moneyness_backtest.py",
            "scripts/backtest/bull_put_moneyness_walkforward.py",
        ],
    ),
    (
        "bear_call",
        "bear_call_moneyness_recommendation.parquet",
        ("ticker", "exit_rule"),
        "recommended_moneyness",
        [
            "scripts/backtest/bear_call_moneyness_backtest.py",
            "scripts/backtest/bear_call_moneyness_walkforward.py",
        ],
    ),
    (
        "inverted_fly",
        "inverted_fly_wing_recommendation.parquet",
        ("ticker",),
        "recommended_variant",
        [
            "scripts/backtest/inverted_fly_wing_backtest.py",
            "scripts/backtest/inverted_fly_wing_analyze.py",
        ],
    ),
]


def snapshot_live(out_dir: Path) -> dict[str, Path]:
    """Copy each live recommendation parquet to out_dir. Returns mapping
    label -> snapshot path. Missing files (e.g. first run) are skipped."""
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {}
    for label, fname, *_ in STUDIES:
        src = PROFILE / fname
        if src.exists():
            dst = out_dir / fname
            shutil.copy2(src, dst)
            snapshot[label] = dst
        else:
            print(f"  · {label}: no current recommendation parquet ({fname})")
    return snapshot


def restore_live(snapshot: dict[str, Path]) -> None:
    """Copy snapshot files back over the live recommendation parquets."""
    for label, snap_path in snapshot.items():
        live_path = PROFILE / snap_path.name
        shutil.copy2(snap_path, live_path)
        print(f"  ✓ restored {live_path.name} from snapshot")


def run_script(rel_path: str) -> tuple[bool, float]:
    """Invoke a backtest/walk-forward script as a module. Returns (ok, secs)."""
    mod = rel_path.removesuffix(".py").replace("/", ".")
    print(f"  → running {mod} ...", flush=True)
    t0 = time.time()
    res = subprocess.run(
        [sys.executable, "-m", mod],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    elapsed = time.time() - t0
    if res.returncode != 0:
        print(f"    ✗ FAILED in {elapsed:.0f}s")
        print(f"    stdout tail:\n{res.stdout[-1000:]}")
        print(f"    stderr tail:\n{res.stderr[-1000:]}")
        return False, elapsed
    print(f"    ✓ ok in {elapsed:.0f}s")
    return True, elapsed


def diff_recommendations(
    before: Path | None, after: Path | None, key_cols: tuple[str, ...], value: str
) -> dict:
    """Compare two recommendation parquets, return per-row diff. key_cols
    is a tuple of column names that uniquely identify a row (e.g.
    ("ticker", "exit_rule") for bull_put / bear_call)."""
    if before is None or not before.exists():
        if after is None or not after.exists():
            return {"missing_both": True}
        df_after = pd.read_parquet(after)
        return {"all_new": df_after}
    if after is None or not after.exists():
        return {"all_dropped": pd.read_parquet(before)}

    df_b = pd.read_parquet(before)
    df_a = pd.read_parquet(after)
    b_map = {tuple(r[c] for c in key_cols): r[value] for _, r in df_b.iterrows()}
    a_map = {tuple(r[c] for c in key_cols): r[value] for _, r in df_a.iterrows()}

    keys_b = set(b_map)
    keys_a = set(a_map)
    added = sorted(keys_a - keys_b)
    dropped = sorted(keys_b - keys_a)
    common = sorted(keys_b & keys_a)
    changed = []
    unchanged_n = 0
    for k in common:
        v_b = b_map[k]
        v_a = a_map[k]
        if v_b != v_a:
            changed.append((k, v_b, v_a))
        else:
            unchanged_n += 1

    return {
        "added": added,
        "dropped": dropped,
        "changed": changed,
        "unchanged_n": unchanged_n,
        "n_before": len(df_b),
        "n_after": len(df_a),
    }


def render_report(run_date: str, diffs: dict, timings: dict, dry_run: bool) -> str:
    lines = []
    lines.append(f"# Quarterly Cohort Refresh — {run_date}")
    lines.append("")
    lines.append(
        f"_Mode: {'DRY-RUN (live recs not modified)' if dry_run else 'APPLY (live recs updated)'}_"
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Study | Runtime | Δ added | Δ dropped | Δ changed | Unchanged |")
    lines.append("|---|---|---|---|---|---|")
    for label, _fname, _k, _v, _scripts in STUDIES:
        d = diffs.get(label, {})
        secs = sum(timings.get(label, []))
        lines.append(
            f"| {label} | {secs:.0f}s | "
            f"{len(d.get('added', []))} | "
            f"{len(d.get('dropped', []))} | "
            f"{len(d.get('changed', []))} | "
            f"{d.get('unchanged_n', 0)} |"
        )
    lines.append("")

    for label, _fname, _k, value, _scripts in STUDIES:
        d = diffs.get(label)
        if not d:
            continue
        lines.append(f"## {label}")
        lines.append("")
        if d.get("missing_both"):
            lines.append("_no recommendation parquet before or after — study did not produce output_")
            lines.append("")
            continue
        if d.get("all_new") is not None:
            df = d["all_new"]
            lines.append(f"_first run — all {len(df)} recommendations are new_")
            lines.append("")
            continue
        if d.get("all_dropped") is not None:
            df = d["all_dropped"]
            lines.append(f"_recommendation parquet missing post-run — all {len(df)} recommendations dropped_")
            lines.append("")
            continue

        lines.append(
            f"- Before: {d['n_before']} recs · After: {d['n_after']} recs · "
            f"Unchanged: {d['unchanged_n']}"
        )
        fmt_key = lambda k: " ".join(str(p) for p in k) if isinstance(k, tuple) else str(k)
        if d["added"]:
            lines.append(f"- **Added ({len(d['added'])}):** "
                         f"{', '.join(fmt_key(k) for k in d['added'])}")
        if d["dropped"]:
            lines.append(f"- **Dropped ({len(d['dropped'])}):** "
                         f"{', '.join(fmt_key(k) for k in d['dropped'])}")
        if d["changed"]:
            lines.append(f"- **Changed ({len(d['changed'])}):**")
            lines.append("")
            lines.append(f"| Key | Before ({value}) | After ({value}) |")
            lines.append("|---|---|---|")
            for k, vb, va in d["changed"]:
                lines.append(f"| {fmt_key(k)} | {vb} | {va} |")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Overwrite live recommendation parquets after the run "
                        "(default: restore live recs from snapshot)")
    p.add_argument("--skip-rerun", action="store_true",
                   help="Skip the 2-3 hour backtest+walkforward re-runs and "
                        "only generate a no-op diff report (sanity check the "
                        "report-generation path)")
    args = p.parse_args()

    dry_run = not args.apply
    run_date = date.today().isoformat()
    work_dir = REFRESH_DIR / run_date
    before_dir = work_dir / "before"
    after_dir = work_dir / "after"

    print(f"Quarterly cohort refresh — {run_date}")
    print(f"Mode: {'DRY-RUN (live recs preserved)' if dry_run else 'APPLY (live recs updated)'}")
    print()

    print("[1/4] Snapshotting current live recommendations...")
    before_snapshot = snapshot_live(before_dir)
    print()

    timings = {label: [] for label, *_ in STUDIES}
    if not args.skip_rerun:
        print("[2/4] Running backtests + walk-forwards (this takes ~2-3 hours)...")
        for label, _fname, _k, _v, scripts in STUDIES:
            print(f"  ── {label} ──")
            for script in scripts:
                ok, secs = run_script(script)
                timings[label].append(secs)
                if not ok:
                    print(f"    aborting {label} (script failure)")
                    break
            print()
    else:
        print("[2/4] Skipping re-run (--skip-rerun); diff will be no-op.")
        print()

    print("[3/4] Snapshotting post-run recommendations...")
    after_snapshot = snapshot_live(after_dir)
    print()

    print("[4/4] Computing diffs and rendering report...")
    diffs = {}
    for label, fname, key_cols, value, _scripts in STUDIES:
        b = before_snapshot.get(label)
        a = after_snapshot.get(label)
        diffs[label] = diff_recommendations(b, a, key_cols, value)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"quarterly_refresh_{run_date}.md"
    report_path.write_text(render_report(run_date, diffs, timings, dry_run))
    print(f"  ✓ Report: {report_path}")

    if dry_run and not args.skip_rerun:
        print()
        print("Restoring live recommendations from before-snapshot...")
        restore_live(before_snapshot)
        print()
        print("Dry-run complete. Live recs unchanged.")
        print(f"To apply: python3.11 -m scripts.maintenance.quarterly_cohort_refresh --apply")

    return 0


if __name__ == "__main__":
    sys.exit(main())
