"""
Daily mark-to-market for open credit spreads.

The 50% / 80% credit-captured alerts in daily_alert.py read
spread_score_daily.mark_credit; without a fresh mark each trading day
they silently no-op, hiding profit-target hits on real positions.

Runs against every open `placed=1` row in spread_score_trades. For each
trade, fetches the live Schwab chain via lib.schwab_options.fetch_chain_with_greeks
(same source as live trade construction — keeps entry, marking, and
alerts on a single price origin), reads bid/ask on short and long legs,
computes mid-mark = short_mid - long_mid, and INSERT OR REPLACEs into
spread_score_daily for today.

Spread-types covered:
  - bull_put, bull_put_mp, bull_put_earnings: puts (pBidPx/pAskPx)
  - bear_call, bear_call_earnings: calls (cBidPx/cAskPx)
  - iron_condor, iron_fly: not currently in the cohort; add when needed
  - zebra*: skipped — debit, different math (handled by zebra_stop_loss_event)
  - inverted_fly*: skipped — debit, different math
  - covered_call: not yet, cohort is empty

Usage:
  python3.11 scripts/pipeline/mark_open_spreads.py            # mark all
  python3.11 scripts/pipeline/mark_open_spreads.py --dry-run  # no DB write
  python3.11 scripts/pipeline/mark_open_spreads.py --symbol KRE USB

Cron: 4:20 PM ET weekdays (slot vacated by the disabled Metal daemon).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH  # noqa: E402
from lib.schwab_options import fetch_chain_with_greeks  # noqa: E402

# Spread-types that are credit verticals (short - long = positive credit).
# Anything else is skipped — this script does not mark debit structures.
CREDIT_VERTICAL_TYPES = {
    "bull_put", "bull_put_mp", "bull_put_earnings",
    "bear_call", "bear_call_earnings",
}


def load_open_credit_verticals(conn, symbol_filter: list[str] | None = None) -> pd.DataFrame:
    placeholders = ",".join("?" for _ in CREDIT_VERTICAL_TYPES)
    sql = f"""
        SELECT id, symbol, opex_date, spread_type,
               short_strike, long_strike, width, entry_credit, entry_date
        FROM spread_score_trades
        WHERE exit_date IS NULL
          AND placed = 1
          AND spread_type IN ({placeholders})
    """
    params: list = list(CREDIT_VERTICAL_TYPES)
    if symbol_filter:
        sql += f"  AND symbol IN ({','.join('?' for _ in symbol_filter)})"
        params.extend(symbol_filter)
    return pd.read_sql_query(sql, conn, params=params)


def mark_one(chain: pd.DataFrame, spread_type: str,
             short_strike: float, long_strike: float) -> dict | None:
    """Compute mark_credit + leg mids for one trade. Returns None on failure."""
    if chain is None or chain.empty:
        return None
    if spread_type.startswith("bull_put"):
        bid_col, ask_col = "pBidPx", "pAskPx"
    elif spread_type.startswith("bear_call"):
        bid_col, ask_col = "cBidPx", "cAskPx"
    else:
        return None

    sub = chain.dropna(subset=[bid_col, ask_col])
    sub = sub[(sub[bid_col] > 0) & (sub[ask_col] > 0)]
    if sub.empty:
        return None

    short_row = sub[sub["strike"] == short_strike]
    long_row = sub[sub["strike"] == long_strike]
    if short_row.empty or long_row.empty:
        return None

    short_mid = (float(short_row.iloc[0][bid_col]) + float(short_row.iloc[0][ask_col])) / 2
    long_mid = (float(long_row.iloc[0][bid_col]) + float(long_row.iloc[0][ask_col])) / 2
    mark_credit = round(short_mid - long_mid, 4)
    return {
        "mark_credit": mark_credit,
        "short_mid": short_mid,
        "long_mid": long_mid,
    }


def write_mark(conn, trade_id: int, mark_date_str: str, mark_credit: float,
               underlying_price: float, unrealized_pnl: float,
               pnl_pct: float, dte: int, dry_run: bool) -> None:
    """INSERT OR REPLACE one row into spread_score_daily.

    Only the columns the daily_alert needs are populated; the wider
    metric set (iv_rank, vrp, gex_z, etc.) is intentionally left NULL —
    those were diagnostic for Metal's research path, not required for
    profit-target alerts. Add later if a dashboard wants them.
    """
    if dry_run:
        return
    conn.execute(
        """
        INSERT OR REPLACE INTO spread_score_daily
          (trade_id, mark_date, mark_credit, underlying_price,
           unrealized_pnl, pnl_pct, dte)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (trade_id, mark_date_str, mark_credit, underlying_price,
         unrealized_pnl, pnl_pct, dte),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily mark for open credit verticals")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print marks but do not write to spread_score_daily")
    parser.add_argument("--symbol", nargs="+", default=None,
                        help="Only mark trades for these symbols")
    parser.add_argument("--mark-date", default=None,
                        help="Override mark_date (YYYY-MM-DD). Default: today")
    args = parser.parse_args()

    mark_date_str = args.mark_date or date.today().isoformat()

    conn = sqlite3.connect(DB_PATH)
    try:
        trades = load_open_credit_verticals(conn, args.symbol)
        if trades.empty:
            print(f"No open credit-vertical trades to mark "
                  f"(symbol filter: {args.symbol or 'all'}).")
            return 0

        print("=" * 72)
        print(f"  MaxPain Mark Run — mark_date {mark_date_str}")
        print(f"  Trades: {len(trades)}   Source: Schwab API (fetch_chain_with_greeks)")
        if args.dry_run:
            print("  DRY-RUN — no DB writes")
        print("=" * 72)

        n_ok = 0
        n_failed = 0
        # Group by (symbol, opex_date) so we hit Schwab once per chain
        for (sym, opex), grp in trades.groupby(["symbol", "opex_date"]):
            print(f"\n  {sym} OpEx {opex}  ({len(grp)} trade{'s' if len(grp) > 1 else ''})")
            try:
                chain, spot = fetch_chain_with_greeks(sym, opex)
            except Exception as e:
                print(f"    chain fetch error: {e}")
                n_failed += len(grp)
                continue
            if chain is None or chain.empty or spot is None:
                print(f"    no chain or spot returned")
                n_failed += len(grp)
                continue

            opex_dt = pd.to_datetime(opex).date()
            mark_dt = pd.to_datetime(mark_date_str).date()
            dte = max(0, (opex_dt - mark_dt).days)

            for _, t in grp.iterrows():
                m = mark_one(chain, t["spread_type"],
                             float(t["short_strike"]), float(t["long_strike"]))
                if m is None:
                    print(f"    {t['spread_type']:20} K={t['short_strike']:g}/{t['long_strike']:g}  "
                          f"NO MARK (strikes missing or zero quotes)")
                    n_failed += 1
                    continue

                mark_credit = m["mark_credit"]
                entry_credit = float(t["entry_credit"])
                if entry_credit > 0:
                    unrealized_pnl = round(entry_credit - mark_credit, 4)
                    pnl_pct = round((entry_credit - mark_credit) / entry_credit * 100, 1)
                else:
                    unrealized_pnl = round(-mark_credit, 4)
                    pnl_pct = 0.0

                print(f"    {t['spread_type']:20} K={t['short_strike']:g}/{t['long_strike']:g}  "
                      f"mark=${mark_credit:.2f}  entry=${entry_credit:.2f}  "
                      f"P&L=${unrealized_pnl:+.2f} ({pnl_pct:+.0f}%)  DTE={dte}")

                write_mark(conn, int(t["id"]), mark_date_str, mark_credit,
                           float(spot), unrealized_pnl, pnl_pct, dte, args.dry_run)
                n_ok += 1

        if not args.dry_run:
            conn.commit()

        print()
        print("=" * 72)
        print(f"  Marked {n_ok} / {len(trades)} trades   ({n_failed} failed)")
        if not args.dry_run and n_ok > 0:
            print(f"  Wrote spread_score_daily rows for mark_date={mark_date_str}")
        print("=" * 72)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
