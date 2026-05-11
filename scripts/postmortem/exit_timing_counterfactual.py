"""Exit-timing counterfactual — "did I exit too early?"

For each closed credit spread in a given OpEx cycle, compute what the P/L
would have been if held to expiry (intrinsic value vs. strikes at OpEx
close), and compare to the actual realized P/L.

  delta_held = held_to_expiry_pnl − actual_final_pnl

  delta > 0  → exited too early (held would have been better)
  delta < 0  → exit was the right call (held would have hurt)

V1 scope: bull_put + bear_call only (intrinsic math is unambiguous for
2-leg credit verticals). ZEBRA / inverted_fly / long_put can be added
later if the user wants.

Anchors:
- Phase 1 (this script): held-to-expiry only (free spot data via yfinance).
- Phase 2 (future): T-21 counterfactual + 50%-profit-target counterfactual,
  both of which need historical option-chain data (ORATS replay).

Usage:
  python3.11 -m scripts.postmortem.exit_timing_counterfactual --opex 2026-05-15
  python3.11 -m scripts.postmortem.exit_timing_counterfactual --opex 2026-05-15 --as-of 2026-05-08

If `--as-of` is in the future or OpEx hasn't happened yet, the script
falls back to the most recent close available. The counterfactual is
labeled accordingly ("held to as-of YYYY-MM-DD" vs "held to expiry").
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"


def _intrinsic_at_spot(spread_type: str, short_k: float, long_k: float, spot: float) -> float:
    """Per-share intrinsic value of the spread at a given spot.
    bull_put = short higher put, long lower put. Width = short - long.
    bear_call = short lower call, long higher call. Width = long - short.
    """
    if spread_type.startswith("bull_put"):
        width = short_k - long_k
        if spot >= short_k:
            return 0.0
        if spot <= long_k:
            return width
        return short_k - spot
    if spread_type.startswith("bear_call"):
        width = long_k - short_k
        if spot <= short_k:
            return 0.0
        if spot >= long_k:
            return width
        return spot - short_k
    raise ValueError(f"Unsupported spread_type {spread_type}")


def _fetch_close(symbol: str, target_date: date) -> tuple[float | None, date | None]:
    """Return (close_price, close_date) for the most recent trading-day close
    on or before target_date. None if no data."""
    start = (target_date - pd.Timedelta(days=10)).isoformat() if hasattr(target_date, 'isoformat') else target_date
    end = (target_date + pd.Timedelta(days=1))
    try:
        h = yf.download(
            symbol, start=start, end=end.isoformat() if hasattr(end, 'isoformat') else end,
            progress=False, auto_adjust=False,
        )
    except Exception:
        return None, None
    if h is None or h.empty:
        return None, None
    h = h[h.index.date <= target_date]
    if h.empty:
        return None, None
    last = h.iloc[-1]
    close = float(last["Close"].iloc[0]) if hasattr(last["Close"], "iloc") else float(last["Close"])
    return close, h.index[-1].date()


def _load_closed_trades(opex: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, symbol, spread_type, short_strike, long_strike,
               entry_credit, exit_credit, exit_date, final_pnl, shares,
               opex_date, entry_date
        FROM spread_score_trades
        WHERE status = 'closed' AND opex_date = ?
          AND (spread_type LIKE 'bull_put%' OR spread_type LIKE 'bear_call%')
        ORDER BY symbol
    """, (opex,)).fetchall()
    conn.close()
    return rows


def run(opex: str, as_of: date | None = None) -> dict:
    rows = _load_closed_trades(opex)
    if not rows:
        return {"ok": False, "error": f"No closed credit verticals for OpEx {opex}"}

    opex_d = datetime.strptime(opex, "%Y-%m-%d").date()
    target = as_of or opex_d
    today = date.today()
    if target > today:
        target = today  # fall back to today's close if requested date is in the future

    spot_cache: dict[tuple[str, date], tuple[float | None, date | None]] = {}
    out_rows = []
    for r in rows:
        sym = r["symbol"]
        key = (sym, target)
        if key not in spot_cache:
            spot_cache[key] = _fetch_close(sym, target)
        spot, spot_d = spot_cache[key]
        if spot is None:
            out_rows.append({
                "id": r["id"], "symbol": sym, "spread_type": r["spread_type"],
                "strikes": f"{r['short_strike']:g}/{r['long_strike']:g}",
                "shares": r["shares"] or 1,
                "actual_pnl": r["final_pnl"],
                "actual_exit_date": r["exit_date"],
                "spot_at_anchor": None, "intrinsic": None,
                "held_pnl": None, "delta": None,
                "note": f"no spot data for {sym} on/before {target}",
            })
            continue

        intrinsic = _intrinsic_at_spot(
            r["spread_type"], r["short_strike"], r["long_strike"], spot
        )
        shares = r["shares"] or 1
        held_pnl = (float(r["entry_credit"]) - intrinsic) * 100 * shares
        actual = float(r["final_pnl"]) if r["final_pnl"] is not None else 0.0
        delta = held_pnl - actual

        out_rows.append({
            "id": r["id"], "symbol": sym, "spread_type": r["spread_type"],
            "strikes": f"{r['short_strike']:g}/{r['long_strike']:g}",
            "shares": shares,
            "actual_pnl": round(actual, 0),
            "actual_exit_date": r["exit_date"],
            "spot_at_anchor": round(spot, 2),
            "spot_date": str(spot_d),
            "intrinsic": round(intrinsic, 2),
            "held_pnl": round(held_pnl, 0),
            "delta": round(delta, 0),
            "note": "",
        })

    label = "held-to-expiry" if target == opex_d else f"held-to-{target}"
    return {
        "ok": True,
        "opex": opex,
        "anchor_date": str(target),
        "label": label,
        "n": len(out_rows),
        "rows": out_rows,
    }


def render(result: dict) -> str:
    if not result.get("ok"):
        return f"ERROR: {result.get('error')}"

    rows = result["rows"]
    label = result["label"]
    lines = [
        f"EXIT-TIMING COUNTERFACTUAL — OpEx {result['opex']} · {label} (anchor {result['anchor_date']})",
        f"  N = {result['n']} closed credit verticals (bull_put + bear_call)",
        "",
        f"  {'id':>4} {'sym':<6} {'structure':<12} {'strikes':>11} {'qty':>3}  "
        f"{'actual':>8} {'spot':>7} {'intr':>5} {'held':>8} {'Δ':>8}  exit→anchor",
        "  " + "─" * 110,
    ]

    rows_with = [r for r in rows if r.get("delta") is not None]
    rows_with.sort(key=lambda r: r["delta"], reverse=True)
    rows_skipped = [r for r in rows if r.get("delta") is None]

    for r in rows_with:
        flag = "↑" if r["delta"] > 0 else ("↓" if r["delta"] < 0 else " ")
        lines.append(
            f"  {r['id']:>4} {r['symbol']:<6} {r['spread_type']:<12} "
            f"{r['strikes']:>11} {r['shares']:>3}  "
            f"${r['actual_pnl']:>+6.0f} ${r['spot_at_anchor']:>6.2f} "
            f"${r['intrinsic']:>4.2f} ${r['held_pnl']:>+6.0f} ${r['delta']:>+6.0f} {flag}  "
            f"{r['actual_exit_date']}→{r['spot_date']}"
        )

    if rows_skipped:
        lines.append("")
        lines.append("Skipped (no spot data):")
        for r in rows_skipped:
            lines.append(f"  • {r['symbol']} id={r['id']}: {r['note']}")

    if rows_with:
        sum_actual = sum(r["actual_pnl"] for r in rows_with)
        sum_held = sum(r["held_pnl"] for r in rows_with)
        sum_delta = sum(r["delta"] for r in rows_with)
        early_count = sum(1 for r in rows_with if r["delta"] > 0)
        right_count = sum(1 for r in rows_with if r["delta"] < 0)
        lines.append("")
        lines.append("Summary:")
        lines.append(f"  Total actual P/L:      ${sum_actual:>+8.0f}")
        lines.append(f"  Total held-to-anchor:  ${sum_held:>+8.0f}")
        lines.append(f"  Net delta (held-act):  ${sum_delta:>+8.0f}")
        lines.append(f"  Exited too early:      {early_count} / {len(rows_with)}")
        lines.append(f"  Exit was correct:      {right_count} / {len(rows_with)}")

    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Exit-timing counterfactual for closed credit verticals.")
    ap.add_argument("--opex", required=True, help="OpEx date YYYY-MM-DD (e.g. 2026-05-15)")
    ap.add_argument("--as-of", default=None,
                    help="Anchor date YYYY-MM-DD. Default = OpEx date. Future dates fall back to today.")
    args = ap.parse_args()
    as_of_d = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else None
    result = run(args.opex, as_of=as_of_d)
    print(render(result))
