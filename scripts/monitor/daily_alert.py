#!/usr/bin/env python3.11
"""
MaxPain v1.7 daily alert
~/MaxPain_Project/scripts/monitor/daily_alert.py

Two-section alert that replaces the granular-greek noise of the old
Metal_Project daily_alert.py:

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
import logging
import sqlite3
from datetime import datetime, date
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"
BY_TICKER = ROOT / "data/orats/by_ticker"
EARNINGS_CACHE = ROOT / "data/profile/earnings_calendar_cache.parquet"

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


def latest_credit_captured_pct(conn, trade_id: int, entry_credit: float) -> float | None:
    """For credit spreads: % of max credit captured = (entry - current_mark) / entry × 100.
    Returns None if no daily mark exists or entry_credit is non-positive (debit trade)."""
    if entry_credit is None or entry_credit <= 0:
        return None  # debit trade (e.g. zebra) — different math
    row = conn.execute(
        "SELECT mark_credit FROM spread_score_daily "
        "WHERE trade_id = ? ORDER BY mark_date DESC LIMIT 1",
        (int(trade_id),),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    mark = float(row[0])
    return (entry_credit - mark) / entry_credit * 100


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

        is_credit_vertical = struct in ("bull_put", "bear_call", "iron_condor", "iron_fly")

        # ── Profit-target alerts (DTE-independent, credit verticals only) ──
        profit_alerted = False
        if is_credit_vertical and "id" in p and not pd.isna(p["id"]) and not pd.isna(p.get("entry_credit", None)):
            pct = latest_credit_captured_pct(conn, p["id"], float(p["entry_credit"]))
            if pct is not None:
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

        elif struct == "zebra_protected":
            if dte <= 10 and dte > 0:
                actionable.append(
                    f"🛡  {sym} {struct} {suffix}: "
                    f"T-{dte} — consider closing protective put for residual value"
                )

        elif struct == "zebra":
            if dte <= 3:
                actionable.append(
                    f"⚠ {sym} {struct} {suffix}: T-{dte} — EXPIRATION APPROACHING (held-to-rule spec)"
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
    """Most recent intraday/EOD close from research_cohort_snapshots or
    daily_snapshots — used when ORATS is a day stale and we have a fresher
    Schwab capture."""
    for tbl in ("research_cohort_snapshots", "daily_snapshots"):
        try:
            row = conn.execute(
                f"SELECT current_price, snapshot_date FROM {tbl} "
                f"WHERE symbol = ? ORDER BY snapshot_date DESC LIMIT 1",
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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true",
                        help="Always print state summary even if no events")
    args = parser.parse_args()

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

    # Entry-window section (fires when Window A 45-DTE or Window B T-5 entry is approaching)
    entry_window_events = detect_entry_windows(conn)
    if entry_window_events:
        print(f"\n  ENTRY WINDOW APPROACHING")
        print(f"  {'-'*68}")
        for ev in entry_window_events:
            print(f"  {ev}")

    # Earnings-risk section
    earnings_events = detect_earnings_risk(positions)
    actionable_earnings = [e for e in earnings_events if not e.startswith("(")]
    if actionable_earnings:
        print(f"\n  EARNINGS RISK (inside holding window)")
        print(f"  {'-'*68}")
        for ev in actionable_earnings:
            print(f"  {ev}")

    # DTE checkpoints section
    dte_events = detect_dte_checkpoints(positions, conn)
    if dte_events:
        print(f"\n  DTE CHECKPOINTS")
        print(f"  {'-'*68}")
        for ev in dte_events:
            print(f"  {ev}")

    # All-quiet footer
    if not regime_events and not approach_events and not pos_events and not entry_window_events and not actionable_earnings and not dte_events:
        print(f"\n  ✓ All quiet — no alerts.")

    print(f"\n{'='*72}\n")
    conn.close()


if __name__ == "__main__":
    main()
