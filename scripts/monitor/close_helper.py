"""Close-side helper — current mid/natural/recommended close per open position.

Solves the GS-yesterday pattern where Schwab marks showed +$150 P/L at theoretical
mid but no fill was available there. Renders, for every placed=1 open position:

  - Current mid close, natural close (worst-case), recommended GTC limit
  - $ P/L and % capture at each price point
  - Liquidity flag if any leg's bid-ask exceeds the WIDE threshold

Reads from spread_score_trades and live Schwab chains. Caches chain fetches
by (symbol, expiry) so a multi-position alert only hits Schwab once per
(symbol, expiry) pair.

Output is a dict {ok, text, html, rows, error} suitable for embedding in
the daily alert or invoking from CLI.

Supported structures:
  - bull_put / bear_call          (2-leg credit close)
  - inverted_fly_pair/single/...  (4-leg debit, close = sell longs/buy shorts)
  - zebra_tier1 / zebra_tier2     (3-leg: 2 long ITM calls + 1 short ATM call)
  - long_put                      (single-leg debit, close = sell at bid)
"""
from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH as METAL_DB  # noqa: E402
from lib.schwab_options import fetch_chain_with_greeks  # noqa: E402

# Mirror the construction-block patient-trader heuristics. Always sit on the
# favorable side of mid; trade fill speed for fill quality.
_LIMIT_SLIP_CLOSE_DEBIT = 0.05   # closing credit spread (buy back) → bid ≤ mid − $0.05
_LIMIT_SLIP_CLOSE_CREDIT = 0.05  # closing a debit (sell out)   → ask ≥ mid + $0.05
_WIDE_BIDASK_RATIO = 0.20


@dataclass
class CloseRow:
    id: int
    symbol: str
    spread_type: str
    short_strike: float
    long_strike: float
    opex_date: str
    entry_credit: float       # signed: positive = credit, negative = debit
    shares: int
    spot: float
    mid_close: float          # what you pay/receive at theoretical mid
    natural_close: float      # what you pay/receive at natural worst
    limit_close: float        # recommended GTC limit
    pnl_at_mid: float         # $ P/L if filled at mid
    pnl_at_natural: float
    pnl_at_limit: float
    capture_at_mid: float     # fraction of max profit captured (credit-spread convention)
    wide_warning: Optional[str]
    error: Optional[str] = None


def _open_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute("""
        SELECT id, symbol, opex_date, spread_type, short_strike, long_strike,
               entry_credit, entry_date, shares
        FROM spread_score_trades
        WHERE placed = 1 AND status = 'open'
        ORDER BY symbol, opex_date, spread_type
    """).fetchall()


def _normalize_shares(s) -> int:
    if s is None:
        return 1
    try:
        return max(1, int(s))
    except Exception:
        return 1


def _put_ba(chain, strike: float) -> tuple[float, float]:
    m = chain[chain["strike"] == strike]
    if m.empty:
        return 0.0, 0.0
    r = m.iloc[0]
    return float(r.get("pBidPx", 0) or 0), float(r.get("pAskPx", 0) or 0)


def _call_ba(chain, strike: float) -> tuple[float, float]:
    m = chain[chain["strike"] == strike]
    if m.empty:
        return 0.0, 0.0
    r = m.iloc[0]
    return float(r.get("cBidPx", 0) or 0), float(r.get("cAskPx", 0) or 0)


def _wide_check(legs: list[tuple[float, float, float]]) -> Optional[str]:
    """legs = [(bid, mid, ask), ...] — return WIDE warning string or None."""
    worst = 0.0
    for bid, mid, ask in legs:
        if mid <= 0 or bid <= 0 or ask <= 0:
            continue
        ratio = (ask - bid) / mid
        worst = max(worst, ratio)
    if worst > _WIDE_BIDASK_RATIO:
        return f"WIDE ({worst*100:.0f}%)"
    return None


def _vertical_close(row, chain, spot: float) -> CloseRow:
    """Close pricing for bull_put or bear_call credit spread."""
    short_k = row["short_strike"]
    long_k = row["long_strike"]
    is_bull_put = row["spread_type"].startswith("bull_put")
    if is_bull_put:
        short_bid, short_ask = _put_ba(chain, short_k)
        long_bid, long_ask = _put_ba(chain, long_k)
    else:
        short_bid, short_ask = _call_ba(chain, short_k)
        long_bid, long_ask = _call_ba(chain, long_k)

    short_mid = (short_bid + short_ask) / 2 if (short_bid > 0 and short_ask > 0) else 0
    long_mid = (long_bid + long_ask) / 2 if (long_bid > 0 and long_ask > 0) else 0

    # To close a credit spread: BUY back short, SELL long. Cost = pay.
    mid_close = short_mid - long_mid
    natural_close = short_ask - long_bid  # buy short@ask, sell long@bid (worst)
    limit_close = mid_close - _LIMIT_SLIP_CLOSE_DEBIT  # patient buyer — bid below mid

    entry_credit = float(row["entry_credit"])
    shares = _normalize_shares(row["shares"])

    wing = abs(short_k - long_k)
    pnl_mid = (entry_credit - mid_close) * 100 * shares
    pnl_natural = (entry_credit - natural_close) * 100 * shares
    pnl_limit = (entry_credit - limit_close) * 100 * shares
    # capture = (entry_credit - mid_close) / entry_credit
    capture = (entry_credit - mid_close) / entry_credit if entry_credit > 0 else 0.0

    legs_ba = [(short_bid, short_mid, short_ask), (long_bid, long_mid, long_ask)]
    wide = _wide_check(legs_ba)

    return CloseRow(
        id=row["id"], symbol=row["symbol"], spread_type=row["spread_type"],
        short_strike=short_k, long_strike=long_k, opex_date=row["opex_date"],
        entry_credit=entry_credit, shares=shares, spot=spot,
        mid_close=mid_close, natural_close=natural_close, limit_close=limit_close,
        pnl_at_mid=pnl_mid, pnl_at_natural=pnl_natural, pnl_at_limit=pnl_limit,
        capture_at_mid=capture, wide_warning=wide,
    )


def _zebra_close(row, chain, spot: float) -> CloseRow:
    """Close pricing for ZEBRA: 2 long ITM calls + 1 short ATM call.
    The DB stores long_strike = ITM long, short_strike = ATM short (per opener).
    Entry was a debit; close = sell longs at bid, buy short at ask = collect credit.
    """
    long_k = row["long_strike"]
    short_k = row["short_strike"]
    long_bid, long_ask = _call_ba(chain, long_k)
    short_bid, short_ask = _call_ba(chain, short_k)
    long_mid = (long_bid + long_ask) / 2 if (long_bid > 0 and long_ask > 0) else 0
    short_mid = (short_bid + short_ask) / 2 if (short_bid > 0 and short_ask > 0) else 0

    # Close credit (we COLLECT this): sell 2 longs @ bid, buy 1 short @ ask
    mid_close_credit = 2 * long_mid - short_mid
    natural_close_credit = 2 * long_bid - short_ask  # worst (less collected)
    limit_close_credit = mid_close_credit + _LIMIT_SLIP_CLOSE_CREDIT  # patient seller — ask above mid

    # entry_credit is NEGATIVE for debit positions (e.g. -9.16 for KRE zebra)
    entry_debit = -float(row["entry_credit"])  # positive number
    shares = _normalize_shares(row["shares"])

    # P/L = (close_credit - entry_debit) × 100 × shares
    pnl_mid = (mid_close_credit - entry_debit) * 100 * shares
    pnl_natural = (natural_close_credit - entry_debit) * 100 * shares
    pnl_limit = (limit_close_credit - entry_debit) * 100 * shares
    capture = (mid_close_credit - entry_debit) / entry_debit if entry_debit > 0 else 0.0

    legs_ba = [(long_bid, long_mid, long_ask), (short_bid, short_mid, short_ask)]
    wide = _wide_check(legs_ba)

    return CloseRow(
        id=row["id"], symbol=row["symbol"], spread_type=row["spread_type"],
        short_strike=short_k, long_strike=long_k, opex_date=row["opex_date"],
        entry_credit=float(row["entry_credit"]), shares=shares, spot=spot,
        mid_close=mid_close_credit, natural_close=natural_close_credit,
        limit_close=limit_close_credit,
        pnl_at_mid=pnl_mid, pnl_at_natural=pnl_natural, pnl_at_limit=pnl_limit,
        capture_at_mid=capture, wide_warning=wide,
    )


def _long_put_close(row, chain, spot: float) -> CloseRow:
    """Close pricing for a long_put (single-leg debit, e.g. SOXX 340 JAN-2027)."""
    k = row["long_strike"]  # convention: long_strike holds the strike for long_put
    bid, ask = _put_ba(chain, k)
    mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else 0
    natural = bid                            # sell at bid (worst)
    limit = mid + _LIMIT_SLIP_CLOSE_CREDIT   # patient seller — ask above mid

    entry_debit = -float(row["entry_credit"])  # SOXX entry stored as -15.15 (debit)
    shares = _normalize_shares(row["shares"])

    pnl_mid = (mid - entry_debit) * 100 * shares
    pnl_natural = (natural - entry_debit) * 100 * shares
    pnl_limit = (limit - entry_debit) * 100 * shares
    capture = (mid - entry_debit) / entry_debit if entry_debit > 0 else 0.0
    wide = _wide_check([(bid, mid, ask)])
    return CloseRow(
        id=row["id"], symbol=row["symbol"], spread_type=row["spread_type"],
        short_strike=row["short_strike"], long_strike=k, opex_date=row["opex_date"],
        entry_credit=float(row["entry_credit"]), shares=shares, spot=spot,
        mid_close=mid, natural_close=natural, limit_close=limit,
        pnl_at_mid=pnl_mid, pnl_at_natural=pnl_natural, pnl_at_limit=pnl_limit,
        capture_at_mid=capture, wide_warning=wide,
    )


def build_close_block(db_path: Path = METAL_DB) -> dict:
    """Top-level entry: compute close pricing for every placed=1 open position.

    Returns {ok, text, html, rows, errors}. Caches chain fetches by (symbol,
    expiry) so a multi-position book hits Schwab once per pair.
    """
    conn = sqlite3.connect(str(db_path))
    positions = _open_positions(conn)
    conn.close()

    if not positions:
        return {"ok": True, "text": "No open placed positions.", "html": "", "rows": [], "errors": []}

    chain_cache: dict[tuple[str, str], tuple] = {}
    rows: list[CloseRow] = []
    errors: list[str] = []

    for p in positions:
        sym, opex = p["symbol"], p["opex_date"]
        if (sym, opex) not in chain_cache:
            try:
                chain_cache[(sym, opex)] = fetch_chain_with_greeks(sym, opex)
            except Exception as e:
                errors.append(f"{sym} {opex}: chain fetch failed — {e}")
                chain_cache[(sym, opex)] = (None, None)
        chain, spot = chain_cache[(sym, opex)]
        if chain is None or chain.empty:
            errors.append(f"{sym} {opex}: empty chain")
            continue

        st = p["spread_type"]
        try:
            if st.startswith("bull_put") or st.startswith("bear_call"):
                rows.append(_vertical_close(p, chain, spot))
            elif st.startswith("zebra"):
                rows.append(_zebra_close(p, chain, spot))
            elif st == "long_put":
                rows.append(_long_put_close(p, chain, spot))
            elif st.startswith("inverted_fly"):
                # 4-leg IF — defer for now; rare in current book
                errors.append(f"{sym} id={p['id']}: inverted_fly close pricing not yet implemented")
            else:
                errors.append(f"{sym} id={p['id']}: unsupported spread_type {st}")
        except Exception as e:
            errors.append(f"{sym} id={p['id']}: {e}")

    # Sort by capture (credit spreads sort cleanly; debits use the same field)
    rows.sort(key=lambda r: r.capture_at_mid, reverse=True)

    return {"ok": True, "text": _render_text(rows, errors),
            "html": _render_html(rows, errors), "rows": rows, "errors": errors}


def _capture_band(c: float) -> str:
    if c >= 0.50: return "🟢"
    if c >= 0.25: return "🟡"
    if c >= 0.00: return "▪️"
    return "🔴"


def _dte_for(opex_date: str) -> Optional[int]:
    try:
        d = datetime.strptime(opex_date, "%Y-%m-%d").date()
        return (d - date.today()).days
    except Exception:
        return None


def _t21_band(dte: Optional[int]) -> tuple[str, str]:
    """T-21 management state. TastyTrade-canonical: at 21 DTE the gamma:theta
    ratio flips against you — close/roll regardless of capture %.
       DTE > 25  → quiet ("", "")
       DTE 22-25 → 🟡 approaching
       DTE ≤ 21  → 🔴 hit/past
    """
    if dte is None:
        return ("", "")
    if dte > 25:
        return ("", "")
    if dte > 21:
        return ("🟡", f"T-21 in {dte - 21}d")
    if dte == 21:
        return ("🔴", "T-21 today — close/roll")
    return ("🔴", f"T-21 hit ({21 - dte}d past) — close/roll now")


def _t21_actions(rows: list[CloseRow]) -> list[tuple[CloseRow, int, str, str]]:
    """Subset of rows where T-21 is approaching or past. Sorted DTE asc
    (most-urgent first). Excludes long_put (single-leg debit, no roll cue)
    but keeps zebra (T-21 still applies to short call leg)."""
    out = []
    for r in rows:
        if r.spread_type == "long_put":
            continue
        dte = _dte_for(r.opex_date)
        emoji, label = _t21_band(dte)
        if not emoji:
            continue
        out.append((r, dte if dte is not None else 0, emoji, label))
    out.sort(key=lambda x: x[1])
    return out


def _render_text(rows: list[CloseRow], errors: list[str]) -> str:
    if not rows:
        return "No closeable positions."
    lines = []

    t21 = _t21_actions(rows)
    if t21:
        lines.append("T-21 MANAGEMENT — close or roll regardless of capture %")
        lines.append("")
        for r, dte, emoji, label in t21:
            strikes = f"{r.short_strike:g}/{r.long_strike:g}"
            lines.append(
                f"  {emoji} {r.symbol:<6} id={r.id:<3} {r.spread_type:<14} "
                f"{r.opex_date} {strikes:>11} (DTE {dte})  — {label}"
            )
        lines.append("")

    lines.append("OPEN POSITIONS — close-side mark (sorted by capture %)")
    lines.append("")
    lines.append(f"  {'id':>4} {'sym':<6} {'OpEx':<10} {'structure':<14} "
                 f"{'strikes':>11} {'qty':>3}  "
                 f"{'entry':>6} {'mid':>6} {'natur':>6} {'limit':>6}  "
                 f"{'$@mid':>7} {'$@nat':>7} {'$@lim':>7}  {'cap':>5}  liq")
    lines.append("  " + "─" * 132)
    for r in rows:
        strikes = f"{r.short_strike:g}/{r.long_strike:g}"
        liq = f"⚠ {r.wide_warning}" if r.wide_warning else ""
        lines.append(
            f"  {_capture_band(r.capture_at_mid)} {r.id:>2} {r.symbol:<6} "
            f"{r.opex_date:<10} {r.spread_type:<14} "
            f"{strikes:>11} {r.shares:>3}  "
            f"${r.entry_credit:>5.2f} ${r.mid_close:>5.2f} ${r.natural_close:>5.2f} ${r.limit_close:>5.2f}  "
            f"${r.pnl_at_mid:>+6.0f} ${r.pnl_at_natural:>+6.0f} ${r.pnl_at_limit:>+6.0f}  "
            f"{r.capture_at_mid*100:>+4.0f}%  {liq}"
        )
    if errors:
        lines.append("")
        lines.append("Errors:")
        for e in errors:
            lines.append(f"  • {e}")
    return "\n".join(lines)


def _render_html(rows: list[CloseRow], errors: list[str]) -> str:
    if not rows:
        return "<p>No closeable positions.</p>"

    t21_html = ""
    t21 = _t21_actions(rows)
    if t21:
        t21_rows = []
        for r, dte, emoji, label in t21:
            color = "#a00" if "hit" in label or "today" in label else "#a80"
            t21_rows.append(
                f"<tr>"
                f"<td>{emoji}</td>"
                f"<td><b>{r.symbol}</b></td>"
                f"<td align=right>id={r.id}</td>"
                f"<td>{r.spread_type}</td>"
                f"<td>{r.opex_date}</td>"
                f"<td align=right>{r.short_strike:g}/{r.long_strike:g}</td>"
                f"<td align=center>DTE {dte}</td>"
                f"<td style='color:{color};font-weight:bold'>{label}</td>"
                f"</tr>"
            )
        t21_html = (
            "<div style='font-family:Menlo,Consolas,monospace;font-size:12px;"
            "margin-bottom:10px'>"
            "<div style='font-weight:bold;margin-bottom:4px'>"
            "T-21 MANAGEMENT — close or roll regardless of capture %</div>"
            "<table style='border-collapse:collapse;font-size:12px'>"
            f"<tbody>{''.join(t21_rows)}</tbody></table></div>"
        )

    head = ("<tr style='background:#eee'>"
            "<th>id</th><th>sym</th><th>OpEx</th><th>structure</th>"
            "<th>strikes</th><th>qty</th>"
            "<th>entry</th><th>mid_cls</th><th>natural</th><th>limit</th>"
            "<th>$@mid</th><th>$@nat</th><th>$@lim</th><th>cap%</th><th>liq</th></tr>")
    body = []
    for r in rows:
        cap_color = ("#0a0" if r.capture_at_mid >= 0.50 else
                     "#a80" if r.capture_at_mid >= 0.25 else
                     "#666" if r.capture_at_mid >= 0.0 else "#a00")
        liq = (f"<span style='color:#a00;font-weight:bold'>⚠ {r.wide_warning}</span>"
               if r.wide_warning else "")
        body.append(
            f"<tr>"
            f"<td align=right>{r.id}</td>"
            f"<td><b>{r.symbol}</b></td>"
            f"<td>{r.opex_date}</td>"
            f"<td>{r.spread_type}</td>"
            f"<td align=right>{r.short_strike:g}/{r.long_strike:g}</td>"
            f"<td align=center>{r.shares}</td>"
            f"<td align=right>${r.entry_credit:.2f}</td>"
            f"<td align=right>${r.mid_close:.2f}</td>"
            f"<td align=right style='color:#888'>${r.natural_close:.2f}</td>"
            f"<td align=right><b>${r.limit_close:.2f}</b></td>"
            f"<td align=right>${r.pnl_at_mid:+.0f}</td>"
            f"<td align=right style='color:#888'>${r.pnl_at_natural:+.0f}</td>"
            f"<td align=right>${r.pnl_at_limit:+.0f}</td>"
            f"<td align=right style='color:{cap_color};font-weight:bold'>"
            f"{r.capture_at_mid*100:+.0f}%</td>"
            f"<td>{liq}</td>"
            f"</tr>"
        )
    err_html = ""
    if errors:
        err_html = ("<div style='font-size:11px;color:#888;margin-top:4px'>"
                    "Errors: " + "; ".join(errors) + "</div>")
    return (
        f"{t21_html}"
        "<div style='font-family:Menlo,Consolas,monospace;font-size:12px'>"
        "<div style='font-weight:bold;margin-bottom:4px'>"
        "OPEN POSITIONS — close-side mark (sorted by capture %)</div>"
        "<table style='border-collapse:collapse;font-size:12px'>"
        f"<thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"
        f"{err_html}</div>"
    )


# ─── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Close-side mark for open positions.")
    ap.add_argument("--html", action="store_true")
    args = ap.parse_args()
    out = build_close_block()
    print(out["html"] if args.html else out["text"])
