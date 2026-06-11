"""Overnight-drift watch — intraday-vs-overnight decomposition (DESCRIPTIVE).

Surfaces the pattern the user flagged 2026-06-11 ([[project_market_view_20260611]]):
QQQ/SOXX "sag intraday, get goosed back overnight; down days gap up next AM." In the
data (yfinance, ~28 sessions) this was real — SOXX overnight mean +0.56%/d vs intraday
+0.12%/d, nearly ALL the gains coming overnight — but it began CRACKING 6/5-6/10
(intraday selling deepening, and on 6/10 the overnight goose failed outright).

For each watched symbol we decompose every session into two legs:
  • intraday  = close / open      − 1   (what the cash session does)
  • overnight = open  / prevclose − 1   (what the gap/futures session does)
and compare a trailing PATTERN window (the established regime) against the last few
RECENT sessions (is it breaking?). Three descriptive states:

  🟢 normal              — intraday and overnight contributions roughly balanced
  🟡 strong levitation   — gains concentrated overnight (dip-buying / "it always
                            recovers") — a complacency / late-cycle tell, NOT direction
  🔴 levitation breaking — the overnight bid is failing WHILE intraday selling deepens
                            (the 6/10 tell). Cross-reference the BREADTH RING: ring
                            🟡/🔴 + this = the break confirming.

DESCRIPTIVE ONLY. Not pre-registered; does NOT gate, size, or vote in the exit
cascade — purely informational, consistent with "descriptive first, pre-register
before it gates" (same contract as lib/breadth_ring and lib/sector_drift).

Soft-fail by contract: every entry point returns a structured result or an {'error'}
dict / status string; the daily alert must never break if this is unavailable.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

# ── Watched symbols ───────────────────────────────────────────────────────────
# QQQ/SOXX are where the user observed the pattern; SPY as a broad-market reference
# (if SPY also levitates it's a market-wide complacency read, not just semis/megacap).
SYMBOLS = ("SOXX", "QQQ", "SPY")

# ── Windows & thresholds (a-priori; descriptive, never tuned to an outcome) ─────
PATTERN_W = 25     # trailing sessions that establish "is this the regime?"
RECENT_W = 5       # last sessions used to detect the break (the 6/10-style tell)
MIN_SESSIONS = PATTERN_W + RECENT_W + 2   # need this much daily history

LEV_GAP = 0.0015   # overnight mean must beat intraday mean by ≥0.15%/d to call it levitation
BREAK_INTRA = -0.0020  # recent intraday mean this negative = real intraday selling
BREAK_OV = 0.0005      # recent overnight mean at/below this (~flat→neg) = goose no longer rescuing

_SEVERITY = {"🟢": 0, "🟡": 1, "🔴": 2}


def _fetch_ohlc(symbols=SYMBOLS) -> Optional[dict]:
    """Per-symbol daily Open/Close (~60d) via yfinance. Returns {sym: DataFrame}
    (a symbol whose fetch fails is simply omitted); None only if ALL failed or
    yfinance is unavailable. auto_adjust=True is fine — it scales open & close
    together, so the intraday/overnight ratios are unaffected."""
    try:
        import yfinance as yf
    except Exception:
        return None
    out = {}
    for t in symbols:
        try:
            df = yf.download(t, period="60d", auto_adjust=True, progress=False)
            if df is None or df.empty or "Open" not in df or "Close" not in df:
                continue
            o = np.asarray(df["Open"]).ravel()
            c = np.asarray(df["Close"]).ravel()
            frame = pd.DataFrame({"open": o, "close": c},
                                 index=pd.to_datetime(df.index)).dropna()
            if len(frame) >= MIN_SESSIONS:
                out[t] = frame
        except Exception:
            continue
    return out or None


def _decompose(frame: pd.DataFrame) -> dict:
    """Compute the intraday/overnight legs and pattern-vs-recent stats for one symbol."""
    f = frame.sort_index().copy()
    f["intraday"] = f["close"] / f["open"] - 1.0
    f["overnight"] = f["open"] / f["close"].shift(1) - 1.0
    f = f.dropna(subset=["overnight"])   # first row has no prev close

    recent = f.iloc[-RECENT_W:]
    pattern = f.iloc[-(PATTERN_W + RECENT_W):-RECENT_W]   # the regime BEFORE the recent break

    intra_mean = float(pattern["intraday"].mean())
    ov_mean = float(pattern["overnight"].mean())
    intra_recent = float(recent["intraday"].mean())
    ov_recent = float(recent["overnight"].mean())

    # dip-buy tell: avg overnight gap on the session AFTER a down-intraday session,
    # measured over the pattern window (positive = down days get bought back overnight)
    down_prev = f["intraday"].shift(1) < 0
    after_down = f.loc[down_prev, "overnight"].iloc[-(PATTERN_W + RECENT_W):]
    gap_after_down = float(after_down.mean()) if len(after_down) else float("nan")
    n_after_down = int(len(after_down))

    levitating = (ov_mean > 0.0) and (ov_mean - intra_mean >= LEV_GAP)
    breaking = levitating and (intra_recent <= BREAK_INTRA) and (ov_recent <= BREAK_OV)
    status = "🔴" if breaking else ("🟡" if levitating else "🟢")

    return {
        "status": status, "levitating": levitating, "breaking": breaking,
        "intra_mean": intra_mean, "ov_mean": ov_mean,
        "intra_recent": intra_recent, "ov_recent": ov_recent,
        "gap_after_down": gap_after_down, "n_after_down": n_after_down,
        "last_intra": float(f["intraday"].iloc[-1]),
        "last_ov": float(f["overnight"].iloc[-1]),
        "asof": f.index[-1].date().isoformat(),
    }


def _classify(status: str, lev_syms: list, brk_syms: list) -> tuple[str, str]:
    """Map overall state → (headline, plain-language read). Single source of wording,
    shared by live-compute and the persisted reader."""
    if status == "🔴":
        names = "/".join(brk_syms)
        return (f"{names} levitation BREAKING — overnight bid failing while intraday selling deepens",
                "the 'it always recovers overnight' pattern is cracking — cross-check the BREADTH "
                "RING: ring 🟡/🔴 + this = the break confirming (still descriptive, not a signal to act)")
    if status == "🟡":
        names = "/".join(lev_syms)
        return (f"{names} strong overnight levitation — gains concentrated in the gap/overnight session",
                "dip-buying / 'it always recovers' — a complacency / late-cycle tell, NOT a directional "
                "call; watch for the overnight bid to fail (would flip 🔴)")
    return ("no abnormal overnight levitation",
            "intraday and overnight contributions are roughly balanced — the watched names are not "
            "relying on an overnight bid")


def compute_overnight_drift(symbols=SYMBOLS) -> dict:
    """Compute the descriptive overnight-drift watch. Returns a structured dict; on
    any data failure returns {'error': <str>} so the caller can soft-fail."""
    data = _fetch_ohlc(symbols)
    if data is None:
        return {"error": "OHLC fetch failed (yfinance unavailable or no symbol returned enough history)"}

    per = {}
    for sym in symbols:
        if sym in data:
            per[sym] = _decompose(data[sym])
    if not per:
        return {"error": "no symbol had sufficient history"}

    status = max((d["status"] for d in per.values()), key=lambda s: _SEVERITY[s])
    lev_syms = [s for s, d in per.items() if d["levitating"] and not d["breaking"]]
    brk_syms = [s for s, d in per.items() if d["breaking"]]
    headline, read = _classify(status, lev_syms, brk_syms)
    asof = max(d["asof"] for d in per.values())

    return {
        "asof": asof, "status": status, "headline": headline, "read": read,
        "symbols": per, "stale_today": asof != date.today().isoformat(),
    }


def _fmt_sym_line(sym: str, d: dict) -> str:
    g = d["gap_after_down"]
    gap = f"dip-buy gap {g*100:+.2f}%" if not np.isnan(g) else "dip-buy gap n/a"
    return (f"  {sym:<4} {d['status']}  o/n {d['ov_mean']*100:+.2f}%/d vs intraday "
            f"{d['intra_mean']*100:+.2f}%/d ({PATTERN_W}d) · last {RECENT_W}d "
            f"intraday {d['intra_recent']*100:+.2f}% o/n {d['ov_recent']*100:+.2f}% · {gap}")


def render_text(drift: dict) -> list:
    """Render the watch as alert lines (descriptive). Returns a one-line note on
    error; the caller decides whether to print it."""
    if drift.get("error"):
        return [f"OVERNIGHT-DRIFT WATCH — unavailable ({drift['error']})"]
    lines = ["OVERNIGHT-DRIFT WATCH  (intraday vs overnight — descriptive, not a gate)",
             f"  {'-'*66}",
             f"  {drift['status']} {drift['headline']}"]
    per = drift["symbols"]
    for sym in SYMBOLS:                      # stable display order
        if sym in per:
            lines.append(_fmt_sym_line(sym, per[sym]))
    lines.append(f"  → {drift['read']}")
    if drift.get("stale_today"):
        lines.append(f"  [STALE] latest close is {drift['asof']}, not today")
    return lines


def persist(drift: dict, conn) -> None:
    """Append today's per-symbol rows to overnight_drift_daily (history substrate).
    Soft-fail — never raise into the cron."""
    if drift.get("error"):
        return
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS overnight_drift_daily (
                   asof TEXT, symbol TEXT, status TEXT, levitating INTEGER,
                   breaking INTEGER, intra_mean REAL, ov_mean REAL,
                   intra_recent REAL, ov_recent REAL, gap_after_down REAL,
                   n_after_down INTEGER, last_intra REAL, last_ov REAL,
                   PRIMARY KEY (asof, symbol))"""
        )
        for sym, d in drift["symbols"].items():
            conn.execute(
                """INSERT OR REPLACE INTO overnight_drift_daily
                   (asof, symbol, status, levitating, breaking, intra_mean, ov_mean,
                    intra_recent, ov_recent, gap_after_down, n_after_down,
                    last_intra, last_ov) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (drift["asof"], sym, d["status"], int(d["levitating"]),
                 int(d["breaking"]), d["intra_mean"], d["ov_mean"],
                 d["intra_recent"], d["ov_recent"],
                 None if np.isnan(d["gap_after_down"]) else d["gap_after_down"],
                 d["n_after_down"], d["last_intra"], d["last_ov"]),
            )
        conn.commit()
    except Exception:
        pass


def latest_persisted(conn, max_stale_days: int = 4) -> Optional[dict]:
    """Read the most recent rows written by the refresh cron and rebuild the render
    dict. Returns None if no rows, the table is absent, or the latest asof is older
    than max_stale_days (the alert then falls back to a live compute)."""
    try:
        asof_row = conn.execute(
            "SELECT MAX(asof) FROM overnight_drift_daily").fetchone()
    except Exception:
        return None
    if not asof_row or not asof_row[0]:
        return None
    asof = asof_row[0]
    try:
        age = (pd.Timestamp(date.today()) - pd.Timestamp(asof)).days
    except Exception:
        age = 0
    if age > max_stale_days:
        return None
    rows = conn.execute(
        """SELECT symbol, status, levitating, breaking, intra_mean, ov_mean,
                  intra_recent, ov_recent, gap_after_down, n_after_down,
                  last_intra, last_ov
           FROM overnight_drift_daily WHERE asof = ?""", (asof,)).fetchall()
    if not rows:
        return None
    per = {}
    for r in rows:
        per[r[0]] = {
            "status": r[1], "levitating": bool(r[2]), "breaking": bool(r[3]),
            "intra_mean": r[4], "ov_mean": r[5], "intra_recent": r[6],
            "ov_recent": r[7], "gap_after_down": float("nan") if r[8] is None else r[8],
            "n_after_down": r[9], "last_intra": r[10], "last_ov": r[11], "asof": asof,
        }
    status = max((d["status"] for d in per.values()), key=lambda s: _SEVERITY[s])
    lev_syms = [s for s, d in per.items() if d["levitating"] and not d["breaking"]]
    brk_syms = [s for s, d in per.items() if d["breaking"]]
    headline, read = _classify(status, lev_syms, brk_syms)
    return {
        "asof": asof, "status": status, "headline": headline, "read": read,
        "symbols": per, "stale_today": asof != date.today().isoformat(),
    }
