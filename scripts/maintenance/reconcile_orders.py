#!/usr/bin/env python3.11
"""Go-live order reconciler CLI — READ-ONLY Schwab orders -> our book.

Never places/modifies/cancels an order (see lib.order_reconciler). Default is
DRY-RUN (prints the plan, writes nothing). Pass --apply to write.

  python3.11 -m scripts.maintenance.reconcile_orders            # dry-run
  python3.11 -m scripts.maintenance.reconcile_orders --apply    # write
  python3.11 -m scripts.maintenance.reconcile_orders --days 10  # wider lookback
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.order_reconciler import reconcile  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write to the DB (default: dry-run)")
    ap.add_argument("--days", type=int, default=5, help="lookback window for FILLED orders")
    args = ap.parse_args()

    rep = reconcile(days=args.days, dry_run=not args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"=== Order reconciler ({mode}, last {args.days}d) ===\n")

    if rep["inserts"]:
        print(f"NEW positions to open ({len(rep['inserts'])}):")
        for i in rep["inserts"]:
            print(f"  + {i['symbol']:<6} {i['spread_type']:<12} "
                  f"{i['short_strike']:g}/{i['long_strike']:g} x{i['shares']} "
                  f"entry {i['entry_credit']:+.2f} ({i['entry_date']}) fees {i['fees_total']:.2f} "
                  f"order={i['open_order_id']}")
    if rep["links"]:
        print(f"\nLink order to existing open row ({len(rep['links'])}):")
        for l in rep["links"]:
            print(f"  ~ trade {l['trade_id']}: {l['desc']}  order={l['order_id']}")
    if rep["closes"]:
        print(f"\nCLOSES ({len(rep['closes'])}):")
        for c in rep["closes"]:
            print(f"  ✓ trade {c['trade_id']} {c['symbol']:<6} {c['spread_type']:<12} "
                  f"{c['strikes']:>11} x{c['shares']}  entry {c['entry_credit']:+.2f} "
                  f"exit {c['exit_price']:.2f} fees {c['fees_total']:.2f} -> "
                  f"net P/L ${c['final_pnl']:+.0f} ({c['exit_date']}) order={c['close_order_id']}")
    if rep["skipped"]:
        print(f"\nSkipped (already recorded) ({len(rep['skipped'])}):")
        for s in rep["skipped"]:
            print(f"  · order {s['order_id']}: {s['why']} (trade {s.get('trade_id')})")
    if rep["flags"]:
        print(f"\n⚠ FLAGGED — manual confirm ({len(rep['flags'])}):")
        for f in rep["flags"]:
            print(f"  ⚠ order {f.get('order_id')}: {f.get('flag') or f.get('reason')} "
                  f"[{f.get('underlying','?')} {f.get('spread_type','?')}]")

    if not any(rep[k] for k in ("inserts", "links", "closes", "flags")):
        print("Nothing to reconcile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
