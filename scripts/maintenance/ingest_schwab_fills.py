"""EOD Schwab fills ingestion (go-live bookkeeping).

Reads TRADE transactions from the live Schwab account and writes parsed fills
(symbol/leg, action, qty, price, realized netAmount, fees) into the `schwab_fills`
table, idempotent on activityId. This is the single ~4:20 ET EOD reconciler from
feedback_schwab_eod_reconciler_scope — no realtime — that replaces the manual
close protocol once live: Schwab supplies positions, P/L and fees directly
(reference_schwab_account_api_access).

Incremental: fetches from the latest ingested fill time (minus a 2-day overlap)
to now; INSERT OR IGNORE makes the overlap harmless. First run uses --lookback
(default 90 days). Fail-soft: a Schwab outage logs and exits non-zero without
corrupting the table.

After ingesting, fills are matched to `spread_score_trades` via
lib/fills_ledger_match (go-live audit F5): clean opens AUTO-CREATE a live
ledger row (account='live'), clean closes auto-close it, already-recorded
trades just get linked, and anything ambiguous is flagged loudly every run
until resolved. This is what keeps a live position from running dark the way
the HCA bull_put did 6/09→6/12.

Runs intraday (10:00/12:00/14:00/16:22 ET) since 2026-06-12 — read-only API,
idempotent, so frequency only shrinks the detection window.

Usage:
  python3.11 -m scripts.maintenance.ingest_schwab_fills            # incremental
  python3.11 -m scripts.maintenance.ingest_schwab_fills --lookback 30
  python3.11 -m scripts.maintenance.ingest_schwab_fills --report   # show recent fills, no fetch
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import connect                              # noqa: E402
from lib.schwab_account import (                        # noqa: E402
    fetch_transactions, parse_trade_transaction, upsert_fills, ensure_fills_table,
)

_Z = "%Y-%m-%dT%H:%M:%S.000Z"


def _window(conn, lookback_days: int) -> tuple[str, str]:
    """[start,end] ISO-Z. Start = last ingested fill time − 2-day overlap, else
    now − lookback_days."""
    ensure_fills_table(conn)
    last = conn.execute("SELECT MAX(time) FROM schwab_fills").fetchone()[0]
    end = datetime.now(timezone.utc) + timedelta(days=1)
    if last:
        start = datetime.fromisoformat(last.replace("Z", "+00:00")) - timedelta(days=2)
    else:
        start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    return start.strftime(_Z), end.strftime(_Z)


def _report(conn) -> None:
    ensure_fills_table(conn)
    n = conn.execute("SELECT COUNT(*) FROM schwab_fills").fetchone()[0]
    print(f"schwab_fills: {n} rows")
    rows = conn.execute(
        "SELECT trade_date, action, symbol, quantity, price, fees, net_amount "
        "FROM schwab_fills ORDER BY time DESC LIMIT 15").fetchall()
    for r in rows:
        print(f"  {r[0]} {str(r[1] or ''):<4} {str(r[2])[:22]:<22} "
              f"qty={r[3]} px={r[4]} fees={r[5]} net={r[6]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback", type=int, default=90, help="days back on first/empty run")
    ap.add_argument("--report", action="store_true", help="print recent fills, no fetch")
    args = ap.parse_args()

    conn = connect()
    try:
        if args.report:
            _report(conn)
            return 0
        start, end = _window(conn, args.lookback)
        try:
            txns = fetch_transactions(start, end, types="TRADE")
        except Exception as e:
            print(f"ingest_schwab_fills: Schwab fetch FAILED ({e.__class__.__name__}: {e}) — no changes")
            return 1
        fills = [f for f in (parse_trade_transaction(t) for t in txns) if f]
        skipped = len(txns) - len(fills)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        n_new = upsert_fills(conn, fills, stamp)
        total = conn.execute("SELECT COUNT(*) FROM schwab_fills").fetchone()[0]
        opt = sum(1 for f in fills if f["asset_type"] == "OPTION")
        print(f"ingest_schwab_fills: window {start[:10]}..{end[:10]} | "
              f"{len(txns)} txns ({skipped} non-instrument) → {n_new} new fills "
              f"({opt} option legs); {total} total in schwab_fills")

        # F5: match option fills → ledger rows so live positions never run
        # dark. Loud failure (exit 1 → cron email): a broken matcher must not
        # silently leave a live position untracked.
        from lib.fills_ledger_match import match_fills_to_ledger, render_summary
        result = match_fills_to_ledger(conn)
        summary = render_summary(result)
        if summary:
            print("fills→ledger match:")
            print(summary)
        if result["flagged"]:
            print(f"ingest_schwab_fills: {len(result['flagged'])} UNRESOLVED "
                  f"fill group(s) — exiting 1 so cron alerts until handled")
            return 1
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
