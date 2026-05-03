"""
Regime-health monitor — system-wide + per-position daily surveillance.

Built 2026-05-03. Mirrors the entry gates in cycle_qualifier.py but applies
them as a continuous health check, not a binary entry decision. The same
signals that justify entry into bull_put / bear_call / zebra are watched
for degradation; the email surfaces 🟢 / 🟡 / 🔴 status per component, per
family, and per open position.

Persistence: writes to regime_health_snapshots, regime_health_composites,
and position_health_snapshots. After 30-60 days of data we can audit how
many days the warning bands fired before actual gate flips or position
losses — a feedback loop on the early-warning system itself.

Out of scope (deferred to v2):
  - inverted_fly: directionally neutral; user explicitly exempted
  - IV/HV ratio per name: adds another axis; revisit if 200-DMA proves
    insufficient
  - Bear/bull bias checks for IF/covered_call: structures themselves are
    direction-neutral
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from scripts.qualifier import gate_config as G  # noqa: E402

DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"
ORATS_BY_TICKER = ROOT / "data/orats/by_ticker"


# ── Component assessors ─────────────────────────────────────────────────────

def _component(name: str, value: float, prior_5d: float | None,
               status: str, label: str) -> dict:
    """Pack a single component reading into the standard dict shape."""
    delta_5d = (value - prior_5d) if (prior_5d is not None) else None
    return {
        "name": name,
        "value": value,
        "delta_5d": delta_5d,
        "status": status,
        "label": label,
    }


def _assess_term_spread(ts: float, prior: float | None) -> dict:
    """bull_put gate: needs term_spread < 0 (contango)."""
    if ts < -G.TERM_SPREAD_NEAR_BAND:
        s, lbl = "🟢", f"contango {ts:+.4f}"
    elif ts < 0:
        s, lbl = "🟡", f"narrowing contango {ts:+.4f} (near 0)"
    else:
        s, lbl = "🔴", f"INVERTED {ts:+.4f}"
    return _component("term_spread", ts, prior, s, lbl)


def _assess_vrp(vrp: float, prior: float | None) -> dict:
    """bull_put gate: needs VRP > 0."""
    if vrp > G.VRP_NEAR_BAND:
        s, lbl = "🟢", f"VRP {vrp:+.4f}"
    elif vrp > 0:
        s, lbl = "🟡", f"VRP narrowing {vrp:+.4f} (near 0)"
    else:
        s, lbl = "🔴", f"VRP NEGATIVE {vrp:+.4f}"
    return _component("vrp", vrp, prior, s, lbl)


def _assess_spy_above_ma200(pct: float, prior: float | None) -> dict:
    """bull_put / hard-pause gate: needs SPY pct_to_ma200 > 0 (above)."""
    if pct > G.SPY_MA200_NEAR_PCT:
        s, lbl = "🟢", f"SPY +{pct*100:.1f}% vs 200-DMA"
    elif pct > 0:
        s, lbl = "🟡", f"SPY +{pct*100:.1f}% vs 200-DMA (within 3%)"
    else:
        s, lbl = "🔴", f"SPY {pct*100:+.1f}% vs 200-DMA (BELOW)"
    return _component("spy_pct_to_ma200", pct, prior, s, lbl)


def _assess_ivr_low(ivr: float, prior: float | None) -> dict:
    """bull_put / hard-pause: hard-pause active when IVR > 0.5; want low."""
    if ivr < 0.5 - G.IVR_NEAR_BAND:
        s, lbl = "🟢", f"IVR {ivr:.2f}"
    elif ivr < 0.5:
        s, lbl = "🟡", f"IVR {ivr:.2f} (approaching 0.50)"
    else:
        s, lbl = "🔴", f"IVR {ivr:.2f} (HARD-PAUSE TRIGGER)"
    return _component("ivr", ivr, prior, s, lbl)


def _assess_spy_below_ma200(pct: float, prior: float | None) -> dict:
    """bear_call H1 gate: needs SPY pct_to_ma200 < 0 (below)."""
    if pct < -G.SPY_MA200_NEAR_PCT:
        s, lbl = "🟢", f"SPY {pct*100:+.1f}% vs 200-DMA"
    elif pct < 0:
        s, lbl = "🟡", f"SPY {pct*100:+.1f}% vs 200-DMA (within 3%)"
    else:
        s, lbl = "🔴", f"SPY +{pct*100:.1f}% vs 200-DMA (ABOVE — H1 BROKEN)"
    return _component("spy_pct_to_ma200", pct, prior, s, lbl)


def _assess_ivr_high(ivr: float, prior: float | None) -> dict:
    """bear_call H1 gate: needs IVR > 0.5."""
    if ivr > 0.5 + G.IVR_NEAR_BAND:
        s, lbl = "🟢", f"IVR {ivr:.2f}"
    elif ivr > 0.5:
        s, lbl = "🟡", f"IVR {ivr:.2f} (approaching 0.50)"
    else:
        s, lbl = "🔴", f"IVR {ivr:.2f} (BELOW 0.50 — H1 BROKEN)"
    return _component("ivr", ivr, prior, s, lbl)


def _composite(components: list[dict]) -> tuple[str, int, int, str]:
    """Combine component statuses into a family-level verdict.

    Returns (composite_emoji, n_yellow, n_red, label).
    """
    n_y = sum(1 for c in components if c["status"] == "🟡")
    n_r = sum(1 for c in components if c["status"] == "🔴")
    if n_r > 0:
        return "🔴", n_y, n_r, "GATE INACTIVE"
    if n_y > 0:
        return "🟡", n_y, n_r, "DEGRADING"
    return "🟢", n_y, n_r, "GATE HEALTHY"


# ── Family assessors ────────────────────────────────────────────────────────

def assess_bull_put(latest: dict, prior_5d: dict | None) -> dict:
    """4-component health: term_spread + VRP + SPY>200DMA + IVR<0.5."""
    p = prior_5d or {}
    components = [
        _assess_term_spread(latest["spy_term_spread"], p.get("spy_term_spread")),
        _assess_vrp(latest["spy_vrp"], p.get("spy_vrp")),
        _assess_spy_above_ma200(latest["spy_pct_to_ma200"], p.get("spy_pct_to_ma200")),
        _assess_ivr_low(latest["spy_ivr_252"], p.get("spy_ivr_252")),
    ]
    composite, n_y, n_r, label = _composite(components)
    return {
        "family": "bull_put",
        "gate_description": "contango + VRP+ + SPY≥200-DMA + IVR<0.50",
        "components": components,
        "composite": composite,
        "n_yellow": n_y,
        "n_red": n_r,
        "composite_label": label,
    }


def assess_bear_call(latest: dict, prior_5d: dict | None) -> dict:
    """2-component health: H1 = SPY<200DMA + IVR>0.5."""
    p = prior_5d or {}
    components = [
        _assess_spy_below_ma200(latest["spy_pct_to_ma200"], p.get("spy_pct_to_ma200")),
        _assess_ivr_high(latest["spy_ivr_252"], p.get("spy_ivr_252")),
    ]
    composite, n_y, n_r, label = _composite(components)
    return {
        "family": "bear_call",
        "gate_description": "H1: SPY<200-DMA + IVR>0.50",
        "components": components,
        "composite": composite,
        "n_yellow": n_y,
        "n_red": n_r,
        "composite_label": label,
    }


def assess_zebra() -> dict:
    """ZEBRA has no SPY-level entry gate — only per-name 200-DMA persistence
    (checked at qualifier entry-time). System-level health is N/A; the
    per-position renderer covers each open ZEBRA's name-level trend."""
    return {
        "family": "zebra",
        "gate_description": "per-name 200-DMA persistence (no system gate)",
        "components": [],
        "composite": "—",
        "n_yellow": 0,
        "n_red": 0,
        "composite_label": "N/A — see per-position lines",
    }


# ── Per-position assessor ───────────────────────────────────────────────────

def _ticker_ma200(symbol: str) -> tuple[float, float] | None:
    """Returns (spot, ma200) from ORATS by_ticker. None if data missing."""
    p = ORATS_BY_TICKER / f"{symbol}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
    except Exception:
        return None
    df = df.dropna(subset=["stkPx"]).drop_duplicates("trade_date").sort_values("trade_date")
    if len(df) < 200:
        return None
    df["ma200"] = df["stkPx"].rolling(200).mean()
    df = df.dropna(subset=["ma200"])
    if df.empty:
        return None
    last = df.iloc[-1]
    return float(last["stkPx"]), float(last["ma200"])


def _position_bias(structure: str) -> str | None:
    """Directional bias of a structure: bullish, bearish, or None (neutral)."""
    s = (structure or "").lower()
    if (s.startswith("zebra") or s.startswith("bull_put")
            or s == "stock" or s == "covered_call"):
        return "bullish"
    if s.startswith("bear_call"):
        return "bearish"
    return None


def assess_position(position: dict, family_status: str) -> dict | None:
    """Per-position status. Returns None for direction-neutral structures."""
    sym = position.get("symbol")
    struct = (position.get("structure") or "").lower()
    bias = _position_bias(struct)
    if bias is None or not sym:
        return None

    ma = _ticker_ma200(sym)
    if ma is None:
        return {
            "trade_id": position.get("id"),
            "symbol": sym, "structure": struct,
            "spot": None, "ma200": None, "pct": None,
            "name_status": "—",
            "name_label": f"no ORATS history for {sym}",
            "system_status": family_status,
            "combined_status": family_status,
        }
    spot, ma200 = ma
    pct = (spot - ma200) / ma200

    # Name-level status by directional bias
    if bias == "bullish":
        if pct > G.SPOT_MA200_NEAR_PCT:
            n_status = "🟢"
            n_label = f"spot ${spot:.2f} vs 200-DMA ${ma200:.2f} ({pct*100:+.1f}%)"
        elif pct > 0:
            n_status = "🟡"
            n_label = (f"spot ${spot:.2f} vs 200-DMA ${ma200:.2f} "
                       f"({pct*100:+.1f}% — within 3% of trend support)")
        else:
            n_status = "🔴"
            n_label = (f"spot ${spot:.2f} vs 200-DMA ${ma200:.2f} "
                       f"({pct*100:+.1f}% — BELOW trend; bullish thesis under stress)")
    else:  # bearish
        if pct < -G.SPOT_MA200_NEAR_PCT:
            n_status = "🟢"
            n_label = f"spot ${spot:.2f} vs 200-DMA ${ma200:.2f} ({pct*100:+.1f}%)"
        elif pct < 0:
            n_status = "🟡"
            n_label = (f"spot ${spot:.2f} vs 200-DMA ${ma200:.2f} "
                       f"({pct*100:+.1f}% — within 3% of trend resistance)")
        else:
            n_status = "🔴"
            n_label = (f"spot ${spot:.2f} vs 200-DMA ${ma200:.2f} "
                       f"({pct*100:+.1f}% — ABOVE trend; bearish thesis under stress)")

    # Combined = worst-of(system, name). Order: 🔴 > 🟡 > 🟢 > —
    rank = {"🔴": 3, "🟡": 2, "🟢": 1, "—": 0}
    sys_rank = rank.get(family_status, 0)
    name_rank = rank.get(n_status, 0)
    if max(sys_rank, name_rank) == 3:
        combined = "🔴"
    elif max(sys_rank, name_rank) == 2:
        combined = "🟡"
    elif max(sys_rank, name_rank) == 1:
        combined = "🟢"
    else:
        combined = "—"

    return {
        "trade_id": position.get("id"),
        "symbol": sym, "structure": struct,
        "spot": spot, "ma200": ma200, "pct": pct,
        "name_status": n_status,
        "name_label": n_label,
        "system_status": family_status,
        "combined_status": combined,
    }


# ── Orchestrator: load + assess all ─────────────────────────────────────────

def load_regime_state_pair(conn: sqlite3.Connection, today: date,
                           lookback_days: int = G.TREND_VELOCITY_LOOKBACK_DAYS) -> tuple[dict | None, dict | None]:
    """Latest regime_state row + the row from ~lookback_days ago for velocity."""
    latest = conn.execute(
        "SELECT * FROM regime_state WHERE snapshot_date <= ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (today.isoformat(),),
    ).fetchone()
    if latest is None:
        return None, None
    cols = [d[1] for d in conn.execute("PRAGMA table_info(regime_state)").fetchall()]
    latest_d = dict(zip(cols, latest))
    target = (today - timedelta(days=lookback_days)).isoformat()
    prior = conn.execute(
        "SELECT * FROM regime_state WHERE snapshot_date <= ? "
        "ORDER BY snapshot_date DESC LIMIT 1",
        (target,),
    ).fetchone()
    prior_d = dict(zip(cols, prior)) if prior else None
    return latest_d, prior_d


def family_for_structure(structure: str) -> str | None:
    s = (structure or "").lower()
    if s.startswith("bull_put"):
        return "bull_put"
    if s.startswith("bear_call"):
        return "bear_call"
    if s.startswith("zebra"):
        return "zebra"
    return None


def assess_all(conn: sqlite3.Connection, today: date,
               positions: pd.DataFrame) -> dict:
    """Run system-level + per-position assessment. Returns dict for renderer
    + persistence."""
    latest, prior = load_regime_state_pair(conn, today)
    if latest is None:
        return {"error": "regime_state empty — cannot assess"}

    families = {
        "bull_put": assess_bull_put(latest, prior),
        "bear_call": assess_bear_call(latest, prior),
        "zebra": assess_zebra(),
    }

    # Per-position assessments grouped by family
    per_pos: dict[str, list[dict]] = {f: [] for f in families}
    if positions is not None and not positions.empty:
        for _, p in positions.iterrows():
            fam = family_for_structure(p.get("structure", ""))
            if fam is None:
                continue
            family_status = families[fam]["composite"]
            assessment = assess_position(p.to_dict(), family_status)
            if assessment is not None:
                per_pos[fam].append(assessment)

    return {
        "snapshot_date": str(today),
        "latest_regime_state_date": latest.get("snapshot_date"),
        "families": families,
        "positions": per_pos,
    }


# ── Persistence ─────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS regime_health_snapshots (
    snapshot_date TEXT NOT NULL,
    family TEXT NOT NULL,
    component_name TEXT NOT NULL,
    component_value REAL,
    component_status TEXT,
    delta_5d REAL,
    PRIMARY KEY (snapshot_date, family, component_name)
);
CREATE TABLE IF NOT EXISTS regime_health_composites (
    snapshot_date TEXT NOT NULL,
    family TEXT NOT NULL,
    composite_status TEXT,
    composite_label TEXT,
    n_yellow INTEGER,
    n_red INTEGER,
    open_positions INTEGER,
    PRIMARY KEY (snapshot_date, family)
);
CREATE TABLE IF NOT EXISTS position_health_snapshots (
    snapshot_date TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    symbol TEXT,
    structure TEXT,
    spot REAL,
    ma200 REAL,
    pct_vs_ma200 REAL,
    name_status TEXT,
    system_status TEXT,
    combined_status TEXT,
    PRIMARY KEY (snapshot_date, trade_id)
);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    conn.commit()


def persist(conn: sqlite3.Connection, assessment: dict) -> None:
    """Idempotent INSERT OR REPLACE for one snapshot_date."""
    if assessment.get("error"):
        return
    ensure_tables(conn)
    snap = assessment["snapshot_date"]

    # Components
    for fam_name, fam in assessment["families"].items():
        for c in fam["components"]:
            conn.execute(
                "INSERT OR REPLACE INTO regime_health_snapshots "
                "(snapshot_date, family, component_name, component_value, "
                " component_status, delta_5d) VALUES (?, ?, ?, ?, ?, ?)",
                (snap, fam_name, c["name"],
                 float(c["value"]) if c["value"] is not None else None,
                 c["status"],
                 float(c["delta_5d"]) if c["delta_5d"] is not None else None),
            )
        # Composite
        n_open = len(assessment["positions"].get(fam_name, []))
        conn.execute(
            "INSERT OR REPLACE INTO regime_health_composites "
            "(snapshot_date, family, composite_status, composite_label, "
            " n_yellow, n_red, open_positions) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (snap, fam_name, fam["composite"], fam["composite_label"],
             fam["n_yellow"], fam["n_red"], n_open),
        )

    # Positions
    for fam_name, pos_list in assessment["positions"].items():
        for p in pos_list:
            conn.execute(
                "INSERT OR REPLACE INTO position_health_snapshots "
                "(snapshot_date, trade_id, symbol, structure, spot, ma200, "
                " pct_vs_ma200, name_status, system_status, combined_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (snap,
                 int(p["trade_id"]) if p.get("trade_id") is not None else -1,
                 p["symbol"], p["structure"],
                 p.get("spot"), p.get("ma200"), p.get("pct"),
                 p["name_status"], p["system_status"], p["combined_status"]),
            )
    conn.commit()


# ── Renderer ────────────────────────────────────────────────────────────────

def _arrow(delta: float | None) -> str:
    if delta is None:
        return ""
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "→"


def render_text(assessment: dict) -> list[str]:
    """Returns a list of email-body lines for the REGIME HEALTH section."""
    if assessment.get("error"):
        return [f"  ⚠ Regime health: {assessment['error']}"]

    lines: list[str] = []
    fams = assessment["families"]
    pos = assessment["positions"]

    # bull_put
    fb = fams["bull_put"]
    lines.append(f"  bull_put gate ({fb['gate_description']})")
    for c in fb["components"]:
        delta_str = ""
        if c["delta_5d"] is not None:
            delta_str = f"  (5d Δ {c['delta_5d']:+.4f} {_arrow(c['delta_5d'])})"
        lines.append(f"    {c['status']} {c['label']}{delta_str}")
    lines.append(
        f"    Composite: {fb['composite']} {fb['composite_label']} "
        f"({fb['n_yellow']} 🟡, {fb['n_red']} 🔴)"
    )
    lines.append(f"    Open positions: {len(pos['bull_put'])} bull_put")

    lines.append("")

    # bear_call
    fc = fams["bear_call"]
    lines.append(f"  bear_call gate ({fc['gate_description']})")
    for c in fc["components"]:
        delta_str = ""
        if c["delta_5d"] is not None:
            delta_str = f"  (5d Δ {c['delta_5d']:+.4f} {_arrow(c['delta_5d'])})"
        lines.append(f"    {c['status']} {c['label']}{delta_str}")
    lines.append(
        f"    Composite: {fc['composite']} {fc['composite_label']} "
        f"({fc['n_yellow']} 🟡, {fc['n_red']} 🔴)"
    )
    lines.append(f"    Open positions: {len(pos['bear_call'])} bear_call")

    lines.append("")

    # zebra
    fz = fams["zebra"]
    lines.append(f"  zebra gate ({fz['gate_description']})")
    lines.append(f"    {fz['composite_label']}")
    lines.append(f"    Open positions: {len(pos['zebra'])} zebra")

    # Per-position health
    has_positions = any(pos[f] for f in pos)
    if has_positions:
        lines.append("")
        lines.append(f"  POSITION HEALTH")
        lines.append(f"  {'-'*68}")
        for fam_name in ("bull_put", "bear_call", "zebra"):
            for p in pos[fam_name]:
                head = f"  {p['combined_status']} {p['symbol']} {p['structure']}"
                if p.get("spot") is not None:
                    lines.append(
                        f"{head}: {p['name_label']}  "
                        f"[sys {p['system_status']} + name {p['name_status']} = {p['combined_status']}]"
                    )
                else:
                    lines.append(f"{head}: {p['name_label']}")

    return lines
