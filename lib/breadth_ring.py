"""Breadth ring — RSP/SPY relative-strength early-warning read (DESCRIPTIVE).

Surfaces the walk-forward-validated signal from project_rsp_spy_breadth_signal:
the RSP/SPY relative-strength trend (equal-weight vs cap-weight, ~50d) is a
trend-QUALITY / downside-RISK filter on SPY's direction —
  • broadening (RSP keeping up / outperforming)  → high-quality, low-tail advance
  • narrowing  (SPY pulling ahead, market narrows) → same uptrend, weaker + ~2× tail
  • narrowing WHILE breadth already extended       → narrow-megacap-top signature
                                                     (worst-forward state in the study)

DESCRIPTIVE ONLY. This is NOT pre-registered and does NOT gate or vote in the
exit cascade — it is an informational ring, consistent with "descriptive first,
pre-register before it gates". The validated core needs only SPY+RSP daily
(fetched live via yfinance). The breadth leg (% S&P > 50dma) is used ONLY for the
top-warning and ONLY when fresh; the breadth file currently has no refresh cron,
so a staleness guard omits it rather than asserting on month-old data.

Soft-fail by contract: every entry point returns a structured result or an error
string; callers (the daily alert) must never break if this is unavailable.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

RATIO_MA = 50            # a-priori lookback validated in the study (21d = noise)
BREADTH_EXTENDED = 70.0  # % of S&P > 50dma considered "extended" (top-warning leg)
BREADTH_MAX_STALE_DAYS = 5   # only use breadth if within this many calendar days
_PROFILE = Path.home() / "MaxPain_Project" / "data/profile"
BREADTH_LIVE = _PROFILE / "breadth_live.parquet"          # refreshed daily by the cron
BREADTH_PARQUET = _PROFILE / "breadth_spx500_v2.parquet"  # frozen research fallback


def _fetch_closes() -> Optional[pd.DataFrame]:
    """SPY + RSP daily closes (~1.5y) via yfinance. None on failure."""
    try:
        import yfinance as yf
    except Exception:
        return None
    out = {}
    for t in ("SPY", "RSP"):
        try:
            df = yf.download(t, period="500d", auto_adjust=True, progress=False)
            if df is None or df.empty:
                return None
            out[t] = pd.Series(np.asarray(df["Close"]).ravel(),
                               index=pd.to_datetime(df.index), name=t)
        except Exception:
            return None
    d = pd.DataFrame(out).dropna()
    return d if len(d) >= 210 else None


def _fresh_breadth() -> Optional[float]:
    """Latest % S&P > 50dma if fresh; else None (staleness guard). Prefer the
    daily-refreshed live file; fall back to the frozen research parquet."""
    for path in (BREADTH_LIVE, BREADTH_PARQUET):
        try:
            if not path.exists():
                continue
            b = pd.read_parquet(path)[["date", "pct_above_50dma"]]
            b["date"] = pd.to_datetime(b["date"])
            last = b.sort_values("date").iloc[-1]
            age = (pd.Timestamp(date.today()) - last["date"]).days
            if age <= BREADTH_MAX_STALE_DAYS:
                return float(last["pct_above_50dma"])
        except Exception:
            continue
    return None


def _classify(broadening: bool, top_warning: bool) -> tuple[str, str, str]:
    """Map ring state → (status emoji, headline, plain-language read). Single
    source of truth for wording, shared by live-compute and the persisted reader."""
    if broadening:
        return ("🟢", "breadth BROADENING (equal-weight keeping up with cap-weight)",
                "high-quality advance — lower downside tail historically")
    if top_warning:
        return ("🔴", "breadth NARROWING while already EXTENDED (narrow-megacap-top signature)",
                "worst-forward state in the study — weak forward + elevated drawdown risk")
    return ("🟡", "breadth NARROWING (cap-weight pulling ahead of equal-weight)",
            "same nominal uptrend, but weaker forward + ~2× downside tail historically")


def compute_breadth_ring() -> dict:
    """Compute the descriptive breadth ring. Returns a structured dict; on any
    data failure returns {'error': <str>} so the caller can soft-fail."""
    d = _fetch_closes()
    if d is None:
        return {"error": "SPY/RSP daily fetch failed (yfinance unavailable or empty)"}

    d = d.copy()
    d["ratio"] = d["RSP"] / d["SPY"]
    d["ratio_ma"] = d["ratio"].rolling(RATIO_MA).mean()
    d["spy200"] = d["SPY"].rolling(200).mean()
    d = d.dropna(subset=["ratio_ma"])
    if d.empty:
        return {"error": "insufficient history for ratio MA"}

    last = d.iloc[-1]
    asof = last.name.date()
    rs = float(last["ratio"] / last["ratio_ma"] - 1.0)   # >0 broadening, <0 narrowing
    broadening = rs > 0.0

    # run-length: trading days the current broadening/narrowing state has held
    state_series = (d["ratio"] > d["ratio_ma"])
    run = 1
    for i in range(len(state_series) - 2, -1, -1):
        if bool(state_series.iloc[i]) == bool(state_series.iloc[-1]):
            run += 1
        else:
            break

    spy_pct_200 = (float(last["SPY"]) / float(last["spy200"]) - 1.0) if pd.notna(last["spy200"]) else None
    breadth = _fresh_breadth()
    extended = breadth is not None and breadth >= BREADTH_EXTENDED
    # top-warning only assertable when breadth is fresh AND extended AND narrowing
    top_warning = (not broadening) and extended
    status, headline, read = _classify(broadening, top_warning)

    return {
        "asof": asof.isoformat(),
        "status": status,
        "headline": headline,
        "read": read,
        "rs": rs,
        "broadening": broadening,
        "run_days": run,
        "spy_pct_200": spy_pct_200,
        "breadth": breadth,
        "breadth_extended": extended,
        "top_warning": top_warning,
        "stale_today": asof != date.today(),
    }


def render_text(ring: dict) -> list[str]:
    """Render the ring as alert lines (descriptive). Empty list if unavailable
    but non-fatal; the caller decides whether to print a one-line note."""
    if ring.get("error"):
        return [f"BREADTH RING — unavailable ({ring['error']})"]
    lines = ["BREADTH RING  (RSP vs SPY — descriptive early-warning, not a gate)",
             f"  {'-'*66}",
             f"  {ring['status']} {ring['headline']}"]
    rs_pct = ring["rs"] * 100
    span = f"{ring['run_days']}d"
    detail = f"  RSP/SPY rel-strength {rs_pct:+.2f}% vs {RATIO_MA}d avg · holding {span}"
    if ring.get("spy_pct_200") is not None:
        detail += f" · SPY {ring['spy_pct_200']*100:+.1f}% vs 200-DMA"
    lines.append(detail)
    if ring.get("breadth") is not None:
        lines.append(f"  S&P breadth {ring['breadth']:.0f}% >50-DMA"
                     f"{' (EXTENDED)' if ring['breadth_extended'] else ''}")
    else:
        lines.append("  (top-warning leg omitted — S&P breadth feed stale/unavailable)")
    lines.append(f"  → {ring['read']}")
    if ring.get("stale_today"):
        lines.append(f"  [STALE] latest SPY/RSP close is {ring['asof']}, not today")
    return lines


def latest_persisted_ring(conn, max_stale_days: int = 4) -> Optional[dict]:
    """Read the most recent ring row written by the refresh cron and rebuild the
    render dict. Returns None if no row, the table is absent, or the row is older
    than max_stale_days (the alert then falls back to a live compute)."""
    try:
        row = conn.execute(
            """SELECT asof, status, rs, broadening, run_days, spy_pct_200, breadth,
                      breadth_extended, top_warning
               FROM breadth_ring_daily ORDER BY asof DESC LIMIT 1"""
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    asof = row[0]
    try:
        age = (pd.Timestamp(date.today()) - pd.Timestamp(asof)).days
    except Exception:
        age = 0
    if age > max_stale_days:
        return None
    broadening, top_warning = bool(row[3]), bool(row[8])
    _, headline, read = _classify(broadening, top_warning)
    return {
        "asof": asof, "status": row[1], "headline": headline, "read": read,
        "rs": row[2], "broadening": broadening, "run_days": row[4],
        "spy_pct_200": row[5], "breadth": row[6], "breadth_extended": bool(row[7]),
        "top_warning": top_warning, "stale_today": asof != date.today().isoformat(),
    }


def persist(ring: dict, conn) -> None:
    """Append today's ring to breadth_ring_daily (history substrate). Soft-fail."""
    if ring.get("error"):
        return
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS breadth_ring_daily (
                   asof TEXT PRIMARY KEY, status TEXT, rs REAL, broadening INTEGER,
                   run_days INTEGER, spy_pct_200 REAL, breadth REAL,
                   breadth_extended INTEGER, top_warning INTEGER)"""
        )
        conn.execute(
            """INSERT OR REPLACE INTO breadth_ring_daily
               (asof, status, rs, broadening, run_days, spy_pct_200, breadth,
                breadth_extended, top_warning) VALUES (?,?,?,?,?,?,?,?,?)""",
            (ring["asof"], ring["status"], ring["rs"], int(ring["broadening"]),
             ring["run_days"], ring.get("spy_pct_200"), ring.get("breadth"),
             int(ring["breadth_extended"]), int(ring["top_warning"])),
        )
        conn.commit()
    except Exception:
        pass
