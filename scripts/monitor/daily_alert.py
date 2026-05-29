#!/usr/bin/env python3.11
"""
MaxPain v1.7 daily alert
~/MaxPain_Project/scripts/monitor/daily_alert.py

Two-section alert:

  1. REGIME — fires on day-over-day changes in the regime_state table:
     stage transitions, H1 fires, signal flips (200dma cross, IVR cross,
     term flip, VRP flip).
  2. OPEN TRADES — fires only on names with currently-open positions:
     significant underlying moves (vs empirical p95 from
     alert_thresholds), short-strike breach, MTM-based roll trigger,
     50% profit target hit.

If neither section produces any events, the alert prints a one-line
"all quiet" header and exits — no email, no spam.

Cron: 4:45 PM ET on weekdays, after the 4:15 close-price update and
the 4:30 monitor.

Usage:
  python3.11 daily_alert.py             # default daily run
  python3.11 daily_alert.py --verbose   # show all signals, even unchanged
"""
from __future__ import annotations

import argparse
import io
import logging
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data/orats/by_ticker"
EARNINGS_CACHE = ROOT / "data/profile/earnings_calendar_cache.parquet"

# Enrichment imports (lazy: only on construction blocks path)
sys.path.insert(0, str(ROOT))

from lib.db import DB_PATH  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")
log = logging.getLogger("alert")


# ─── Regime section ───────────────────────────────────────────────

def load_recent_regime(conn) -> pd.DataFrame:
    """Last 5 days of regime_state, newest last."""
    df = pd.read_sql(
        "SELECT * FROM regime_state ORDER BY snapshot_date DESC LIMIT 5",
        conn,
    )
    return df.sort_values("snapshot_date").reset_index(drop=True)


def detect_regime_events(df: pd.DataFrame) -> list[str]:
    """Compare today's row to yesterday's row; emit human-readable events."""
    if len(df) < 2:
        return ["(insufficient regime history; need at least 2 days)"]
    today = df.iloc[-1]
    prev = df.iloc[-2]
    events = []

    # Stage transition is the headline
    if today["stage"] != prev["stage"]:
        events.append(
            f"STAGE: {prev['stage']} → {today['stage']} "
            f"(direction: {'tightening' if today['stage'] > prev['stage'] else 'easing'})"
        )

    # Boolean signal flips
    flag_pairs = [
        ("h1_active", "H1 (bear regime)"),
        ("hard_pause_active", "Hard pause"),
        ("soft_downsize_active", "Soft-downsize"),
        ("below_200dma", "SPY below 200dma"),
        ("ivr_high", "SPY IVR > 0.5"),
        ("term_inverted", "Term inverted"),
        ("if_gate_active", "IF gate"),
        ("bull_put_signal_active", "Bull-put signal (contango+VRP>0)"),
    ]
    for col, label in flag_pairs:
        if int(today[col]) != int(prev[col]):
            new_state = "ON" if today[col] else "OFF"
            events.append(f"{label}: {new_state}")

    # Numeric crosses worth flagging
    # SPY closed across 200dma
    if (today["spy_close"] - today["spy_ma200"]) * (prev["spy_close"] - prev["spy_ma200"]) < 0:
        direction = "above" if today["spy_close"] > today["spy_ma200"] else "below"
        events.append(
            f"SPY 200dma cross: closed {direction} (SPY ${today['spy_close']:.2f} vs "
            f"200dma ${today['spy_ma200']:.2f})"
        )

    # Term spread sign change
    if today["spy_term_spread"] is not None and prev["spy_term_spread"] is not None:
        if today["spy_term_spread"] * prev["spy_term_spread"] < 0:
            events.append(
                f"Term spread flipped: {prev['spy_term_spread']:+.4f} → "
                f"{today['spy_term_spread']:+.4f}"
            )

    # VRP sign change
    if today["spy_vrp"] is not None and prev["spy_vrp"] is not None:
        if today["spy_vrp"] * prev["spy_vrp"] < 0:
            events.append(
                f"VRP flipped: {prev['spy_vrp']:+.4f} → {today['spy_vrp']:+.4f}"
            )

    return events


def summarize_regime(df: pd.DataFrame) -> str:
    """One-line current state summary."""
    if df.empty:
        return "(no regime data)"
    t = df.iloc[-1]
    vix_str = f" | VIX {t['spy_vix']:.2f}" if 'spy_vix' in t.index and t['spy_vix'] is not None else ""
    return (f"as of close {t['as_of_close']}: "
            f"stage={t['stage']} | "
            f"SPY ${t['spy_close']:.2f} ({'-' if t['below_200dma'] else '+'}"
            f"{abs(t['spy_pct_to_ma200'])*100:.1f}% vs 200dma) | "
            f"IVR {t['spy_ivr_252']:.2f}{vix_str} | "
            f"H1={'ON' if t['h1_active'] else 'off'} | "
            f"IF gate={'ON' if t['if_gate_active'] else 'off'} | "
            f"BP signal={'ON' if t['bull_put_signal_active'] else 'off'}")


def detect_approaching_thresholds(df: pd.DataFrame) -> list[str]:
    """Flag signals near but not yet over their thresholds.
    Complements detect_regime_events (which fires on flips). This fires
    on values in a buffer zone before the flip would trigger — early warning.
    Suppressed when the threshold has already been crossed (the flip detector
    handles that case).
    """
    if df.empty:
        return []
    t = df.iloc[-1]
    events = []

    pct = t.get("spy_pct_to_ma200")
    if pct is not None:
        if 0 <= pct <= 0.02:
            events.append(
                f"SPY {pct*100:+.2f}% above 200dma — within 2% buffer "
                f"(${t['spy_close']:.2f} vs ${t['spy_ma200']:.2f})"
            )
        elif -0.02 <= pct < 0:
            events.append(
                f"SPY {pct*100:+.2f}% below 200dma — recently broke down "
                f"(${t['spy_close']:.2f} vs ${t['spy_ma200']:.2f})"
            )

    ivr = t.get("spy_ivr_252")
    if ivr is not None:
        if 0.4 <= ivr < 0.5:
            events.append(f"IVR {ivr:.3f} — approaching ivr_high threshold (0.5)")
        elif 0.6 <= ivr < 0.7:
            events.append(f"IVR {ivr:.3f} — approaching soft-downsize trigger (0.7)")

    ts = t.get("spy_term_spread")
    if ts is not None and -0.005 <= ts < 0:
        events.append(
            f"Term spread {ts:+.4f} — within 0.005 of inversion (would flip IF gate ON)"
        )

    vrp = t.get("spy_vrp")
    if vrp is not None:
        if -0.01 <= vrp < 0:
            events.append(f"VRP {vrp:+.4f} — approaching positive (would lift bull_put gate)")
        elif 0 < vrp <= 0.01:
            events.append(f"VRP {vrp:+.4f} — approaching negative (would weaken bull_put gate)")

    vix = t.get("spy_vix") if 'spy_vix' in t.index else None
    if vix is not None:
        if 18 <= vix < 20:
            events.append(f"VIX {vix:.2f} — approaching elevated (20)")
        elif vix >= 25:
            events.append(f"VIX {vix:.2f} — significantly elevated")

    return events


# ─── Open-trade section ────────────────────────────────────────────

def load_open_positions(conn) -> pd.DataFrame:
    """Union of currently-open spread positions and stock trades.

    Spread filter: placed=1 only — algo recommendations (placed=0 from
    spread_score_tracker --mark) are NOT actual positions and shouldn't
    fire alerts. See reference_placed_flag.md.
    """
    spreads = pd.read_sql("""
        SELECT id, symbol, opex_date, spread_type AS structure,
               short_strike, long_strike, width, entry_credit,
               entry_date, entry_price
        FROM spread_score_trades
        WHERE exit_date IS NULL AND placed = 1
    """, conn)
    spreads["source"] = "spread_score_trades"

    try:
        stocks = pd.read_sql("""
            SELECT symbol, opex_date, trade_type AS structure,
                   entry_date, entry_price, shares,
                   short_strike, long_strike, width
            FROM trade_log
            WHERE exit_date IS NULL OR exit_price IS NULL OR exit_price = 0
        """, conn)
        stocks["source"] = "trade_log"
    except Exception:
        stocks = pd.DataFrame()

    parts = [df for df in (spreads, stocks) if not df.empty]
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True, sort=False)


def load_thresholds(conn) -> dict:
    df = pd.read_sql("SELECT ticker, p95 FROM alert_thresholds", conn)
    return dict(zip(df["ticker"], df["p95"]))


# ─── DTE checkpoints section ──────────────────────────────────────────────────

def trading_days_to(opex_date) -> int:
    """Trading days from today to opex_date (exclusive of today). 0 if today is on/past opex."""
    if isinstance(opex_date, str):
        opex_date = pd.to_datetime(opex_date).date()
    today = date.today()
    if today >= opex_date:
        return 0
    return max(0, len(pd.bdate_range(today, opex_date)) - 1)


MARK_STALE_AFTER_DAYS = 2  # warn if latest mark older than this many calendar days


def credit_captured_status(conn, trade_id: int, entry_credit: float) -> dict | None:
    """For credit spreads: status of latest daily mark.

    Return shape:
      {pct, mark_date, stale, error}
        pct        — % of credit captured = (entry - mark) / entry * 100
        mark_date  — date of latest mark in spread_score_daily (str or None)
        stale      — True when mark is older than MARK_STALE_AFTER_DAYS or missing
        error      — human-readable warning string, or None when fresh

    Returns None for non-applicable rows (debit structures like ZEBRA).
    """
    if entry_credit is None or entry_credit <= 0:
        return None  # debit trade — different math
    row = conn.execute(
        "SELECT mark_credit, mark_date FROM spread_score_daily "
        "WHERE trade_id = ? ORDER BY mark_date DESC LIMIT 1",
        (int(trade_id),),
    ).fetchone()
    if row is None or row[0] is None:
        return {
            "pct": None,
            "mark_date": None,
            "stale": True,
            "error": (f"no marks found in spread_score_daily for trade_id={trade_id} — "
                      "profit-target alert cannot fire (mark daemon disabled?)"),
        }
    mark = float(row[0])
    mark_date_str = row[1]
    pct = (entry_credit - mark) / entry_credit * 100
    age_days = None
    try:
        md = pd.to_datetime(mark_date_str).date()
        age_days = (date.today() - md).days
    except Exception:
        age_days = None
    if age_days is not None and age_days > MARK_STALE_AFTER_DAYS:
        return {
            "pct": pct,
            "mark_date": mark_date_str,
            "stale": True,
            "error": (f"latest mark is {age_days}d old (mark_date {mark_date_str}) — "
                      "profit-target alert may be inaccurate (mark daemon disabled?)"),
        }
    return {
        "pct": pct,
        "mark_date": mark_date_str,
        "stale": False,
        "error": None,
    }


def latest_credit_captured_pct(conn, trade_id: int, entry_credit: float) -> float | None:
    """Backward-compat shim: returns just the pct (or None)."""
    s = credit_captured_status(conn, trade_id, entry_credit)
    return s["pct"] if s else None


# ZEBRA is delta-1 stock-replacement: alert at the same 3.5% spot stop used
# for stock-only trades (project_options_strategy.md). Threshold applies to
# the underlying drop from entry, not to the option-position MTM (which for
# debit structures is dominated by extrinsic decay early in the trade).
ZEBRA_STOP_LOSS_PCT = 0.035


ZEBRA_ENTRY_LOOKBACK_DAYS = 5  # tolerance for finding entry-date snapshot


def zebra_stop_loss_event(conn, symbol: str, entry_date) -> dict | None:
    """Compute ZEBRA stop-loss state for one position.

    Returns a dict with keys:
      stopped: bool — True when pct_drop >= ZEBRA_STOP_LOSS_PCT
      error:   str | None — populated when snapshots aren't available; the
               caller surfaces this as a warning so the alert never silently
               no-ops on a real position
      entry_spot, cur_spot, pct_drop, entry_source_date: float / str fields
               (present on the success path only)

    Snapshot lookup falls back to the closest live_snapshots row within
    +/- ZEBRA_ENTRY_LOOKBACK_DAYS calendar days of entry_date. Reading
    current_price from any opex_date row is fine — it's the underlying
    spot, identical across opex_date partitions on a given snapshot_date.
    """
    if not symbol or entry_date is None:
        return {"stopped": False, "error": "missing symbol or entry_date"}
    entry_str = str(entry_date)[:10]
    entry_row = conn.execute(
        "SELECT current_price, snapshot_date FROM live_snapshots "
        "WHERE symbol = ? AND current_price IS NOT NULL "
        "  AND ABS(julianday(snapshot_date) - julianday(?)) <= ? "
        "ORDER BY ABS(julianday(snapshot_date) - julianday(?)), snapshot_date "
        "LIMIT 1",
        (symbol, entry_str, ZEBRA_ENTRY_LOOKBACK_DAYS, entry_str),
    ).fetchone()
    if not entry_row:
        return {"stopped": False,
                "error": (f"no live_snapshots row for {symbol} within "
                          f"{ZEBRA_ENTRY_LOOKBACK_DAYS}d of entry {entry_str} "
                          f"— stop-loss cannot be evaluated")}
    latest_row = conn.execute(
        "SELECT current_price, snapshot_date FROM live_snapshots "
        "WHERE symbol = ? AND current_price IS NOT NULL "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if not latest_row:
        return {"stopped": False,
                "error": f"no recent live_snapshots row for {symbol}"}
    entry_spot = float(entry_row[0])
    cur_spot = float(latest_row[0])
    if entry_spot <= 0:
        return {"stopped": False,
                "error": f"invalid entry_spot {entry_spot} for {symbol} {entry_str}"}
    pct_drop = (entry_spot - cur_spot) / entry_spot
    return {
        "stopped": pct_drop >= ZEBRA_STOP_LOSS_PCT,
        "error": None,
        "entry_spot": entry_spot,
        "cur_spot": cur_spot,
        "pct_drop": pct_drop,
        "entry_source_date": entry_row[1],
        "current_source_date": latest_row[1],
    }


def detect_dte_checkpoints(positions: pd.DataFrame, conn) -> list[str]:
    """For each open placed=1 position, fire DTE-based and profit-target checkpoint alerts.

    Output discipline: individual lines only for ACTIONABLE items (profit targets,
    critical-DTE windows, protective-put-close, expiration). Bare 21-DTE crossings
    consolidate into a per-OpEx summary so the alert doesn't spam 28 identical lines
    when an entire cohort is at the same DTE.
    """
    if positions.empty:
        return []

    actionable = []
    bare_21dte_by_opex: dict[str, list[str]] = {}  # opex_date → list of "sym (DTE)" strings

    for _, p in positions.iterrows():
        sym = p.get("symbol")
        struct = (p.get("structure") or "").lower()
        opex_str = p.get("opex_date")
        if not opex_str:
            continue
        dte = trading_days_to(opex_str)
        suffix = f"(OpEx {opex_str})"
        sk = p.get("short_strike")
        sk_str = f"{sk:g}" if sk is not None and not pd.isna(sk) else "?"

        is_credit_vertical = struct in ("bull_put", "bull_put_mp", "bear_call", "iron_condor", "iron_fly")

        # ── Profit-target alerts (DTE-independent, credit verticals only) ──
        profit_alerted = False
        if is_credit_vertical and "id" in p and not pd.isna(p["id"]) and not pd.isna(p.get("entry_credit", None)):
            s = credit_captured_status(conn, p["id"], float(p["entry_credit"]))
            if s and s.get("error"):
                actionable.append(
                    f"⚠ {sym} {struct} K={sk_str} {suffix}: profit-target check — "
                    f"{s['error']}"
                )
            if s and s.get("pct") is not None and not s.get("stale"):
                pct = s["pct"]
                if pct >= 80:
                    actionable.append(
                        f"🎯 {sym} {struct} K={sk_str} {suffix}: "
                        f"{pct:.0f}% CREDIT CAPTURED — Sosnoff 80% target HIT"
                    )
                    profit_alerted = True
                elif pct >= 50:
                    actionable.append(
                        f"💰 {sym} {struct} K={sk_str} {suffix}: "
                        f"{pct:.0f}% credit captured — TastyTrade 50% rule eligible"
                    )
                    profit_alerted = True

        # ── DTE-band alerts ──
        if dte == 0:
            actionable.append(f"⚠ {sym} {struct} K={sk_str} {suffix}: EXPIRATION TODAY")
            continue

        if is_credit_vertical:
            if dte <= 3:
                actionable.append(
                    f"⏰ {sym} {struct} K={sk_str} {suffix}: "
                    f"T-{dte} — D-3 EXIT WINDOW (Window B credit-vertical exit)"
                )
            elif dte <= 5:
                actionable.append(
                    f"⏰ {sym} {struct} K={sk_str} {suffix}: "
                    f"T-{dte} — OpEx week begins"
                )
            elif dte <= 21:
                # Suppress bare 21-DTE if profit target already alerted (too redundant)
                if not profit_alerted:
                    bare_21dte_by_opex.setdefault(opex_str, []).append(f"{sym} {struct} K={sk_str}")

        elif struct in ("zebra", "zebra_protected"):
            sl = zebra_stop_loss_event(conn, sym, p.get("entry_date"))
            if sl and sl.get("error"):
                actionable.append(
                    f"⚠ {sym} {struct} {suffix}: stop-loss check FAILED — "
                    f"{sl['error']}"
                )
            elif sl and sl.get("stopped"):
                src_note = ""
                if sl.get("entry_source_date") and str(p.get("entry_date"))[:10] != sl["entry_source_date"]:
                    src_note = f" [entry spot from {sl['entry_source_date']} snapshot]"
                actionable.append(
                    f"🛑 {sym} {struct} {suffix}: STOP-LOSS — spot ${sl['cur_spot']:.2f} "
                    f"vs entry ${sl['entry_spot']:.2f} (-{sl['pct_drop']*100:.1f}%) — "
                    f"CLOSE POSITION (≥{ZEBRA_STOP_LOSS_PCT*100:.1f}% rule){src_note}"
                )
            if struct == "zebra_protected" and 0 < dte <= 10:
                actionable.append(
                    f"🛡  {sym} {struct} {suffix}: "
                    f"T-{dte} — consider closing protective put for residual value"
                )
            # ZEBRA exit cadence — held to OpEx, no managed exit.
            # Phase 1 + Phase 2 backtests (2026-05-14) validated held-to-
            # expiration on both the parent ZEBRA and the long-put overlay;
            # all 5 managed-exit variants on the put (M1-M4) underperformed
            # HOLD by 0/4 walk-forward splits. The 2026-05-03 T-21 ROLL CUE
            # was an untested TastyTrade-canonical rule that the 2026-05-14
            # backtests effectively invalidated — late-cycle gamma is what
            # the structure is built to capture, not avoid.
            #
            # Only fires near expiry for assignment-mechanics awareness; not
            # a roll instruction.
            if 0 < dte <= 3:
                actionable.append(
                    f"⏰ {sym} {struct} {suffix}: T-{dte} — at expiry "
                    f"(held-to-OpEx per validated rule; close manually if "
                    f"you prefer to avoid short-call assignment mechanics)"
                )

        elif struct in ("inverted_fly", "if_pair", "if_single"):
            if dte <= 5:
                actionable.append(
                    f"⏰ {sym} {struct} K={sk_str} {suffix}: "
                    f"T-{dte} — final week (50%-only exit rule, no time stop)"
                )

    # Consolidate bare 21-DTE alerts into summary lines per OpEx
    summary_lines = []
    for opex_str in sorted(bare_21dte_by_opex.keys()):
        names = bare_21dte_by_opex[opex_str]
        sample_dte = trading_days_to(opex_str)
        summary_lines.append(
            f"⏰ {len(names)} positions (OpEx {opex_str}) at {sample_dte} DTE — "
            f"managed-exit zone, no profit targets hit yet"
        )

    return actionable + summary_lines


# ZEBRA earnings-lead window: warn N calendar days before an upcoming earnings
# event for any open ZEBRA. ZEBRA is exempted from the qualifier's earnings
# auto-skip (it's defined-risk, behaves like delta-1 stock); the policy is to
# trade through earnings but give the user advance notice to decide hold/close.
ZEBRA_EARNINGS_LEAD_DAYS = 5


# Live-position trend-violation logic moved to scripts/monitor/regime_health.py
# as part of the unified REGIME HEALTH monitor (system + per-position + history).


def detect_zebra_earnings_warnings(positions: pd.DataFrame) -> list[str]:
    """For every open ZEBRA / zebra_protected position, fire a warning when
    an upcoming earnings event for that symbol falls within the lead window."""
    if positions.empty:
        return []
    zebras = positions[positions["structure"].astype(str).str.lower().str.startswith("zebra")]
    if zebras.empty:
        return []

    try:
        from scripts.qualifier.earnings_calendar import load_earnings_calendar
    except Exception as e:
        return [f"⚠ ZEBRA earnings-lead check skipped — calendar import failed: {e}"]

    today = date.today()
    horizon = today + timedelta(days=ZEBRA_EARNINGS_LEAD_DAYS)
    syms = sorted(zebras["symbol"].dropna().unique().tolist())
    try:
        cal = load_earnings_calendar(syms)
    except Exception as e:
        return [f"⚠ ZEBRA earnings-lead check skipped — calendar load failed: {e}"]
    if cal is None or cal.empty:
        return []
    cal = cal.copy()
    cal["earnings_date"] = pd.to_datetime(cal["earnings_date"]).dt.date
    cal = cal[(cal["earnings_date"] >= today) & (cal["earnings_date"] <= horizon)]
    if cal.empty:
        return []

    out = []
    seen = set()  # one warning per (symbol, earnings_date)
    for _, p in zebras.iterrows():
        sym = p.get("symbol")
        struct = p.get("structure", "zebra")
        opex_str = p.get("opex_date", "?")
        match = cal[cal["ticker"] == sym]
        if match.empty:
            continue
        for _, m in match.iterrows():
            ed = m["earnings_date"]
            key = (sym, ed)
            if key in seen:
                continue
            seen.add(key)
            days_to = (ed - today).days
            band = "TODAY" if days_to == 0 else (
                "TOMORROW" if days_to == 1 else f"in {days_to}d"
            )
            out.append(
                f"🗓 {sym} {struct} (OpEx {opex_str}): EARNINGS {ed} "
                f"({band}) — decide hold/close before close on day before"
            )
    return out


def get_recent_close(symbol: str) -> tuple[float | None, float | None, str | None]:
    """Return (today_close, prior_close, today_date) from ORATS by_ticker.

    ORATS data is one trading day stale relative to today's market close.
    'today' here means the most recent ORATS trade_date for the ticker.
    """
    path = BY_TICKER / f"{symbol}.parquet"
    if not path.exists():
        return None, None, None
    df = pd.read_parquet(path, columns=["trade_date", "stkPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    daily = df.drop_duplicates("trade_date").set_index("trade_date")["stkPx"].sort_index()
    if len(daily) < 2:
        return None, None, None
    return float(daily.iloc[-1]), float(daily.iloc[-2]), str(daily.index[-1].date())


def get_schwab_today(conn, symbol: str) -> tuple[float | None, str | None]:
    """Most recent intraday/EOD close from live_snapshots — used when ORATS
    is a day stale and we have a fresher Schwab capture."""
    try:
        row = conn.execute(
            "SELECT current_price, snapshot_date FROM live_snapshots "
            "WHERE symbol = ? ORDER BY snapshot_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row and row[0]:
            return float(row[0]), row[1]
    except Exception:
        pass
    return None, None


def detect_position_events(positions: pd.DataFrame, thresholds: dict, conn) -> list[str]:
    """Emit per-position events: big moves, FRESH strike breaches, deeper-into-breach moves.

    Strike-breach logic:
      - FRESH breach: yesterday's close on the OK side, today's close on the wrong side.
      - DEEPER breach: already breached + today's move was big AND further into breach.
      - Suppressed: stable/quiet breach (already breached, no big move today).
    """
    if positions.empty:
        return []
    events = []
    seen_moves = set()  # one big-move alert per ticker even if multiple positions

    for _, p in positions.iterrows():
        sym = p["symbol"]

        # Underlying daily move check
        today_px, prior_px, today_dt = get_recent_close(sym)
        if today_px is None or prior_px is None or prior_px <= 0:
            continue
        schwab_px, schwab_dt = get_schwab_today(conn, sym)
        if schwab_px is not None and schwab_dt and schwab_dt > today_dt:
            today_px = schwab_px
            today_dt = schwab_dt

        ret = today_px / prior_px - 1
        thr = thresholds.get(sym, 0.05)
        big_move = abs(ret) >= thr

        if big_move and sym not in seen_moves:
            seen_moves.add(sym)
            direction = "↑" if ret > 0 else "↓"
            events.append(
                f"{sym}: BIG MOVE {direction} {ret*100:+.2f}% "
                f"(p95 threshold {thr*100:.2f}%, now ${today_px:.2f})"
            )

        # Spread-specific checks (skip stock-only trades)
        struct = (p.get("structure") or "").lower()
        if struct not in ("bull_put", "bear_call", "iron_condor", "iron_fly", "jade_lizard"):
            continue
        short_k = p.get("short_strike")
        if pd.isna(short_k):
            continue

        # Determine breach state today and yesterday
        if struct == "bull_put":
            breached_today = today_px <= short_k
            breached_prior = prior_px <= short_k
            side = "PUT"
        elif struct == "bear_call":
            breached_today = today_px >= short_k
            breached_prior = prior_px >= short_k
            side = "CALL"
        else:
            continue

        if breached_today and not breached_prior:
            events.append(
                f"{sym} {struct} (OpEx {p['opex_date']}, K={short_k:g}): "
                f"FRESH short {side} BREACH at close (spot ${today_px:.2f}, "
                f"prior ${prior_px:.2f})"
            )
        elif breached_today and big_move:
            # already breached but moved meaningfully further in
            direction_into_breach = (
                (struct == "bull_put" and ret < 0) or
                (struct == "bear_call" and ret > 0)
            )
            if direction_into_breach:
                events.append(
                    f"{sym} {struct} (OpEx {p['opex_date']}, K={short_k:g}): "
                    f"DEEPER into short {side} breach ({ret*100:+.2f}%, "
                    f"spot ${today_px:.2f})"
                )

    return events


# ─── Assignment-zone early warning ────────────────────────────────────────────

ASSIGNMENT_ZONE_DTE = 5  # fire from T-5 through expiry day


def detect_assignment_zone(positions: pd.DataFrame, conn) -> list[str]:
    """Flag open verticals where current spot is between the short and long
    strike AND DTE ≤ 5 — the zone where one leg gets assigned (100 shares)
    and the other expires worthless. Held to expiry, this turns a clean
    $X loss into "you now own 100 shares Monday at the wrong cost basis."

    Per project_assignment_zone_friction.md — held-to-expiry backtests
    assume cash-equivalent settlement; live assignment is the hidden drag.

    Scope: bull_put + bear_call only. Iron flies / IF / ZEBRA have different
    assignment mechanics that don't reduce to a simple [low, high] zone.
    """
    if positions.empty:
        return []
    events = []

    for _, p in positions.iterrows():
        struct = (p.get("structure") or "").lower()
        if struct not in ("bull_put", "bear_call"):
            continue
        sym = p["symbol"]
        opex_str = p.get("opex_date")
        if not opex_str:
            continue
        dte = trading_days_to(opex_str)
        if dte > ASSIGNMENT_ZONE_DTE:
            continue

        sk = p.get("short_strike")
        lk = p.get("long_strike")
        if sk is None or lk is None:
            continue
        try:
            sk = float(sk); lk = float(lk)
        except Exception:
            continue
        zone_lo, zone_hi = (lk, sk) if struct == "bull_put" else (sk, lk)

        # Prefer the freshest spot (Schwab snapshot today, else ORATS yesterday)
        spot, _ = get_schwab_today(conn, sym)
        if spot is None:
            spot, _, _ = get_recent_close(sym)
        if spot is None:
            continue

        if not (zone_lo <= spot <= zone_hi):
            continue

        if dte == 0:
            sev, label = "🔥", "EXPIRY DAY"
        elif dte <= 2:
            sev, label = "🔔", f"T-{dte}"
        else:
            sev, label = "⚠", f"T-{dte}"

        # Distance from each strike — informs which side is at risk
        dist_short = spot - sk if struct == "bear_call" else sk - spot
        dist_long = spot - lk if struct == "bull_put" else lk - spot
        # which leg gets assigned: the short leg, when spot pierces it
        # bull_put: short put assigned if spot ≤ short strike at expiry
        # bear_call: short call assigned if spot ≥ short strike at expiry
        events.append(
            f"{sev} {sym} {struct} {lk:g}/{sk:g} (OpEx {opex_str}): {label}, "
            f"spot ${spot:.2f} inside [{zone_lo:g}, {zone_hi:g}] — close intraday "
            f"to avoid 100-share assignment at ${sk:g}"
        )

    return events


# ─── 52-week extreme context (regime tagging, not actionable) ────────────────

W52_LOOKBACK = 252
W52_NEAR_PCT = 0.05  # within 5% of the extreme = "APPROACHING"


def compute_52w_status(symbol: str) -> tuple[str, float | None, float | None,
                                              float | None] | None:
    """Return (status, close, hi_252, lo_252) for a symbol from ORATS daily.

    status ∈ {at_52w_high, near_52w_high, at_52w_low, near_52w_low, neither}.
    Returns None if insufficient history.
    """
    path = BY_TICKER / f"{symbol}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path, columns=["trade_date", "stkPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    daily = (df.dropna(subset=["stkPx"])
               .drop_duplicates("trade_date")
               .sort_values("trade_date")
               .set_index("trade_date")["stkPx"])
    if len(daily) < W52_LOOKBACK:
        return None
    window = daily.iloc[-W52_LOOKBACK:]
    hi = float(window.max())
    lo = float(window.min())
    close = float(daily.iloc[-1])
    if close >= hi - 1e-9:
        status = "at_52w_high"
    elif close <= lo + 1e-9:
        status = "at_52w_low"
    elif close >= hi * (1 - W52_NEAR_PCT):
        status = "near_52w_high"
    elif close <= lo * (1 + W52_NEAR_PCT):
        status = "near_52w_low"
    else:
        status = "neither"
    return status, close, hi, lo


def detect_52w_extreme_positions(positions: pd.DataFrame) -> list[str]:
    """Tag open positions whose underlying is at or near a 52w extreme.

    Per project_52w_extremes_rejected.md: not actionable as a filter, but
    informative regime context — 52w highs = low-vol melt-up; 52w lows =
    vol expansion / premium-seller stress.
    """
    if positions.empty:
        return []
    seen = set()
    events = []
    for _, p in positions.iterrows():
        sym = p["symbol"]
        if sym in seen:
            continue
        seen.add(sym)
        result = compute_52w_status(sym)
        if result is None:
            continue
        status, close, hi, lo = result
        if status == "neither":
            continue
        if status == "at_52w_high":
            events.append(f"🔼 {sym} at 52w HIGH (${close:.2f}, range ${lo:.2f}–${hi:.2f}) — low-vol regime")
        elif status == "near_52w_high":
            pct_from_hi = (hi - close) / hi * 100
            events.append(f"⬆ {sym} approaching 52w high (${close:.2f}, {pct_from_hi:.1f}% below ${hi:.2f}) — low-vol regime")
        elif status == "at_52w_low":
            events.append(f"🔽 {sym} at 52w LOW (${close:.2f}, range ${lo:.2f}–${hi:.2f}) — vol expansion / premium-seller stress")
        elif status == "near_52w_low":
            pct_from_lo = (close - lo) / lo * 100
            events.append(f"⬇ {sym} approaching 52w low (${close:.2f}, {pct_from_lo:.1f}% above ${lo:.2f}) — vol expansion regime")
    return events


def detect_if_candidates_at_52w_lows() -> list[str]:
    """Scan the inverted_fly cohort for names currently at (or near) 52w lows.

    Per project_52w_extremes_rejected.md secondary use: 52w-low names show
    2× baseline realized vol — long-vol territory, IF thesis confirmation.
    Lists candidates the user does NOT necessarily hold; informational only.
    """
    try:
        sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
        from scripts.qualifier import gate_config as G
    except Exception:
        return []
    cohort = sorted(set(G.COHORT_INVERTED_FLY_PAIR + G.COHORT_INVERTED_FLY_SINGLE))
    candidates = []
    for sym in cohort:
        result = compute_52w_status(sym)
        if result is None:
            continue
        status, close, hi, lo = result
        if status == "at_52w_low":
            candidates.append(f"🔽 {sym} at 52w LOW (${close:.2f}) — IF setup confirmation")
        elif status == "near_52w_low":
            pct_from_lo = (close - lo) / lo * 100
            candidates.append(f"⬇ {sym} approaching 52w low (${close:.2f}, {pct_from_lo:.1f}% above ${lo:.2f})")
    return candidates


# ─── Entry window alerts ──────────────────────────────────────────────────────

ENTRY_WINDOW_LEAD_DAYS = 3  # fire alert from D-3 through entry day (gives ~2 trading days
                             # of evaluation + modeling + order-setup time per user spec)


def detect_entry_windows(conn) -> list[str]:
    """Fire when GO/PENDING qualifier verdicts have days_until ≤ ENTRY_WINDOW_LEAD_DAYS.
    Covers BOTH Window A (45-DTE managed) and Window B (T-5 / MaxPain) with the same
    lead-time logic. Fires daily during the window so the user doesn't miss it.
    Groups by (window, opex) so multi-name cohorts collapse to one summary line."""
    try:
        latest = conn.execute(
            "SELECT MAX(run_date) FROM cycle_qualifier_runs"
        ).fetchone()
        if not latest or not latest[0]:
            return []
        run_date = latest[0]
    except Exception:
        return []

    df = pd.read_sql(f"""
        SELECT symbol, structure, window, target, opex, days_until, verdict, reason
        FROM cycle_qualifier_runs
        WHERE run_date = '{run_date}'
          AND verdict IN ('GO', 'DOWNSIZE', 'PENDING')
          AND days_until <= {ENTRY_WINDOW_LEAD_DAYS}
        ORDER BY days_until, opex, window, verdict, symbol
    """, conn)

    if df.empty:
        return []

    events = []
    for (window, opex, target), grp in df.groupby(["window", "opex", "target"]):
        days_until = int(grp["days_until"].iloc[0])
        if days_until == 0:
            band = "TODAY"
            sev = "🔥"
        elif days_until == 1:
            band = "TOMORROW"
            sev = "🔔"
        else:
            band = f"in {days_until} trading days"
            sev = "🔔"

        go_names = sorted(grp[grp["verdict"] == "GO"]["symbol"].unique().tolist())
        ds_names = sorted(grp[grp["verdict"] == "DOWNSIZE"]["symbol"].unique().tolist())
        pending_n = (grp["verdict"] == "PENDING").sum()

        header = (f"{sev} {window}  →  entry {target} ({band})  ·  OpEx {opex}")

        lines = [header]
        if go_names:
            lines.append(f"     GO ({len(go_names)}): {', '.join(go_names)}")
        if ds_names:
            lines.append(f"     DOWNSIZE ({len(ds_names)}): {', '.join(ds_names)}")
        if pending_n > 0:
            lines.append(f"     PENDING: {pending_n} more (gates may flip — check qualifier output)")

        events.append("\n  ".join(lines))

    return events


# ─── Earnings risk section ────────────────────────────────────────────────────

# ─── Ex-dividend assignment-risk warning (covered-call positions only) ──────

EXDIV_LEAD_DAYS = 3        # fire from D-3 through ex-div day
EXDIV_NEAR_PCT = 0.01       # also fire if short call within 1% of strike (about-to-be-ITM)


def _last_business_day_of_month(yr: int, mo: int) -> date:
    """Approximate ex-div for a credit ETF — last weekday of the month."""
    if mo == 12:
        nxt = date(yr + 1, 1, 1)
    else:
        nxt = date(yr, mo + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _project_forward_exdivs(symbol: str, today_date: date,
                             window_days: int = 35) -> list[date]:
    """Forward ex-div date list for a symbol within `window_days`.

    Strategy: pull yfinance dividend history; check cadence. For monthly
    distributors (credit ETFs), project the next month's ex-div as the last
    business day of the current month if today is in early month, or next
    month otherwise. Falls back to empty list on any error.
    """
    try:
        import yfinance as yf
        divs = yf.Ticker(symbol).dividends
        if divs is None or divs.empty:
            return []
        # Recent ex-div dates
        recent = sorted([pd.Timestamp(d).tz_localize(None).normalize().date()
                         for d in divs.index])
        # Spot-check cadence: if 8 of last 12 are within 35 days of each other,
        # treat as monthly and project.
        if len(recent) < 6:
            return []
        gaps = [(recent[i] - recent[i - 1]).days for i in range(1, len(recent))]
        recent_gaps = gaps[-12:]
        monthly = sum(1 for g in recent_gaps if 25 <= g <= 35)
        if monthly < 6:
            return []  # not a regular monthly distributor
        # Project: last-business-day-of-month for current and next month
        candidates = []
        for offset in range(0, window_days, 28):
            check = today_date + timedelta(days=offset)
            candidates.append(_last_business_day_of_month(check.year, check.month))
        candidates = sorted(set(candidates))
        return [d for d in candidates
                if today_date <= d <= today_date + timedelta(days=window_days)]
    except Exception:
        return []


def detect_ex_div_assignment_risk(positions: pd.DataFrame, conn) -> list[str]:
    """Fire when an open stock-holding position (covered_call) faces:
      (a) ex-div within EXDIV_LEAD_DAYS trading days, AND
      (b) short call ITM or within EXDIV_NEAR_PCT of strike.

    Early-exercise risk: deep ITM call holders exercise the day before
    ex-div to capture the dividend, calling away your shares + forfeiting
    the dividend you'd otherwise collect. Closing the call before ex-div
    keeps the dividend (cost: pay back remaining premium of the short call).

    Filters to structure='covered_call' — credit verticals have no stock
    leg to call away, so ex-div is irrelevant for them.
    """
    if positions.empty:
        return []
    cc = positions[positions["structure"].str.lower() == "covered_call"]
    if cc.empty:
        return []
    today_date = date.today()
    events = []
    for _, p in cc.iterrows():
        sym = p["symbol"]
        sk = p.get("short_strike")
        if sk is None:
            continue
        try:
            sk = float(sk)
        except Exception:
            continue
        # Get current spot
        spot, _ = get_schwab_today(conn, sym)
        if spot is None:
            spot, _, _ = get_recent_close(sym)
        if spot is None:
            continue
        # Is short call ITM or about-to-be-ITM?
        ratio = (spot / sk - 1) if sk > 0 else 0
        is_itm = spot >= sk
        is_near = ratio >= -EXDIV_NEAR_PCT  # within 1% below strike
        if not (is_itm or is_near):
            continue
        # Forward ex-div lookahead
        upcoming = _project_forward_exdivs(sym, today_date,
                                            window_days=EXDIV_LEAD_DAYS * 3)
        if not upcoming:
            continue
        next_exdiv = upcoming[0]
        days_to_exdiv = (next_exdiv - today_date).days
        if days_to_exdiv > EXDIV_LEAD_DAYS:
            continue

        if days_to_exdiv == 0:
            sev = "🔥"
            band = "EX-DIV TODAY"
        elif days_to_exdiv == 1:
            sev = "🔔"
            band = "EX-DIV TOMORROW"
        else:
            sev = "🔔"
            band = f"EX-DIV in {days_to_exdiv} days"

        if is_itm:
            zone = f"ITM {(spot - sk):.2f}"
        else:
            zone = f"near strike ({ratio*100:+.1f}%)"

        events.append(
            f"{sev} {sym} covered_call K={sk:g}: {band} ({next_exdiv}), "
            f"spot ${spot:.2f} {zone} — close call before ex-div to keep dividend "
            f"or accept early-exercise"
        )
    return events


def load_earnings_cache() -> pd.DataFrame:
    if not EARNINGS_CACHE.exists():
        return pd.DataFrame()
    df = pd.read_parquet(EARNINGS_CACHE)
    df["earnings_date"] = pd.to_datetime(df["earnings_date"]).dt.date
    return df


def detect_earnings_risk(positions: pd.DataFrame) -> list[str]:
    """Fire when an open position has earnings inside the holding window
    (today ≤ earnings_date ≤ opex_date). Collapses multi-leg positions on
    the same (symbol, opex_date) into one alert."""
    if positions.empty:
        return []

    df = load_earnings_cache()
    if df.empty:
        return ["(no earnings_calendar_cache.parquet — run "
                "scripts/pipeline/refresh_earnings_calendar.py)"]

    today = date.today()
    events = []

    for (sym, opex_str), group in positions.groupby(["symbol", "opex_date"]):
        if not opex_str:
            continue
        opex = pd.to_datetime(opex_str).date()
        if opex < today:
            continue

        sym_events = df[(df["ticker"] == sym)
                        & (df["earnings_date"] >= today)
                        & (df["earnings_date"] <= opex)]
        if sym_events.empty:
            continue

        ed = sorted(sym_events["earnings_date"].tolist())[0]
        days_to = (ed - today).days
        post_tail = (opex - ed).days

        legs = sorted({s.lower() for s in group["structure"].dropna().tolist()})
        leg_str = "+".join(legs) if legs else "?"
        if len(legs) >= 2 and "bull_put" in legs and "bear_call" in legs:
            short_strikes = group["short_strike"].dropna().unique()
            if len(short_strikes) == 1:
                leg_str = f"iron_fly @ {short_strikes[0]:g}"
            else:
                leg_str = "iron_condor"

        if days_to == 0:
            sev = "🚨"
            band = "TODAY"
        elif days_to <= 2:
            sev = "🚨"
            band = f"in {days_to}d"
        elif days_to <= 5:
            sev = "⚠"
            band = f"in {days_to}d"
        else:
            sev = "ⓘ"
            band = f"in {days_to}d"

        events.append(
            f"{sev} {sym} EARNINGS {ed.isoformat()} ({band}) — "
            f"INSIDE {leg_str} (OpEx {opex_str}, {post_tail}d post-earnings tail)"
        )

    return sorted(events)


# ─── Main ──────────────────────────────────────────────────────────

# ─── Trade-construction enrichment (Schwab-live legs for actionable rows) ─────

def build_construction_enrichment(conn) -> tuple[str, list[str]]:
    """Pull live construction blocks for every actionable GO/DOWNSIZE row in
    the most recent qualifier run with days_until <= 1.

    Returns (text_blocks, html_blocks) — text concatenated, HTML as list.
    """
    try:
        latest = conn.execute("SELECT MAX(run_date) FROM cycle_qualifier_runs").fetchone()
    except Exception:
        return "", []
    if not latest or not latest[0]:
        return "", []

    df = pd.read_sql_query(f"""
        SELECT symbol, structure, target, opex, days_until, verdict, reason, sector
        FROM cycle_qualifier_runs
        WHERE run_date = '{latest[0]}'
          AND verdict IN ('GO', 'DOWNSIZE')
          AND days_until <= 1
        ORDER BY structure, symbol
    """, conn)
    if df.empty:
        return "", []

    # Lazy import — only when actionable rows exist (saves cron startup time)
    from scripts.monitor.trade_construction import (
        build_construction_block, build_zebra_with_overlay_block,
    )
    from scripts.monitor.zebra_overlay_rule import regime_overlay_rule
    from scripts.qualifier.gate_config import COHORT_ZEBRA_OVERLAY_AUTO
    from lib.sector_map import ETF_SENTINEL, UNKNOWN_SENTINEL

    # Compute the overlay rule once per alert run — shared across all ZEBRA
    # candidate cards. Avoids re-querying regime_state per symbol.
    overlay_rule = None

    # Track (opex, sector) → count of candidates rendered so we can flag
    # the 2nd entry in a sector with ⚠ SECTOR-LOAD. The qualifier already
    # caps the 3rd+ (SKIP_CONCENTRATION verdict not present in this query);
    # this annotation surfaces the cap-adjacent state to the trader.
    sector_count: dict[tuple[str, str], int] = {}

    # Track every symbol that produces a construction block, so we can run
    # the macro-concentration check across the whole rendered candidate set
    # after the loop. This is the macro-band analog of SECTOR-LOAD: surfaces
    # cross-sector correlation traps that GICS cap misses (BAC+JPM both
    # POS_MED rate β = unintended same-bet concentration).
    rendered_symbols: list[str] = []

    text_parts = []
    html_parts = []
    for _, r in df.iterrows():
        # Earnings rows have opex = "(earnings-anchored)" — skip these for
        # construction (need real expiration). Earnings track gets its own
        # treatment in a later phase.
        if not r["opex"] or "earnings" in r["opex"]:
            continue

        # Increment + maybe-flag sector load
        sector = r.get("sector") or UNKNOWN_SENTINEL
        sector_warning = None
        if sector not in (ETF_SENTINEL, UNKNOWN_SENTINEL, None):
            key = (r["opex"], sector)
            sector_count[key] = sector_count.get(key, 0) + 1
            if sector_count[key] == 2:
                sector_warning = (
                    f"  ⚠ SECTOR-LOAD: 2nd {sector} entry for OpEx {r['opex']} "
                    f"— at the per-sector cap (max {2} per GICS sector)"
                )

        result = build_construction_block(r["symbol"], r["structure"], r["opex"])
        if not result["ok"]:
            text_parts.append(f"  ⚠ {r['symbol']} {r['structure']}: {result['error']}")
            continue
        rendered_symbols.append(r["symbol"])
        text_parts.append(result["text"])
        if sector_warning:
            text_parts.append(sector_warning)
        text_parts.append("")
        html_parts.append(result["html"])
        if sector_warning:
            html_parts.append(
                f"<div style='font-size:12px;color:#b58900;margin:4px 0 12px 0;"
                f"padding:6px 10px;background:#fff8dc;border-left:3px solid #b58900'>"
                f"{sector_warning.strip()}</div>"
            )

        # For ZEBRA rows in the validated AUTO-attach cohort, render the
        # regime-conditional overlay variant. For ZEBRA rows NOT in the AUTO
        # cohort, surface a one-line note that the overlay is discretionary
        # only — backtests didn't validate auto-attach for that name.
        # Phase 1+2 validated: matched-expiry long put, strike by regime,
        # both legs held to OpEx. AUTO cohort sourced from
        # gate_config.COHORT_ZEBRA_OVERLAY_AUTO (tier-1 + tier-2 per-name).
        if r["structure"].startswith("zebra"):
            if r["symbol"] in COHORT_ZEBRA_OVERLAY_AUTO:
                if overlay_rule is None:
                    overlay_rule = regime_overlay_rule()
                ovl = build_zebra_with_overlay_block(r["symbol"], r["opex"], overlay_rule)
                if ovl["ok"]:
                    text_parts.append(ovl["text"])
                    text_parts.append("")
                    html_parts.append(ovl["html"])
                else:
                    text_parts.append(f"  ⚠ {r['symbol']} zebra_overlay: {ovl['error']}")
            else:
                discretionary_note = (
                    f"  ℹ {r['symbol']} long-put overlay: discretionary only "
                    f"(not in COHORT_ZEBRA_OVERLAY_AUTO). Run "
                    f"`python3.11 -m scripts.monitor.trade_construction "
                    f"--symbol {r['symbol']} --expiry {r['opex']} --with-overlay` "
                    f"to render on demand."
                )
                text_parts.append(discretionary_note)
                text_parts.append("")
                html_parts.append(
                    f"<div style='font-size:12px;color:#586069;margin:4px 0 12px 0;"
                    f"padding:6px 10px;background:#f6f8fa;border-left:3px solid #586069'>"
                    f"<b>{r['symbol']}</b> long-put overlay: discretionary only "
                    f"(not in <code>COHORT_ZEBRA_OVERLAY_AUTO</code>). "
                    f"Run <code>python3.11 -m scripts.monitor.trade_construction "
                    f"--symbol {r['symbol']} --expiry {r['opex']} --with-overlay</code> "
                    f"to render on demand.</div>"
                )

    # Macro-band concentration check across the rendered candidate set.
    # Surfaces correlation traps that the sector cap misses (e.g., XLU+TLT
    # both NEG_HIGH on β_dgs10 = same rate-defensive bet across sectors).
    # Only flags when ≥2 candidates share a tier, and only for non-NEUTRAL
    # tiers. Soft warning — does NOT block the trade.
    if len(rendered_symbols) >= 2:
        try:
            from lib.macro_profile import cohort_macro_concentration
            dupes = cohort_macro_concentration(rendered_symbols)
        except FileNotFoundError:
            dupes = {}  # macro_profile.parquet not yet built — skip silently
        except Exception as e:
            dupes = {}
            text_parts.append(f"  ℹ macro-concentration check failed: {e}")
        if dupes:
            text_parts.append("")
            text_parts.append("─── MACRO CONCENTRATION ───")
            text_parts.append(
                "  Soft warning — multiple candidates share a macro-sensitivity tier."
            )
            text_parts.append(
                "  Cross-sector correlation that GICS cap doesn't catch."
            )
            html_block = [
                "<div style='font-size:12px;color:#586069;margin:12px 0 4px 0;"
                "padding:6px 10px;background:#f6f8fa;border-left:3px solid #586069'>"
                "<b>MACRO CONCENTRATION</b> — multiple candidates share a macro tier "
                "(cross-sector correlation GICS cap misses).<ul style='margin:4px 0 0 0;padding-left:20px'>"
            ]
            for dim, dupes_for_dim in dupes.items():
                for tier_label, tickers in dupes_for_dim.items():
                    text_parts.append(
                        f"  ⚠ {dim} {tier_label}: {len(tickers)} names — {', '.join(tickers)}"
                    )
                    html_block.append(
                        f"<li><code>{dim}</code> <b>{tier_label}</b>: "
                        f"{len(tickers)} names — {', '.join(tickers)}</li>"
                    )
            html_block.append("</ul></div>")
            html_parts.append("".join(html_block))

    return "\n".join(text_parts), html_parts


def build_email_html(text_body: str, construction_cards: list[str]) -> str:
    """Wrap the captured stdout in monospace HTML + append construction cards."""
    safe_text = (text_body
                 .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    cards_html = "\n".join(construction_cards) if construction_cards else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Helvetica,Arial,sans-serif;max-width:780px;margin:0 auto;padding:12px">
<pre style="font-family:Menlo,Consolas,monospace;font-size:13px;background:#fafafa;
            border:1px solid #ddd;padding:12px;white-space:pre-wrap;line-height:1.4">
{safe_text}</pre>
{cards_html}
<div style="font-size:11px;color:#888;margin-top:16px;border-top:1px solid #eee;padding-top:8px">
Generated by MaxPain daily_alert.py · framework-driven recommendations only ·
construction blocks are entry-day/T-1 actionable trades
</div>
</body></html>"""


def derive_subject(text_body: str, n_constructions: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    if n_constructions:
        return f"MaxPain Alert — {n_constructions} actionable trade{'s' if n_constructions > 1 else ''} — {today}"
    if "⚠" in text_body or "RED" in text_body:
        return f"MaxPain Alert — events present — {today}"
    return f"MaxPain Alert — daily — {today}"


def _derive_severity(subject: str, text_body: str) -> str:
    if "RED" in subject or "⚠" in text_body:
        return "RED"
    if "YELLOW" in subject:
        return "YELLOW"
    if "actionable trade" in subject:
        return "ACTION"
    return "INFO"


def _persist_run(subject: str, text_body: str, html_body: str,
                 n_constructions: int, has_events: bool) -> None:
    """Archive this alert run as one row in daily_alert_runs (one row per day,
    INSERT OR REPLACE so the latest run wins). Used by the dashboard's Daily
    Alert page for browsable history + post-mortem reconstruction."""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_alert_runs (
            run_date TEXT PRIMARY KEY,
            run_timestamp TEXT NOT NULL,
            subject TEXT,
            severity TEXT,
            text_body TEXT,
            html_body TEXT,
            n_constructions INTEGER,
            has_events INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    today = datetime.now().strftime("%Y-%m-%d")
    severity = _derive_severity(subject, text_body)
    conn.execute("""
        INSERT OR REPLACE INTO daily_alert_runs
            (run_date, run_timestamp, subject, severity, text_body, html_body,
             n_constructions, has_events)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (today, datetime.now().isoformat(timespec="seconds"),
          subject, severity, text_body, html_body,
          int(n_constructions), int(bool(has_events))))
    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true",
                        help="Always print state summary even if no events")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip SMTP send (still prints to stdout/log)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print only — no SMTP, no DB writes")
    args = parser.parse_args()

    # Capture all stdout into `buf` AND keep cron log working by tee-ing.
    # Earlier `with redirect_stdout(buf):` only enclosed the conn= line, so
    # `text_body` ended up empty and the has_events guard at the bottom of
    # main() always hit the "truly quiet" early-return. Fixed 2026-05-07.
    class _Tee:
        def __init__(self, *streams): self.streams = streams
        def write(self, s):
            for st in self.streams:
                try: st.write(s)
                except Exception: pass
        def flush(self):
            for st in self.streams:
                try: st.flush()
                except Exception: pass

    buf = io.StringIO()
    construction_text = ""
    construction_html = []
    _real_stdout = sys.stdout
    sys.stdout = _Tee(_real_stdout, buf)

    conn = sqlite3.connect(DB_PATH)

    print(f"\n{'='*72}")
    print(f"  MaxPain Daily Alert — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*72}")

    # Regime section
    print("\n  REGIME")
    print(f"  {'-'*68}")
    regime_df = load_recent_regime(conn)
    print(f"  {summarize_regime(regime_df)}")
    regime_events = detect_regime_events(regime_df)
    if regime_events and not all(e.startswith("(") for e in regime_events):
        print()
        for ev in regime_events:
            print(f"  ⚠ {ev}")
    elif args.verbose:
        print(f"  (no day-over-day changes)")

    # Approaching-threshold section (early-warning, in-buffer-zone)
    approach_events = detect_approaching_thresholds(regime_df)
    if approach_events:
        print(f"\n  APPROACHING THRESHOLDS")
        print(f"  {'-'*68}")
        for ev in approach_events:
            print(f"  ⓘ {ev}")

    # Daily Macro Brief (reads Agent_Project ChromaDB — curve / FedWatch /
    # Fed RSS). Soft-fail: never break the alert pipeline if Agent_Project
    # is unavailable or its scrapers haven't run yet.
    try:
        from lib.macro_brief import build_macro_brief, render_text as render_brief_text
        brief = build_macro_brief()
        brief_text = render_brief_text(brief)
        if brief_text.strip():
            for line in brief_text.split("\n"):
                print(line)
    except Exception as e:
        print(f"\n  MACRO BRIEF — unavailable ({e.__class__.__name__}: {e})")

    # AI Pre-Cycle Commentary annotation (Phase 2). If today's 9:30 ET cron
    # produced a fresh commentary, surface a short summary here. Soft-fail.
    try:
        from datetime import date as _date
        from lib.ai_pre_cycle_commentary import get_latest_cached
        latest = get_latest_cached(_date.today().isoformat())
        if latest and latest.get("response_text"):
            print(f"\n  AI PRE-CYCLE COMMENTARY  (run_date {latest['run_date']}, "
                  f"prompt {latest.get('prompt_version', '?')})")
            print(f"  {'-'*68}")
            txt = latest["response_text"].strip()
            if len(txt) > 500:
                snippet = txt[:500].rsplit(" ", 1)[0] + "…"
                print(f"  {snippet}")
                print(f"  (full text on dashboard page 8 — "
                      f"in={latest['input_tokens']:,} out={latest['output_tokens']:,})")
            else:
                for line in txt.split("\n"):
                    print(f"  {line}")
    except Exception as e:
        print(f"\n  PRE-CYCLE COMMENTARY — unavailable ({e.__class__.__name__}: {e})")

    # Open-trade section
    print("\n  OPEN TRADES")
    print(f"  {'-'*68}")
    positions = load_open_positions(conn)
    thresholds = load_thresholds(conn)
    n_pos = len(positions)
    n_syms = positions["symbol"].nunique() if n_pos else 0
    print(f"  {n_pos} open positions across {n_syms} symbols")

    pos_events = detect_position_events(positions, thresholds, conn)
    if pos_events:
        print()
        for ev in pos_events:
            print(f"  ⚠ {ev}")
    elif args.verbose:
        print(f"  (no position-level events)")

    # Assignment-zone section (open verticals where spot is between strikes & DTE ≤ 5)
    assignment_events = detect_assignment_zone(positions, conn)
    if assignment_events:
        print(f"\n  ASSIGNMENT ZONE WARNING (close intraday to avoid 100-share assignment)")
        print(f"  {'-'*68}")
        for ev in assignment_events:
            print(f"  {ev}")

    # Entry-window section (fires when Window A 45-DTE or Window B T-5 entry is approaching)
    entry_window_events = detect_entry_windows(conn)
    if entry_window_events:
        print(f"\n  ENTRY WINDOW APPROACHING")
        print(f"  {'-'*68}")
        for ev in entry_window_events:
            print(f"  {ev}")

    # Ex-div assignment-risk section (covered-call positions only)
    exdiv_events = detect_ex_div_assignment_risk(positions, conn)
    if exdiv_events:
        print(f"\n  EX-DIV ASSIGNMENT RISK (close call before ex-div to keep dividend)")
        print(f"  {'-'*68}")
        for ev in exdiv_events:
            print(f"  {ev}")

    # Earnings-risk section
    earnings_events = detect_earnings_risk(positions)
    actionable_earnings = [e for e in earnings_events if not e.startswith("(")]
    if actionable_earnings:
        print(f"\n  EARNINGS RISK (inside holding window)")
        print(f"  {'-'*68}")
        for ev in actionable_earnings:
            print(f"  {ev}")

    # 52w-extreme tagging on open positions (regime context, not actionable)
    extreme_events = detect_52w_extreme_positions(positions)
    if extreme_events:
        print(f"\n  52-WEEK EXTREME CONTEXT (open positions at 52w highs/lows)")
        print(f"  {'-'*68}")
        for ev in extreme_events:
            print(f"  {ev}")

    # IF cohort screening — names currently at 52w lows (long-vol setup confirmation)
    if_candidates = detect_if_candidates_at_52w_lows()
    if if_candidates:
        print(f"\n  IF COHORT AT 52W LOWS (long-vol setup confirmation, not yet held)")
        print(f"  {'-'*68}")
        for c in if_candidates:
            print(f"  {c}")

    # REGIME HEALTH section (system + per-position; persists to history)
    from scripts.monitor.regime_health import assess_all, persist, render_text
    regime_assessment = assess_all(conn, date.today(), positions)
    regime_health_lines = render_text(regime_assessment)
    if regime_health_lines:
        print(f"\n  REGIME HEALTH")
        print(f"  {'-'*68}")
        for line in regime_health_lines:
            print(line)
    try:
        persist(conn, regime_assessment)
    except Exception as e:
        print(f"  ⚠ regime health persistence failed: {e}")

    # PSYCH-GAP-LOG PROMPTS (SEP-live transition checklist item 1)
    # Surfaces open positions newly at 🟡/🔴 since last log entry so the
    # user is reminded to report a "would I close this in live?" judgment.
    psych_gap_text = ""
    try:
        from lib.psych_gap_log import pending_prompts, render_prompts_text
        gap_prompts = pending_prompts(date.today().isoformat(), conn=conn)
        psych_gap_text = render_prompts_text(gap_prompts)
        if psych_gap_text:
            print(psych_gap_text)
    except Exception as e:
        print(f"  ⚠ psych-gap-log prompt failed: {e}")

    # Open-position close marks (live mid/natural/limit + capture %)
    # Sourced from scripts/monitor/close_helper.py — same module the user runs
    # ad-hoc via CLI. Embedding here surfaces 50%-capture and >25% candidates
    # at alert time, including the natural-vs-mid gap that flagged GS this week.
    close_text = ""
    try:
        from scripts.monitor.close_helper import build_close_block
        close_block = build_close_block()
        close_text = close_block.get("text", "")
        close_errors = close_block.get("errors", [])
        if close_text and close_text != "No open placed positions.":
            print()
            print(close_text)
            for err in close_errors[:5]:
                print(f"  ⚠ {err}")
    except Exception as e:
        print(f"  ⚠ close_helper enrichment failed: {e}")

    # DTE checkpoints section
    dte_events = detect_dte_checkpoints(positions, conn)
    zebra_earnings_events = detect_zebra_earnings_warnings(positions)
    if dte_events or zebra_earnings_events:
        print(f"\n  DTE CHECKPOINTS")
        print(f"  {'-'*68}")
        for ev in dte_events:
            print(f"  {ev}")
        for ev in zebra_earnings_events:
            print(f"  {ev}")

    # All-quiet footer (computed before construction enrichment because we
    # consider construction availability as "not quiet" too).
    construction_text, construction_html = build_construction_enrichment(conn)

    if (not regime_events and not approach_events and not pos_events
            and not assignment_events and not entry_window_events
            and not exdiv_events and not actionable_earnings
            and not extreme_events and not if_candidates and not dte_events
            and not zebra_earnings_events and not regime_health_lines
            and not psych_gap_text and not construction_text):
        print(f"\n  ✓ All quiet — no alerts.")

    if construction_text:
        print()
        print("  TRADE CONSTRUCTIONS  (actionable today, days_until ≤ 1)")
        print(f"  {'-'*68}")
        print(construction_text)

    print(f"\n{'='*72}\n")
    conn.close()

    # ── Restore real stdout; buf has already been tee'd in parallel ──
    sys.stdout = _real_stdout
    text_body = buf.getvalue()

    # ── Email + persist ──
    # Dry-run: print only, no DB writes, no email (per --dry-run docstring).
    if args.dry_run:
        return

    n_constructions = len(construction_html)
    has_events = any(tag in text_body for tag in ("⚠", "REGIME EVENT", "DTE CHECKPOINTS",
                                                   "ENTRY WINDOW", "ASSIGNMENT ZONE",
                                                   "EX-DIV ASSIGNMENT", "EARNINGS RISK",
                                                   "OPEN POSITIONS"))
    quiet = (not n_constructions) and (not has_events)

    # Compose subject + HTML always (used by both email + persistence).
    try:
        subject = derive_subject(text_body, n_constructions)
        html_body = build_email_html(text_body, construction_html)
    except Exception as e:
        print(f"  compose failed: {e}")
        return

    # Email — skip on --no-email or on truly quiet days (don't spam inbox).
    if not args.no_email and not quiet:
        try:
            from lib.email_alert import send_html_alert
            send_html_alert(subject, text_body, html_body)
        except Exception as e:
            print(f"  email send raised: {e}")

    # Persist — always (except dry-run above). Quiet days are still useful in
    # the archive: "no events that day" is itself a state worth preserving for
    # post-mortem reconstruction.
    try:
        _persist_run(subject, text_body, html_body, n_constructions, has_events)
    except Exception as e:
        print(f"  persist failed: {e}")


if __name__ == "__main__":
    main()
