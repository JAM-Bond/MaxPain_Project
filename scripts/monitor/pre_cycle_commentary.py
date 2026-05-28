"""Cron entrypoint for AI Pre-Cycle Commentary (Phase 2).

Fires daily at 9:30 ET (5 min after cycle_qualifier.py at 9:25). Gated:
skips API call entirely if today's qualifier produced zero GO and zero
DOWNSIZE rows. When the gate trips, the cron is essentially free
(SQL count + log line).

Suggested crontab line:

    30 9 * * 1-5 cd ~/MaxPain_Project && /opt/homebrew/bin/python3.11 \\
        scripts/monitor/pre_cycle_commentary.py \\
        >> ~/MaxPain_Project/logs/pre_cycle_commentary_cron.log 2>&1

The dashboard page (8_Pre_Cycle.py) reads the cached output. The 4:45 PM
daily-alert annotation surface also reads the latest cached row.
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from dashboard.queries.pre_cycle_bundle import (  # noqa: E402
    has_decision_relevant_verdicts,
    compose_bundle,
)


def main(run_date: str | None = None, force: bool = False) -> int:
    run_date = run_date or date.today().isoformat()
    ts = datetime.now().isoformat(timespec="seconds")

    print("=" * 78)
    print(f"  AI Pre-Cycle Commentary — run_date {run_date}   ({ts})")
    print("=" * 78)

    has_decisions, n_go, n_ds = has_decision_relevant_verdicts(run_date)
    print(f"  Qualifier decisions today: GO={n_go}  DOWNSIZE={n_ds}")

    if not has_decisions and not force:
        print("  → No decision-relevant verdicts. Commentary skipped (no API call).")
        print("=" * 78)
        return 0

    print("  → Decisions present. Composing bundle + invoking AI.")
    bundle = compose_bundle(run_date)
    print(f"  Bundle composed: {len(bundle):,} chars")

    # Lazy import — avoids requiring `anthropic` for gate-skip cron paths.
    from lib.ai_pre_cycle_commentary import generate_pre_cycle_commentary  # noqa: E402
    result = generate_pre_cycle_commentary(
        bundle_text=bundle, run_date=run_date, force_refresh=force
    )

    print(f"  AI call complete (cached={result.cached}, "
          f"prompt={result.prompt_version}):")
    print(f"    in={result.input_tokens}  out={result.output_tokens}  "
          f"cache_read={result.cache_read_tokens}  "
          f"elapsed={result.elapsed_seconds:.1f}s")
    print()
    print("─── Commentary ─────────────────────────────────────────────────────────────")
    print(result.response_text)
    print("─" * 78)
    print("=" * 78)
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", default=None,
                    help="ISO date (defaults to today)")
    ap.add_argument("--force", action="store_true",
                    help="Bypass gate AND cache; always generate")
    args = ap.parse_args()
    sys.exit(main(run_date=args.run_date, force=args.force))
