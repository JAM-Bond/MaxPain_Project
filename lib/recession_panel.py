"""Recession-probability panel — three independent lenses for the Macro Brief.

Reads Agent_Project ChromaDB (fred_historical_data + yield_curve_snapshots);
never re-scrapes (architecture rule, project_agent_project_integration_queue).

Three lenses, deliberately complementary:
  1. Estrella–Mishkin probit — the Fed's term-spread model. P(recession in 4q)
     from the 3m10y spread. LEADING (~12 months ahead). Curve-based.
  2. Sahm Rule — labor-market trigger, ORTHOGONAL to the curve. Fires when the
     3-month-avg unemployment rate rises ≥0.50pp above its prior-12-month low.
     COINCIDENT (fires ~at recession onset).
  3. Near-term forward spread (Engstrom–Sharpe 2018) — the 6-quarter-ahead
     implied 3m rate minus the current 3m. Fed research argues it subsumes the
     long-term term spread. Curve-based but forward/policy-focused.

Two curve lenses + one labor lens; leading + coincident. A confluence read
("N of 3 flashing") is more robust than any single model. Context only — feeds
the daily Macro Brief and (later) the macro positioning-risk overlay; gates
nothing on its own. See project_macro_positioning_overlay.md.

Usage:
    from lib.recession_panel import build_recession_panel, render_text
    print(render_text(build_recession_panel()))
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

AGENT_ROOT = Path.home() / "Agent_Project"
sys.path.insert(0, str(AGENT_ROOT))


def _client():
    from shared.chromadb_client import DataPipelineChromaDB
    return DataPipelineChromaDB()


def _norm_cdf(z: float) -> float:
    """Standard-normal CDF (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _fred_series(series_id: str) -> list[tuple[date, float]]:
    """Sorted (date, value) for a FRED series from Agent_Project ChromaDB."""
    db = _client()
    res = db.query_by_metadata("fred_historical_data", {"series_id": series_id})
    if not res or not res.get("metadatas"):
        return []
    out: list[tuple[date, float]] = []
    for m in res["metadatas"]:
        d, v = m.get("data_date"), m.get("value")
        if d is None or v is None:
            continue
        try:
            dd = datetime.fromisoformat(str(d)[:10]).date()
            out.append((dd, float(v)))
        except (ValueError, TypeError):
            continue
    return sorted(out)


def _latest(series_id: str) -> tuple[date, float] | None:
    s = _fred_series(series_id)
    return s[-1] if s else None


# ─── Lens 1: Estrella–Mishkin probit ──────────────────────────────────

def _probit_regime(prob: float) -> str:
    if prob < 15:
        return "LOW"
    if prob < 30:
        return "CAUTION"
    if prob < 50:
        return "ELEVATED"
    if prob < 70:
        return "HIGH"
    return "CRITICAL"


def estrella_mishkin() -> dict[str, Any]:
    """P(recession within 4 quarters) = Φ(−0.6045 − 0.7374 × spread_3m10y).
    spread in percentage points. Reads the 3m10y spread MaxPain already pulls."""
    db = _client()
    cur = db.query_by_metadata("yield_curve_snapshots",
                               {"data_type": "yield_curve_snapshot"})
    if not cur or not cur.get("metadatas"):
        return {"ok": False, "error": "yield_curve_snapshots empty"}
    md = cur["metadatas"][0]
    spread = md.get("spread_3m10y")
    if spread is None:
        return {"ok": False, "error": "no spread_3m10y"}
    spread = float(spread)
    prob = round(_norm_cdf(-0.6045 - 0.7374 * spread) * 100, 1)
    return {"ok": True, "spread_3m10y": spread, "prob_pct": prob,
            "regime": _probit_regime(prob), "flashing": prob >= 30.0,
            "asof": md.get("snapshot_date")}


# ─── Lens 2: Sahm Rule ────────────────────────────────────────────────

def sahm_rule() -> dict[str, Any]:
    """Sahm: 3-mo-avg unemployment minus its low over the prior 12 months.
    Trigger ≥ 0.50pp. Uses FRED UNRATE (monthly)."""
    ur = _fred_series("UNRATE")
    if len(ur) < 15:
        return {"ok": False, "error": f"UNRATE history too short ({len(ur)})"}
    vals = [v for _, v in ur]
    dates = [d for d, _ in ur]
    # 3-month moving averages (one per month from index 2 on)
    ma3 = [(vals[i - 2] + vals[i - 1] + vals[i]) / 3.0 for i in range(2, len(vals))]
    cur = ma3[-1]
    low12 = min(ma3[-12:])           # min 3mo-avg over the trailing 12 months
    gap = round(cur - low12, 2)
    return {"ok": True, "current_3mo_avg": round(cur, 2),
            "min_prior_12mo": round(low12, 2), "gap": gap,
            "triggered": gap >= 0.50, "flashing": gap >= 0.50,
            "latest_unrate": vals[-1], "asof": str(dates[-1])}


# ─── Lens 3: Near-term forward spread (Engstrom–Sharpe) ───────────────

def _interp_yield(pts: list[tuple[float, float]], t: float) -> float | None:
    """Linear-interpolate a yield at maturity t (years) from (maturity, yield) pts."""
    pts = sorted(pts)
    if not pts or t < pts[0][0] or t > pts[-1][0]:
        return None
    for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return y0
            return y0 + (y1 - y0) * (t - t0) / (t1 - t0)
    return None


def near_term_forward_spread() -> dict[str, Any]:
    """NTFS = (implied 3m rate 6 quarters / 18 months ahead) − (current 3m rate).
    Negative ⇒ market pricing cuts ⇒ recession signal (Engstrom–Sharpe 2018).

    Approximation: the 6q-ahead 3m forward is derived by linear-interpolating the
    available short Treasury grid (3m/6m/1y/2y) to the 1.5y and 1.75y points and
    taking the implied forward between them. This is the available-tenor proxy,
    not the Fed's fitted-zero-curve series — labelled as such in the render.
    """
    grid_ids = {0.25: "DTB3", 0.5: "DTB6", 1.0: "DTB1YR", 2.0: "DGS2"}
    pts: list[tuple[float, float]] = []
    asof = None
    for t, sid in grid_ids.items():
        lv = _latest(sid)
        if lv is None:
            continue
        pts.append((t, lv[1]))
        asof = max(asof, lv[0]) if asof else lv[0]
    cur3m = next((y for t, y in pts if t == 0.25), None)
    if cur3m is None or len(pts) < 3:
        return {"ok": False, "error": "insufficient short-tenor data"}

    y150 = _interp_yield(pts, 1.5)
    y175 = _interp_yield(pts, 1.75)
    if y150 is None or y175 is None:
        return {"ok": False, "error": "cannot interpolate 1.5y/1.75y"}

    # implied 3-month forward rate from 1.5y to 1.75y (par-yield approximation)
    fwd_3m_6q = (y175 * 1.75 - y150 * 1.5) / 0.25
    ntfs = round(fwd_3m_6q - cur3m, 2)
    if ntfs <= -0.10:
        signal = "RECESSION-WATCH"
    elif ntfs >= 0.10:
        signal = "EXPANSION"
    else:
        signal = "FLAT"
    return {"ok": True, "ntfs": ntfs, "fwd_3m_6q": round(fwd_3m_6q, 2),
            "current_3m": round(cur3m, 2), "signal": signal,
            "flashing": ntfs <= -0.10, "asof": str(asof) if asof else None}


# ─── Panel + render ───────────────────────────────────────────────────

def build_recession_panel() -> dict[str, Any]:
    em = estrella_mishkin()
    sahm = sahm_rule()
    ntfs = near_term_forward_spread()
    flashing = sum(1 for x in (em, sahm, ntfs) if x.get("ok") and x.get("flashing"))
    ok_n = sum(1 for x in (em, sahm, ntfs) if x.get("ok"))
    return {"probit": em, "sahm": sahm, "ntfs": ntfs,
            "n_flashing": flashing, "n_lenses": ok_n}


def render_text(panel: dict[str, Any]) -> str:
    lines = [f"  RECESSION PANEL — {panel['n_flashing']} of {panel['n_lenses']} "
             f"lens(es) flashing recession"]
    em = panel["probit"]
    if em.get("ok"):
        flag = "  ⚑" if em["flashing"] else ""
        lines.append(f"    Estrella–Mishkin probit (leading ~12mo): "
                     f"{em['prob_pct']:.1f}%  [{em['regime']}]  "
                     f"(3m10y {em['spread_3m10y']:+.2f}){flag}")
    else:
        lines.append(f"    Estrella–Mishkin — unavailable: {em.get('error')}")
    s = panel["sahm"]
    if s.get("ok"):
        flag = "  ⚑ TRIGGERED" if s["triggered"] else ""
        lines.append(f"    Sahm Rule (coincident, labor):  gap {s['gap']:+.2f}pp"
                     f"  (3mo-avg {s['current_3mo_avg']:.2f} vs 12mo-low "
                     f"{s['min_prior_12mo']:.2f}; trigger ≥0.50){flag}")
    else:
        lines.append(f"    Sahm Rule — unavailable: {s.get('error')}")
    nt = panel["ntfs"]
    if nt.get("ok"):
        flag = "  ⚑" if nt["flashing"] else ""
        lines.append(f"    Near-term fwd spread (Engstrom–Sharpe): {nt['ntfs']:+.2f}"
                     f"  [{nt['signal']}]  (6q-fwd 3m {nt['fwd_3m_6q']:.2f} − "
                     f"cur 3m {nt['current_3m']:.2f}; approx){flag}")
    else:
        lines.append(f"    Near-term fwd spread — unavailable: {nt.get('error')}")
    return "\n".join(lines)


def render_html(panel: dict[str, Any]) -> str:
    em, s, nt = panel["probit"], panel["sahm"], panel["ntfs"]
    rows = []
    if em.get("ok"):
        f = " ⚑" if em["flashing"] else ""
        rows.append(f"<li>Estrella–Mishkin probit <span style='color:#888'>(leading ~12mo)</span>: "
                    f"<b>{em['prob_pct']:.1f}%</b> [{em['regime']}] "
                    f"<span style='color:#888'>(3m10y {em['spread_3m10y']:+.2f})</span>{f}</li>")
    if s.get("ok"):
        f = ' <b style="color:#a00">⚑ TRIGGERED</b>' if s["triggered"] else ""
        rows.append(f"<li>Sahm Rule <span style='color:#888'>(coincident, labor)</span>: "
                    f"gap <b>{s['gap']:+.2f}pp</b> "
                    f"<span style='color:#888'>(3mo-avg {s['current_3mo_avg']:.2f} vs "
                    f"12mo-low {s['min_prior_12mo']:.2f})</span>{f}</li>")
    if nt.get("ok"):
        f = ' <b style="color:#a00">⚑</b>' if nt["flashing"] else ""
        rows.append(f"<li>Near-term fwd spread <span style='color:#888'>(Engstrom–Sharpe)</span>: "
                    f"<b>{nt['ntfs']:+.2f}</b> [{nt['signal']}] "
                    f"<span style='color:#888'>(approx)</span>{f}</li>")
    return (f'<div style="margin-top:8px"><b>RECESSION PANEL</b> '
            f'<span style="color:#888">— {panel["n_flashing"]} of {panel["n_lenses"]} '
            f'flashing</span><ul style="margin:2px 0 0 18px;padding:0;font-size:12px">'
            + "".join(rows) + "</ul></div>")


if __name__ == "__main__":
    print(render_text(build_recession_panel()))
