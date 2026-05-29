"""Auto-promotion pipeline — nightly driver (Stages 2-5).

Reads tonight's liquidity snapshot (Stage 1 output), picks the night's
batch, extracts historicals for any new tickers, runs walk-forwards,
evaluates gates, writes cohort changes, persists ledger + audit log,
and emails a summary.

Pre-reg: docs/AUTO_PROMOTION_PIPELINE_PREREG.md
Cron: 22:35 ET weekdays (Stage 1 fires at 22:30).

Usage:
  python3.11 -m scripts.maintenance.auto_promotion_nightly
  python3.11 -m scripts.maintenance.auto_promotion_nightly --dry-run --batch-size 5
  python3.11 -m scripts.maintenance.auto_promotion_nightly --snapshot-date 2026-05-16

Always exits 0 from cron's perspective; failures are reported by email.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKTEST_DIR = ROOT / "scripts/backtest"
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))

from lib.auto_promotion import (  # noqa: E402
    AUTO_PROMOTION_DIR,
    NIGHTLY_BATCH_SIZE,
    pick_nightly_batch,
    update_ledger,
    check_safety_thresholds,
    record_cohort_changes,
)
from lib.walkforward_runner import run_walkforward, STRUCTURES  # noqa: E402
from lib.email_alert import send_html_alert  # noqa: E402
from scripts.maintenance.auto_promotion_gate_check import (  # noqa: E402
    evaluate_batch, decisions_to_dataframe,
)
from scripts.maintenance.auto_promotion_gate_config_writer import (  # noqa: E402
    CohortChange, apply_changes, read_cohort_members, STRUCTURE_TO_COHORT,
)

BY_TICKER = ROOT / "data/orats/by_ticker"
RAW_PARQUET_ROOT = ROOT / "data/orats/parquet"

log = logging.getLogger("auto_promotion_nightly")

# Columns we extract for new tickers' per-ticker parquets — match the
# existing by_ticker schema produced by extract_new_tickers.py.
EXTRACT_KEEP_COLS = [
    "ticker", "expirDate", "yte", "strike",
    "stkPx", "delta",
    "cBidPx", "cAskPx", "cMidIv", "cOi", "cVolu",
    "pBidPx", "pAskPx", "pMidIv", "pOi", "pVolu",
]


# ──── Stage 1 output discovery ────────────────────────────────────────────

def _find_snapshot(snapshot_date: date | None) -> Path | None:
    """Return path to liquidity_snapshot_YYYY-MM-DD.parquet for given date
    (or the most recent if None)."""
    if snapshot_date is not None:
        p = AUTO_PROMOTION_DIR / f"liquidity_snapshot_{snapshot_date.isoformat()}.parquet"
        return p if p.exists() else None
    snaps = sorted(AUTO_PROMOTION_DIR.glob("liquidity_snapshot_*.parquet"))
    return snaps[-1] if snaps else None


# ──── Stage 3: historical extract for new tickers ────────────────────────

def _extract_new_tickers(tickers: list[str]) -> dict[str, int]:
    """Extract per-ticker parquets from the raw ORATS daily archive for
    every ticker in `tickers` that doesn't already have a by_ticker file.

    Returns {ticker: row_count_written}. Tickers that already exist or
    produce zero rows are skipped silently.
    """
    BY_TICKER.mkdir(parents=True, exist_ok=True)
    todo = [t for t in tickers if not (BY_TICKER / f"{t}.parquet").exists()]
    if not todo:
        return {}

    target = set(todo)
    log.info("Stage 3: extracting historicals for %d new ticker(s): %s",
             len(todo), sorted(todo)[:10] + (["..."] if len(todo) > 10 else []))

    per_ticker_frames: dict[str, list[pd.DataFrame]] = {t: [] for t in target}
    year_dirs = sorted([d for d in RAW_PARQUET_ROOT.iterdir()
                         if d.is_dir() and d.name.startswith("year=")])
    total_files = 0
    t0 = time.time()
    for yd in year_dirs:
        for md in sorted(yd.iterdir()):
            if not md.is_dir():
                continue
            for pf in sorted(md.glob("*.parquet")):
                total_files += 1
                try:
                    df = pd.read_parquet(
                        pf, columns=EXTRACT_KEEP_COLS,
                        filters=[("ticker", "in", list(target))],
                    )
                except Exception as e:
                    log.warning("  skip %s: %s", pf.name, e)
                    continue
                if df.empty:
                    continue
                trade_date = pd.to_datetime(pf.stem)
                df["trade_date"] = trade_date
                for t in target:
                    sub = df[df["ticker"] == t]
                    if not sub.empty:
                        per_ticker_frames[t].append(sub)
    counts: dict[str, int] = {}
    for t, frames in per_ticker_frames.items():
        if not frames:
            log.info("  %s: zero rows; not writing", t)
            continue
        out = pd.concat(frames, ignore_index=True)
        out_path = BY_TICKER / f"{t}.parquet"
        out.to_parquet(out_path, index=False)
        counts[t] = len(out)
    el = time.time() - t0
    log.info("Stage 3: %d files scanned, %d/%d tickers written in %.1fs",
             total_files, len(counts), len(todo), el)
    return counts


# ──── Reporting helpers ──────────────────────────────────────────────────

# Order the structures get rendered in. Premium-selling first, directional last.
STRUCTURE_ORDER = ["bull_put", "bear_call", "inverted_fly", "zebra"]

# Units label per structure (mean_pnl is in $/contract for verticals & IF, % capture for zebra)
STRUCTURE_UNITS = {
    "bull_put": "$/contract",
    "bear_call": "$/contract",
    "inverted_fly": "$/contract",
    "zebra": "% capture",
}


def _promote_sort_key(d) -> tuple:
    """Sort key for PROMOTE decisions — strongest first.

    Strength = (most-recent mean P/L) primary; (val_n) secondary; (-p_value) tertiary.
    All three reward "higher return for less risk" in the standard reading:
      mean_pnl  — observed edge in $/contract (or % capture for ZEBRA)
      val_n     — sample size; more data = more confidence the edge is real
      p_value   — statistical significance; smaller p = less risk it's chance

    Returns a sort key so that `sort(reverse=True)` puts the strongest first.
    """
    gb = d.detail.get("gate_b", {})
    mean = gb.get("most_recent_mean", float("-inf"))
    val_n = gb.get("most_recent_val_n", 0)
    p = d.detail.get("most_recent_p", 1.0)
    # Sort by mean desc, then val_n desc, then p asc — negate p so larger is "better" under reverse=True
    return (mean, val_n, -p if p == p else float("-inf"))  # NaN-safe


def _demote_sort_key(d) -> tuple:
    """Sort key for DEMOTE decisions — worst-performing first.

    Sort by (-splits_positive) primary so 0/4 cases appear before 1/4.
    Then by valid_splits desc (clearer signal).
    """
    gf = d.detail.get("gate_f", {})
    splits_pos = gf.get("splits_positive", 99)
    valid = gf.get("valid_splits", 0)
    return (-splits_pos, valid)


def _format_promote_line(d) -> str:
    """One-line render of a PROMOTE decision."""
    gb = d.detail.get("gate_b", {})
    mean = gb.get("most_recent_mean", float("nan"))
    val_n = gb.get("most_recent_val_n", 0)
    splits_pos = gb.get("splits_positive", 0)
    p = d.detail.get("most_recent_p", float("nan"))
    units = STRUCTURE_UNITS.get(d.structure, "")
    mean_str = f"{mean:>+8.2f}" if mean == mean else "   —   "
    p_str = f"{p:.4f}" if p == p else "—"
    return (f"  + {d.ticker:6s}  {splits_pos}/4 splits  "
            f"mean={mean_str} {units:11s}  val_n={val_n:>3d}  p={p_str}")


def _format_demote_line(d) -> str:
    """One-line render of a DEMOTE / DEMOTE_DEFERRED decision."""
    gf = d.detail.get("gate_f", {})
    splits_pos = gf.get("splits_positive", "?")
    valid = gf.get("valid_splits", "?")
    n_liq = d.detail.get("n_liq_fails", 0)
    if isinstance(splits_pos, int) and isinstance(valid, int):
        gate_part = f"Gate F {splits_pos}/{valid} valid-splits positive"
    else:
        gate_part = "Gate F"
    if n_liq >= 3:
        gate_part = f"Gate G ({n_liq} liquidity fails)"
    return f"  - {d.ticker:6s}  {gate_part}"


def _build_email_body(run_date: date,
                       n_evaluated: int, batch_size: int, runtime_min: float,
                       decisions: list, cohort_sizes_after: dict[str, int],
                       safety_violations: list[str],
                       writer_result: dict,
                       extra_note: str = "") -> tuple[str, str]:
    """Returns (text_body, html_body) for send_html_alert().

    Promotions and demotions are GROUPED BY STRUCTURE and within each group
    sorted by strength (highest expected edge × sample size, lowest p) first.
    """
    promoted = [d for d in decisions if d.action == "PROMOTE"]
    demoted = [d for d in decisions if d.action == "DEMOTE"]
    deferred = [d for d in decisions if d.action == "DEMOTE_DEFERRED"]
    skipped = [d for d in decisions if d.action == "SKIP"]

    text_lines = [
        f"MaxPain Auto-Promotion — {run_date.isoformat()}",
        "",
        f"Counts: promoted={len(promoted)} demoted={len(demoted)} "
        f"deferred={len(deferred)} evaluated={n_evaluated} batch={batch_size} "
        f"runtime={runtime_min:.1f}min",
        "",
    ]
    if safety_violations:
        text_lines.append("‼ SAFETY VIOLATIONS (writer HALTED):")
        for v in safety_violations:
            text_lines.append(f"  - {v}")
        text_lines.append("")
    if writer_result:
        text_lines.append(f"WRITER: {writer_result.get('reason')}")
        text_lines.append("")

    # Group promotions by structure, render strongest-first within each
    def _by_structure(items):
        out: dict[str, list] = {}
        for d in items:
            out.setdefault(d.structure, []).append(d)
        return out

    if promoted:
        text_lines.append(f"PROMOTED ({len(promoted)}) — grouped by structure, strongest first")
        by_s = _by_structure(promoted)
        ordered_structures = [s for s in STRUCTURE_ORDER if s in by_s] + [
            s for s in by_s if s not in STRUCTURE_ORDER]
        for s in ordered_structures:
            group = sorted(by_s[s], key=_promote_sort_key, reverse=True)
            text_lines.append("")
            text_lines.append(f"  ── {s} ({len(group)}) ──")
            for d in group:
                text_lines.append(_format_promote_line(d))
        text_lines.append("")

    if demoted:
        text_lines.append(f"DEMOTED ({len(demoted)}) — grouped by structure")
        by_s = _by_structure(demoted)
        ordered_structures = [s for s in STRUCTURE_ORDER if s in by_s] + [
            s for s in by_s if s not in STRUCTURE_ORDER]
        for s in ordered_structures:
            group = sorted(by_s[s], key=_demote_sort_key)
            text_lines.append("")
            text_lines.append(f"  ── {s} ({len(group)}) ──")
            for d in group:
                text_lines.append(_format_demote_line(d))
        text_lines.append("")

    if deferred:
        text_lines.append(f"DEMOTE-DEFERRED ({len(deferred)}) — open positions blocking demotion")
        for d in deferred:
            text_lines.append(f"  ⏸ {d.ticker:6s} {d.structure:14s} — {d.reason}")
        text_lines.append("")

    if skipped:
        text_lines.append(f"SKIPPED ({len(skipped)} no-data / error):")
        # Sample first 10 for the email; full list in the parquet audit log
        by_s = _by_structure(skipped)
        for s in [s for s in STRUCTURE_ORDER if s in by_s] + [
            ss for ss in by_s if ss not in STRUCTURE_ORDER]:
            n = len(by_s[s])
            text_lines.append(f"  {s}: {n} skipped")
        text_lines.append("")

    text_lines.append("Cohort sizes after writer:")
    for c, n in cohort_sizes_after.items():
        text_lines.append(f"  {c}: {n}")
    if extra_note:
        text_lines.append("")
        text_lines.append(extra_note)

    text_body = "\n".join(text_lines)
    html_body = "<pre style='font-family: monospace;'>" + text_body + "</pre>"
    return text_body, html_body


def _make_subject(run_date: date, n_prom: int, n_dem: int, n_eval: int,
                  halted: bool = False, failed: bool = False) -> str:
    if failed:
        return f"MaxPain Auto-Promotion — FAILED {run_date.isoformat()}"
    if halted:
        return f"MaxPain Auto-Promotion — HALTED (safety) {run_date.isoformat()}"
    return (f"MaxPain Auto-Promotion — {n_prom} promoted, {n_dem} demoted, "
            f"{n_eval} evaluated — {run_date.isoformat()}")


# ──── Main driver ────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Run gates + writer in dry-run mode; no parquet, no ledger, no gate_config writes, no email")
    ap.add_argument("--batch-size", type=int, default=NIGHTLY_BATCH_SIZE,
                    help=f"Override batch size (default {NIGHTLY_BATCH_SIZE})")
    ap.add_argument("--snapshot-date", default=None,
                    help="Use snapshot for given YYYY-MM-DD (default: latest)")
    ap.add_argument("--tickers", nargs="+", default=None,
                    help="Override batch with explicit ticker list (bypasses liquidity gate)")
    ap.add_argument("--structures", nargs="+", default=list(STRUCTURES),
                    choices=list(STRUCTURES))
    ap.add_argument("--skip-extract", action="store_true",
                    help="Skip Stage 3 (historical extract) for new tickers")
    ap.add_argument("--no-email", action="store_true",
                    help="Don't send email summary")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    run_date = date.today()
    snapshot_date = (datetime.strptime(args.snapshot_date, "%Y-%m-%d").date()
                      if args.snapshot_date else None)
    t0 = time.time()

    try:
        return _run(args, run_date, snapshot_date, t0)
    except Exception:
        tb = traceback.format_exc()
        log.error("FATAL: %s", tb)
        if not args.no_email:
            subj = _make_subject(run_date, 0, 0, 0, failed=True)
            body = f"Auto-promotion nightly cron FAILED.\n\n{tb}"
            send_html_alert(subj, body, f"<pre>{body}</pre>")
        # Exit non-zero so run_cron.sh traps this as a backstop (it emails the
        # log tail). cron does not auto-retry on non-zero, so this is safe — and
        # it covers the case where the email send above itself failed.
        return 1


def _run(args, run_date: date, snapshot_date: date | None, t0: float) -> int:
    # ── Find snapshot ──
    snap_path = _find_snapshot(snapshot_date)
    if snap_path is None:
        msg = (f"No liquidity snapshot found for "
               f"{snapshot_date or 'latest'}. Stage 1 cron may have failed.")
        log.error(msg)
        if not args.no_email:
            send_html_alert(
                _make_subject(run_date, 0, 0, 0, failed=True),
                msg, f"<pre>{msg}</pre>",
            )
        return 0

    log.info("Reading liquidity snapshot: %s", snap_path)
    snap = pd.read_parquet(snap_path)
    eligible = snap[snap["passes"]] if "passes" in snap.columns else snap
    log.info("Liquidity-passing tickers in snapshot: %d", len(eligible))

    # ── Pick batch ──
    if args.tickers:
        batch = list(args.tickers)
        log.info("Override batch (--tickers): %d names", len(batch))
    else:
        scores = dict(zip(eligible["ticker"], eligible["front_month_oi"]))
        batch = pick_nightly_batch(eligible["ticker"].tolist(),
                                    batch_size=args.batch_size,
                                    liquidity_scores=scores)
        log.info("Batch (Stage 2): %d names — first 5: %s",
                 len(batch), batch[:5])

    if not batch:
        log.warning("Empty batch; nothing to do tonight")
        return 0

    # ── Stage 3: historical extract for new tickers ──
    if args.skip_extract:
        log.info("Stage 3: --skip-extract; assuming all by_ticker parquets already exist")
    else:
        _extract_new_tickers(batch)

    # ── Stage 4: walk-forward per (ticker, structure) ──
    log.info("Stage 4: walk-forward for %d tickers × %d structures = %d runs",
             len(batch), len(args.structures),
             len(batch) * len(args.structures))
    results = []
    t4 = time.time()
    n = len(batch) * len(args.structures)
    i = 0
    for ticker in batch:
        for structure in args.structures:
            i += 1
            r = run_walkforward(ticker, structure)
            results.append(r)
            if i % 50 == 0 or i == n:
                log.info("  [%d/%d] %s/%s status=%s",
                         i, n, ticker, structure, r["status"])
    el4 = time.time() - t4
    log.info("Stage 4: done in %.1fmin", el4 / 60)

    # ── Stage 5a: gate evaluation ──
    log.info("Stage 5a: evaluating sealed gates...")
    current_members = read_cohort_members()
    cohorts_for_check = {
        "bull_put": current_members.get("COHORT_BULL_PUT", []),
        "bear_call": current_members.get("COHORT_BEAR_CALL", []),
        "inverted_fly_single": current_members.get("COHORT_INVERTED_FLY_SINGLE", []),
        "inverted_fly_pair": current_members.get("COHORT_INVERTED_FLY_PAIR", []),
        "zebra_tier1": current_members.get("COHORT_ZEBRA_TIER1", []),
        "zebra_tier2": current_members.get("COHORT_ZEBRA_TIER2", []),
    }
    # Load recent liquidity history for Gate G (3 consecutive fails)
    liq_history = []
    recent_snaps = sorted(
        AUTO_PROMOTION_DIR.glob("liquidity_snapshot_*.parquet"))[-3:]
    for p in recent_snaps:
        try:
            liq_history.append(pd.read_parquet(p))
        except Exception as e:
            log.warning("could not load %s: %s", p.name, e)

    decisions = evaluate_batch(
        results, cohorts_for_check,
        liquidity_history=liq_history,
        check_open_positions=True,
    )

    promotes = [d for d in decisions if d.action == "PROMOTE"]
    demotes = [d for d in decisions if d.action == "DEMOTE"]
    deferred = [d for d in decisions if d.action == "DEMOTE_DEFERRED"]
    log.info("Decisions: promote=%d demote=%d deferred=%d no_change/skip=%d",
             len(promotes), len(demotes), len(deferred),
             len(decisions) - len(promotes) - len(demotes) - len(deferred))

    # ── Stage 5b: safety check + writer ──
    n_prom = len(promotes)
    n_dem = len(demotes)
    cohort_sizes_after = {}
    for s in args.structures:
        cohort_name = STRUCTURE_TO_COHORT[s]
        base = len(current_members.get(cohort_name, []))
        delta = sum(1 for d in promotes if d.structure == s) - sum(
            1 for d in demotes if d.structure == s)
        cohort_sizes_after[cohort_name] = base + delta

    safety_ok, violations = check_safety_thresholds(
        n_prom, n_dem, cohort_sizes_after)

    changes = []
    for d in promotes + demotes:
        changes.append(CohortChange(
            ticker=d.ticker, structure=d.structure,
            action=d.action, reason=d.reason,
        ))

    writer_result = {"ok": True, "reason": "dry-run; writer skipped", "summary": {}}
    if changes and not args.dry_run:
        writer_result = apply_changes(
            changes, dry_run=False,
            safety_violations=violations if not safety_ok else None,
        )
    elif changes and args.dry_run:
        writer_result = apply_changes(
            changes, dry_run=True,
            safety_violations=violations if not safety_ok else None,
        )
    else:
        writer_result = {"ok": True, "reason": "no changes to apply", "summary": {}}

    # ── Stage 5c: audit log + ledger ──
    changes_path = (AUTO_PROMOTION_DIR /
                    f"changes_{run_date.isoformat()}.parquet")
    if not args.dry_run:
        AUTO_PROMOTION_DIR.mkdir(parents=True, exist_ok=True)
        decisions_to_dataframe(decisions).to_parquet(changes_path, index=False)
        log.info("Wrote changes audit: %s", changes_path)

        # Persist actionable decisions to the cohort_changes DB table.
        # `applied` reflects whether gate_config.py was actually edited (False
        # if safety brake halted). DEMOTE_DEFERRED rows always store applied=0.
        applied_flag = bool(writer_result.get("ok")) and not violations
        halt_reason = ("; ".join(violations)
                         if violations else None)
        n_db_rows = record_cohort_changes(
            decisions, run_date, applied=applied_flag,
            safety_halt_reason=halt_reason,
        )
        log.info("Recorded %d actionable rows to cohort_changes table "
                  "(applied=%s)", n_db_rows, applied_flag)

        # Update ledger
        ledger_updates = []
        # Group by ticker: status is "ok"/"no_data" from the most-significant
        # structure result (i.e., any "ok" => evaluated)
        by_ticker: dict[str, list[dict]] = {}
        for r in results:
            by_ticker.setdefault(r["ticker"], []).append(r)
        for ticker, rs in by_ticker.items():
            any_ok = any(r["status"] == "ok" for r in rs)
            status = "evaluated_ok" if any_ok else "evaluated_no_data"
            ledger_updates.append((ticker, status))
        update_ledger(ledger_updates, run_date=run_date)
        log.info("Updated ledger for %d tickers", len(ledger_updates))

    # ── Email summary ──
    runtime_min = (time.time() - t0) / 60.0
    text_body, html_body = _build_email_body(
        run_date,
        n_evaluated=len(decisions),
        batch_size=len(batch),
        runtime_min=runtime_min,
        decisions=decisions,
        cohort_sizes_after=cohort_sizes_after,
        safety_violations=violations,
        writer_result=writer_result,
        extra_note=(f"DRY-RUN — no gate_config writes, no ledger update, "
                    f"no parquet audit log."
                    if args.dry_run else
                    f"Audit log: {changes_path}"),
    )
    if not safety_ok:
        subject = _make_subject(run_date, n_prom, n_dem, len(decisions),
                                halted=True)
    else:
        subject = _make_subject(run_date, n_prom, n_dem, len(decisions))

    if not args.no_email:
        sent = send_html_alert(subject, text_body, html_body)
        log.info("Email sent: %s", sent)
    log.info("Complete in %.1fmin", runtime_min)
    return 0


if __name__ == "__main__":
    sys.exit(main())
