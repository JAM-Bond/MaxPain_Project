"""Query helpers for the Positions page (Open + Closed tabs)."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH  # noqa: E402


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def open_positions_df() -> pd.DataFrame:
    """Build the Open tab dataframe by combining live close_helper rows with
    DB metadata + a simple health calc.
    """
    from scripts.monitor.close_helper import build_close_block
    block = build_close_block()
    rows = block.get("rows", [])
    if not rows:
        return pd.DataFrame()

    today = date.today()
    health_map = _per_name_health()

    records = []
    for r in rows:
        try:
            opex_date_obj = datetime.strptime(r.opex_date, "%Y-%m-%d").date()
            dte = (opex_date_obj - today).days
        except Exception:
            dte = None

        # T-21 management cue (TastyTrade-canonical). long_put exempt
        # (single-leg debit, no roll); zebra/credit verticals all on the rule.
        if r.spread_type == "long_put" or dte is None:
            t21_emoji, t21_label = "", ""
        elif dte > 25:
            t21_emoji, t21_label = "", ""
        elif dte > 21:
            t21_emoji, t21_label = "🟡", f"in {dte - 21}d"
        elif dte == 21:
            t21_emoji, t21_label = "🔴", "today"
        else:
            t21_emoji, t21_label = "🔴", f"{21 - dte}d past"
        t21_str = f"{t21_emoji} {t21_label}".strip() if t21_emoji else "—"

        capture = r.capture_at_mid
        h = health_map.get(r.symbol, ("⚪", "n/a"))
        health_emoji, health_detail = h

        # Stop-loss policy per feedback_loss_cap_discipline.md:
        # "max realized loss ≤ 2× target win" → for credit spreads with a
        # 50%-capture target, that's loss = entry_credit, so the buy-back
        # GTC trigger sits at 2× entry_credit. Doesn't apply to long_put
        # or zebra (debit structures are already capped at the debit paid).
        is_credit_vert = r.spread_type.startswith(("bull_put", "bear_call"))
        if is_credit_vert and r.entry_credit > 0:
            stop_trigger = round(r.entry_credit * 2.0, 2)
            stop_dollar = round(stop_trigger * 100 * r.shares, 0)
            # Status: how close current mid is to the stop. Path is
            # entry_credit (0% loss) → stop_trigger (100% loss = -100% capture).
            # Negative capture (mid > entry) means we're toward the stop.
            pct_to_stop = max(0.0, (-capture)) * 100  # 0% at entry, 100% at trigger
            if capture < -0.75:        stop_status = "🔴"
            elif capture < -0.50:      stop_status = "🟡"
            else:                       stop_status = "🟢"
            stop_status_str = f"{stop_status}  ${stop_trigger:.2f}"
        else:
            stop_trigger = None
            stop_dollar = None
            stop_status_str = "—"
            pct_to_stop = None

        # Sort priority: T-21 hits to the top, then approaching, then by capture
        if t21_emoji == "🔴":   t21_sort = 0
        elif t21_emoji == "🟡": t21_sort = 1
        else:                    t21_sort = 2

        records.append({
            "Symbol":      r.symbol,
            "Structure":   r.spread_type,
            "Strikes":     f"{r.short_strike:g}/{r.long_strike:g}",
            "Qty":         r.shares,
            "OpEx":        r.opex_date,
            "DTE":         dte,
            "T-21":        t21_str,
            "Entry":       round(r.entry_credit, 2),
            "Mid close":   round(r.mid_close, 2),
            "Limit close": round(r.limit_close, 2),
            "Natural":     round(r.natural_close, 2),
            "P/L @ limit": round(r.pnl_at_limit, 0),
            "P/L @ mid":   round(r.pnl_at_mid, 0),
            "Capture %":   round(capture * 100, 1),
            "Health":      health_emoji,
            "Stop @":      stop_status_str,
            "Liq":         "⚠ WIDE" if r.wide_warning else "",
            "id":          r.id,
            "_health_detail": health_detail,
            "_natural_pnl": round(r.pnl_at_natural, 0),
            "_stop_trigger": stop_trigger,
            "_stop_dollar": stop_dollar,
            "_pct_to_stop": round(pct_to_stop, 0) if pct_to_stop is not None else None,
            "_t21_sort":   t21_sort,
            "_t21_emoji":  t21_emoji,
        })

    df = pd.DataFrame(records)
    df = df.sort_values(
        ["_t21_sort", "Capture %"], ascending=[True, False]
    ).reset_index(drop=True)
    return df


def _per_name_health() -> dict[str, tuple[str, str]]:
    """Compute per-position health emoji from spot vs 200-DMA proximity.

    Bull-thesis positions (bull_put, zebra, long_call) → 🟢 if spot ≥ 200-DMA + 3%,
    🟡 within ±3%, 🔴 below trend. Bear-thesis positions (bear_call, long_put)
    are inverted. IF gets 🟡 if within 3% (deadband bad for long-vol) else 🟢.

    Reused from `scripts.qualifier.cycle_qualifier.bull_put_ma_pct` so the
    200-DMA computation stays consistent with the qualifier and Rule #1 gate.
    """
    out: dict[str, tuple[str, str]] = {}
    try:
        from scripts.qualifier.cycle_qualifier import bull_put_ma_pct
    except Exception:
        return out

    with _conn() as c:
        rows = c.execute("""
            SELECT DISTINCT symbol, spread_type
            FROM spread_score_trades
            WHERE placed = 1 AND status = 'open'
        """).fetchall()
    bullish = ("bull_put", "zebra_tier1", "zebra_tier2", "zebra_protected", "long_call")
    bearish = ("bear_call", "long_put")
    for r in rows:
        sym, st = r["symbol"], r["spread_type"]
        try:
            ma_pct = bull_put_ma_pct(sym)
        except Exception:
            ma_pct = None
        if ma_pct is None:
            out[sym] = ("⚪", "n/a")
            continue
        detail = f"{ma_pct*100:+.1f}% vs 200-DMA"
        if st in bearish:
            # Bearish thesis: WANT spot below 200-DMA
            if ma_pct <= -0.03: emoji = "🟢"
            elif ma_pct < 0.03: emoji = "🟡"
            else: emoji = "🔴"
        elif st.startswith("inverted_fly"):
            # Long-vol: want movement away from center
            emoji = "🟡" if abs(ma_pct) < 0.03 else "🟢"
        else:
            # Default bullish thesis
            if ma_pct >= 0.03: emoji = "🟢"
            elif ma_pct > -0.03: emoji = "🟡"
            else: emoji = "🔴"
        out[sym] = (emoji, detail)
    return out


def closed_positions_df(opex_filter: str | None = None) -> pd.DataFrame:
    """Pull placed=1 closed trades with realized fields + computed columns."""
    sql = """
        SELECT id, symbol, spread_type AS structure,
               short_strike, long_strike, shares,
               opex_date, entry_date, exit_date,
               entry_credit, exit_credit, final_pnl,
               qualifier_run_date, target_hit_date
        FROM spread_score_trades
        WHERE placed = 1 AND status = 'closed'
    """
    params: list = []
    if opex_filter and opex_filter != "All":
        sql += " AND opex_date = ?"
        params.append(opex_filter)
    sql += " ORDER BY opex_date DESC, exit_date DESC"

    with _conn() as c:
        df = pd.read_sql_query(sql, c, params=params)
    if df.empty:
        return df

    df["Days held"] = df.apply(_days_held, axis=1)
    df["% captured"] = df.apply(_capture, axis=1).round(1)
    df["Exit type"] = df.apply(_classify_exit, axis=1)
    df["Off-script"] = df["qualifier_run_date"].isna()
    df["Strikes"] = df.apply(
        lambda r: f"{r['short_strike']:g}/{r['long_strike']:g}", axis=1
    )
    df["entry_credit"] = df["entry_credit"].round(2)
    df["exit_credit"] = df["exit_credit"].round(2)
    df["final_pnl"] = df["final_pnl"].round(0)

    df = df.rename(columns={
        "symbol": "Symbol", "structure": "Structure",
        "shares": "Qty", "opex_date": "OpEx",
        "entry_date": "Entry date", "exit_date": "Exit date",
        "entry_credit": "Entry", "exit_credit": "Exit",
        "final_pnl": "Realized P/L",
    })
    df = df.sort_values("% captured", ascending=False).reset_index(drop=True)
    return df


def closed_opex_options() -> list[str]:
    """Distinct OpEx dates with closed trades (newest first)."""
    with _conn() as c:
        rows = c.execute("""
            SELECT DISTINCT opex_date FROM spread_score_trades
            WHERE placed = 1 AND status = 'closed' AND opex_date IS NOT NULL
            ORDER BY opex_date DESC
        """).fetchall()
    return [r["opex_date"] for r in rows]


def _days_held(row) -> int | None:
    try:
        e = datetime.strptime(row["entry_date"], "%Y-%m-%d").date()
        x = datetime.strptime(row["exit_date"], "%Y-%m-%d").date()
        return (x - e).days
    except Exception:
        return None


def _capture(row) -> float | None:
    """% captured. Credit positions: (entry-exit)/entry. Debits (negative entry):
    profit comes from exit-credit > entry-debit; capture = (entry-exit)/abs(entry)."""
    try:
        entry = float(row["entry_credit"])
        exit_ = float(row["exit_credit"]) if row["exit_credit"] is not None else None
        if exit_ is None or entry == 0:
            return None
        if entry > 0:
            # credit position (bull_put, bear_call): close cost (exit) below entry = win
            return (entry - exit_) / entry * 100
        # Debit position (long_put, zebra): entry stored as negative.
        # Profit when exit_credit > abs(entry). Capture = pnl / max-loss-equivalent.
        return (exit_ - abs(entry)) / abs(entry) * 100
    except Exception:
        return None


def _classify_exit(row) -> str:
    """Best-effort exit type label.
    - "T-21 mgmt" if exit was 14-25 days before opex AND the trade had ≥30 DTE at entry
    - "Profit target" if target_hit_date is set and exit happened that day or just after
    - "Expiry" if exit_date == opex_date
    - "Manual close" otherwise
    """
    try:
        opex = datetime.strptime(row["opex_date"], "%Y-%m-%d").date()
        exit_d = datetime.strptime(row["exit_date"], "%Y-%m-%d").date()
        entry_d = datetime.strptime(row["entry_date"], "%Y-%m-%d").date()
    except Exception:
        return "—"

    if exit_d == opex:
        return "Expiry"
    if row.get("target_hit_date") and exit_d.isoformat() >= str(row["target_hit_date"]):
        return "Profit target"
    days_to_opex = (opex - exit_d).days
    days_held = (exit_d - entry_d).days
    if 14 <= days_to_opex <= 25 and days_held > 5:
        return "T-21 mgmt"
    return "Manual close"
