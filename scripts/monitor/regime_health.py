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
from lib.db import DB_PATH  # noqa: E402
from scripts.qualifier import gate_config as G  # noqa: E402

ORATS_BY_TICKER = ROOT / "data/orats/by_ticker"

# ── Early-warning cascade configuration ────────────────────────────────────
# Three concentric rings of confirmation. AI is the leading edge (most
# narrative-sensitive); QQQ captures broader tech; SPY is the whole market.
# A cascade firing AI→QQQ→SPY = bull thesis decaying from the inside out.
# A reverse order (SPY→QQQ→AI) = macro shock, different playbook. The order
# of fire matters as much as the count.

AI_COHORT = ["NVDA", "MSFT", "META", "GOOGL", "AMZN", "SMH"]
RING_RED_NAME_THRESHOLD = 3       # ≥3 of 6 AI names 🔴 → ring 🔴
CASCADE_WINDOW_TRADING_DAYS = 5   # rings that transitioned within this window are "active"
RING_FAMILIES = ("ai_ring", "qqq_ring", "spy_ring")

# Bear-call census thresholds (relaxed: ignore SPY macro gate, count
# per-name bearish setups). Surfaces universe rotation before the macro
# regime gate flips.
CENSUS_IVR_THRESHOLD = 0.50
CENSUS_HISTORY_LOOKBACK_DAYS = 20  # baseline for delta comparison
CENSUS_MIN_IVR_HISTORY = 60        # need at least N days of IV history to rank


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

# Per-session memo so we don't re-read the same ORATS parquet many times when
# the same ticker appears in multiple rings + the bear-call census.
_TICKER_SERIES_CACHE: dict[str, pd.DataFrame | None] = {}
_TICKER_IV_CACHE: dict[str, pd.Series | None] = {}


def _load_ticker_daily(symbol: str) -> pd.DataFrame | None:
    """One row per trade_date with stkPx + 200-DMA. Cached per session."""
    if symbol in _TICKER_SERIES_CACHE:
        return _TICKER_SERIES_CACHE[symbol]
    p = ORATS_BY_TICKER / f"{symbol}.parquet"
    if not p.exists():
        _TICKER_SERIES_CACHE[symbol] = None
        return None
    try:
        df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
    except Exception:
        _TICKER_SERIES_CACHE[symbol] = None
        return None
    df = (df.dropna(subset=["stkPx"])
            .drop_duplicates("trade_date")
            .sort_values("trade_date")
            .reset_index(drop=True))
    if len(df) < 200:
        _TICKER_SERIES_CACHE[symbol] = None
        return None
    df["ma200"] = df["stkPx"].rolling(200).mean()
    df = df.dropna(subset=["ma200"]).reset_index(drop=True)
    _TICKER_SERIES_CACHE[symbol] = df
    return df


def _ticker_ma200(symbol: str) -> tuple[float, float] | None:
    """Returns (spot, ma200) from ORATS by_ticker. None if data missing."""
    df = _load_ticker_daily(symbol)
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    return float(last["stkPx"]), float(last["ma200"])


def _ticker_pct_to_ma200(symbol: str, lookback_5d: bool = True) -> tuple[float, float | None] | None:
    """Returns (today_pct, prior_5d_pct or None). Pct = (spot - ma200) / ma200."""
    df = _load_ticker_daily(symbol)
    if df is None or df.empty:
        return None
    last = df.iloc[-1]
    today_pct = (float(last["stkPx"]) - float(last["ma200"])) / float(last["ma200"])
    prior_pct = None
    if lookback_5d and len(df) > 5:
        p = df.iloc[-1 - 5]
        prior_pct = (float(p["stkPx"]) - float(p["ma200"])) / float(p["ma200"])
    return today_pct, prior_pct


def _ticker_atm_iv_series(symbol: str) -> pd.Series | None:
    """Per-day ATM-near, ~30-DTE-near IV time series. Cached per session.

    Picks for each trade_date the row that minimizes (|yte - 30/365|, |strike - spot|)
    across the call/put chain, then averages cMidIv and pMidIv to get a robust
    ATM IV reading. Used as input to the per-symbol IV rank.
    """
    if symbol in _TICKER_IV_CACHE:
        return _TICKER_IV_CACHE[symbol]
    p = ORATS_BY_TICKER / f"{symbol}.parquet"
    if not p.exists():
        _TICKER_IV_CACHE[symbol] = None
        return None
    try:
        df = pd.read_parquet(
            p, columns=["trade_date", "yte", "strike", "stkPx", "cMidIv", "pMidIv"]
        )
    except Exception:
        _TICKER_IV_CACHE[symbol] = None
        return None
    df = df[(df["yte"] >= 0.04) & (df["yte"] <= 0.15)]  # 15-55 calendar days
    if df.empty:
        _TICKER_IV_CACHE[symbol] = None
        return None
    df = df.dropna(subset=["stkPx"]).copy()
    df["yte_dist"] = (df["yte"] - (30 / 365.0)).abs()
    df["strike_dist"] = (df["strike"] - df["stkPx"]).abs()
    df = df.sort_values(["trade_date", "yte_dist", "strike_dist"])
    pick = df.groupby("trade_date", as_index=False).head(1)
    pick = pick.sort_values("trade_date").set_index("trade_date")
    iv = pick[["cMidIv", "pMidIv"]].mean(axis=1, skipna=True).dropna()
    _TICKER_IV_CACHE[symbol] = iv
    return iv


def _ticker_iv_rank(symbol: str, lookback_days: int = 252) -> float | None:
    """Per-symbol IVR = percentile of today's ATM IV within last `lookback_days`.

    Returns 0.0–1.0 (or None when history is insufficient). >0.50 = above
    the median, the relaxed bear-call census's elevated-IV trigger.
    """
    s = _ticker_atm_iv_series(symbol)
    if s is None or len(s) < CENSUS_MIN_IVR_HISTORY:
        return None
    window = s.tail(lookback_days)
    today_iv = float(window.iloc[-1])
    rank = float((window < today_iv).mean())
    return rank


# ── Ring assessors (early-warning cascade) ──────────────────────────────────

def _assess_name_bullish(symbol: str) -> dict | None:
    """Per-name 200-DMA assessment under a bullish-bias frame (🔴 = below).
    Used for AI-ring + QQQ-ring + SPY-ring components. Same color-coding
    rule as `assess_position` for bullish positions, but exposed here so a
    *cohort* of names can be aggregated into a ring composite.
    """
    pair = _ticker_pct_to_ma200(symbol)
    if pair is None:
        return None
    pct, prior_pct = pair
    if pct > G.SPOT_MA200_NEAR_PCT:
        s, lbl = "🟢", f"{symbol} +{pct*100:.1f}% vs 200-DMA"
    elif pct > 0:
        s, lbl = "🟡", f"{symbol} +{pct*100:.1f}% vs 200-DMA (within 3%)"
    else:
        s, lbl = "🔴", f"{symbol} {pct*100:+.1f}% vs 200-DMA (BELOW)"
    return _component(symbol, pct, prior_pct, s, lbl)


def _ring_composite_with_threshold(components: list[dict],
                                    red_name_threshold: int) -> tuple[str, int, int, str]:
    """Ring-level composite. Cohort rings need a count threshold for 🔴 because
    one name underperforming shouldn't flip the whole ring red.

    Single-name rings (QQQ, SPY) collapse cleanly: red_name_threshold=1 makes
    the ring exactly the component status.
    """
    n_y = sum(1 for c in components if c["status"] == "🟡")
    n_r = sum(1 for c in components if c["status"] == "🔴")
    if n_r >= red_name_threshold:
        return "🔴", n_y, n_r, f"RING {n_r}/{len(components)} BELOW 200-DMA"
    if n_r > 0 or n_y > 0:
        return "🟡", n_y, n_r, f"DEGRADING ({n_r} 🔴, {n_y} 🟡)"
    return "🟢", n_y, n_r, "RING HEALTHY"


def assess_ring(family_key: str, label: str, symbols: list[str],
                red_threshold: int) -> dict:
    """Generic ring assessor: AI cohort, QQQ, SPY all use this."""
    components = []
    missing = []
    for sym in symbols:
        c = _assess_name_bullish(sym)
        if c is None:
            missing.append(sym)
        else:
            components.append(c)
    if not components:
        return {
            "family": family_key,
            "gate_description": label,
            "components": [],
            "composite": "—",
            "n_yellow": 0,
            "n_red": 0,
            "composite_label": f"no ORATS data for {symbols}",
            "missing": missing,
        }
    composite, n_y, n_r, comp_label = _ring_composite_with_threshold(
        components, red_threshold
    )
    return {
        "family": family_key,
        "gate_description": label,
        "components": components,
        "composite": composite,
        "n_yellow": n_y,
        "n_red": n_r,
        "composite_label": comp_label,
        "missing": missing,
    }


def assess_ai_ring() -> dict:
    return assess_ring(
        "ai_ring",
        f"AI cohort vs 200-DMA ({len(AI_COHORT)} names)",
        AI_COHORT,
        red_threshold=RING_RED_NAME_THRESHOLD,
    )


def assess_qqq_ring() -> dict:
    return assess_ring(
        "qqq_ring",
        "QQQ vs 200-DMA",
        ["QQQ"],
        red_threshold=1,
    )


def assess_spy_ring() -> dict:
    return assess_ring(
        "spy_ring",
        "SPY vs 200-DMA",
        ["SPY"],
        red_threshold=1,
    )


# ── Cascade orchestrator ────────────────────────────────────────────────────

def _ring_history(conn: sqlite3.Connection, today: date,
                   lookback: int = CASCADE_WINDOW_TRADING_DAYS + 5) -> pd.DataFrame:
    """Last ~10 calendar days of ring composites for cascade analysis."""
    fams = ",".join(f"'{f}'" for f in RING_FAMILIES)
    df = pd.read_sql(
        f"SELECT snapshot_date, family, composite_status, n_red, n_yellow "
        f"FROM regime_health_composites "
        f"WHERE family IN ({fams}) AND snapshot_date <= '{today.isoformat()}' "
        f"ORDER BY snapshot_date",
        conn,
    )
    return df.tail(lookback * 3)  # ~3 rows/day across 3 rings


def compute_cascade(conn: sqlite3.Connection, today: date,
                     today_rings: dict) -> dict:
    """Cascade summary across the AI / QQQ / SPY rings.

    Inspects each ring's most recent transition out of 🟢 within the cascade
    window. Returns:
      - n_red_today: how many rings are 🔴 right now
      - n_yellow_today: how many are 🟡
      - active_rings: rings whose first non-🟢 day in the window is recent
      - fire_order: chronological order of those transitions
      - direction: 'thesis_decay' (AI first), 'macro_shock' (SPY first), or None
      - alert_state: 'CALM' | 'CAUTION' (any 🟡) | 'CASCADE' (2+ 🔴)
    """
    today_status = {fam: today_rings[fam]["composite"] for fam in RING_FAMILIES}
    n_red_today = sum(1 for v in today_status.values() if v == "🔴")
    n_yellow_today = sum(1 for v in today_status.values() if v == "🟡")

    # Pull history (does not include today; today's row has not been persisted yet)
    try:
        hist = _ring_history(conn, today)
    except Exception:
        hist = pd.DataFrame(columns=["snapshot_date", "family", "composite_status"])

    # Build per-ring transition info from history + today's status
    transitions = {}  # family -> {first_non_green_date_in_window, in_window}
    cutoff = today - timedelta(days=CASCADE_WINDOW_TRADING_DAYS + 2)  # cal days ≈ 5 trading
    cutoff_str = cutoff.isoformat()

    for fam in RING_FAMILIES:
        fam_hist = hist[hist["family"] == fam].copy()
        # Append today's snapshot for transition detection
        today_row = pd.DataFrame([{
            "snapshot_date": today.isoformat(), "family": fam,
            "composite_status": today_status[fam],
        }])
        fam_hist = pd.concat([fam_hist, today_row], ignore_index=True)
        fam_hist = fam_hist.sort_values("snapshot_date").reset_index(drop=True)

        # Find earliest day within the window where status is non-🟢
        in_window = fam_hist[fam_hist["snapshot_date"] >= cutoff_str]
        non_green = in_window[in_window["composite_status"] != "🟢"]
        if non_green.empty:
            transitions[fam] = {"first_date": None, "current": today_status[fam]}
            continue
        first_date = non_green["snapshot_date"].iloc[0]
        transitions[fam] = {"first_date": first_date, "current": today_status[fam]}

    # Active = first non-🟢 fell inside the window AND ring is currently non-🟢
    active = {f: t for f, t in transitions.items()
              if t["first_date"] is not None and t["current"] != "🟢"}
    # Order by first-fire date
    fire_order = sorted(active.keys(), key=lambda f: active[f]["first_date"])

    direction = None
    if len(fire_order) >= 2:
        if fire_order[0] == "ai_ring":
            direction = "thesis_decay"   # AI cracked first
        elif fire_order[0] == "spy_ring":
            direction = "macro_shock"    # SPY cracked first

    if n_red_today >= 2:
        alert_state = "CASCADE"
    elif n_yellow_today >= 1 or n_red_today >= 1:
        alert_state = "CAUTION"
    else:
        alert_state = "CALM"

    return {
        "alert_state": alert_state,
        "n_red_today": n_red_today,
        "n_yellow_today": n_yellow_today,
        "today_status": today_status,
        "transitions": transitions,
        "fire_order": fire_order,
        "direction": direction,
        "window_days": CASCADE_WINDOW_TRADING_DAYS,
    }


# ── Bear-call census (relaxed: per-name only, ignores SPY macro gate) ───────

def compute_bear_call_census(conn: sqlite3.Connection, today: date) -> dict:
    """Count of bear_call cohort names passing per-name bearish setup today.

    Pass criteria (relaxed, ignoring SPY macro gate):
      - spot < 200-DMA  (per-name bearish trend)
      - per-name IVR > CENSUS_IVR_THRESHOLD  (elevated IV)

    SPX excluded (no live spot quote). Returns count, names list, and a 20-day
    history baseline so the renderer can show a delta.
    """
    cohort = [s for s in G.COHORT_BEAR_CALL if s != "SPX"]
    passing = []
    skipped = []
    for sym in cohort:
        ma = _ticker_ma200(sym)
        if ma is None:
            skipped.append((sym, "no 200-DMA history"))
            continue
        spot, ma200 = ma
        if spot >= ma200:
            continue
        ivr = _ticker_iv_rank(sym)
        if ivr is None:
            skipped.append((sym, "insufficient IV history"))
            continue
        if ivr <= CENSUS_IVR_THRESHOLD:
            continue
        passing.append({"symbol": sym, "spot": spot, "ma200": ma200,
                         "pct_to_ma200": (spot - ma200) / ma200, "ivr": ivr})

    # 20-day history baseline for delta-vs-trend display
    try:
        baseline = pd.read_sql(
            f"SELECT snapshot_date, n_passing FROM bear_call_census_daily "
            f"WHERE snapshot_date < '{today.isoformat()}' "
            f"ORDER BY snapshot_date DESC LIMIT {CENSUS_HISTORY_LOOKBACK_DAYS}",
            conn,
        )
    except Exception:
        baseline = pd.DataFrame(columns=["snapshot_date", "n_passing"])

    if not baseline.empty:
        baseline_mean = float(baseline["n_passing"].mean())
        baseline_max = int(baseline["n_passing"].max())
        baseline_n = int(len(baseline))
    else:
        baseline_mean = None
        baseline_max = None
        baseline_n = 0

    return {
        "snapshot_date": today.isoformat(),
        "cohort_size": len(cohort),
        "n_passing": len(passing),
        "names_passing": passing,
        "skipped": skipped,
        "ivr_threshold": CENSUS_IVR_THRESHOLD,
        "baseline_mean": baseline_mean,
        "baseline_max": baseline_max,
        "baseline_n_days": baseline_n,
    }


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

    rings = {
        "ai_ring": assess_ai_ring(),
        "qqq_ring": assess_qqq_ring(),
        "spy_ring": assess_spy_ring(),
    }

    cascade = compute_cascade(conn, today, rings)
    bear_call_census = compute_bear_call_census(conn, today)

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
        "rings": rings,
        "cascade": cascade,
        "bear_call_census": bear_call_census,
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
CREATE TABLE IF NOT EXISTS bear_call_census_daily (
    snapshot_date TEXT PRIMARY KEY,
    cohort_size INTEGER,
    n_passing INTEGER,
    names_passing TEXT,
    ivr_threshold REAL
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

    # Components — both bull_put/bear_call/zebra families AND ring families
    # (ai_ring/qqq_ring/spy_ring) share the same schema, so we iterate both.
    families_and_rings = dict(assessment["families"])
    families_and_rings.update(assessment.get("rings", {}))
    for fam_name, fam in families_and_rings.items():
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
        # Composite — only families have associated open positions; rings always 0
        if fam_name in assessment["families"]:
            n_open = len(assessment["positions"].get(fam_name, []))
        else:
            n_open = 0
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

    # Bear-call census (count + names list as JSON for audit)
    census = assessment.get("bear_call_census")
    if census is not None:
        import json
        conn.execute(
            "INSERT OR REPLACE INTO bear_call_census_daily "
            "(snapshot_date, cohort_size, n_passing, names_passing, ivr_threshold) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap, census["cohort_size"], census["n_passing"],
             json.dumps([n["symbol"] for n in census["names_passing"]]),
             census["ivr_threshold"]),
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


def _render_cascade(cascade: dict, rings: dict) -> list[str]:
    """Cascade + per-ring detail block. AI → QQQ → SPY: leading edge first."""
    lines: list[str] = []
    state = cascade["alert_state"]
    if state == "CASCADE":
        header = (f"  🚨 EARLY-WARNING CASCADE — {cascade['n_red_today']} of 3 rings "
                  f"🔴 (TAKE PROFITS / EXIT POSTURE)")
    elif state == "CAUTION":
        header = (f"  ⚠ EARLY-WARNING CASCADE — caution "
                  f"({cascade['n_red_today']} 🔴, {cascade['n_yellow_today']} 🟡)")
    else:
        header = "  EARLY-WARNING CASCADE — calm (all 3 rings 🟢)"
    lines.append(header)

    # Per-ring detail
    ring_order = ("ai_ring", "qqq_ring", "spy_ring")
    ring_titles = {
        "ai_ring": f"AI ring ({len(AI_COHORT)} names)",
        "qqq_ring": "QQQ ring",
        "spy_ring": "SPY ring",
    }
    for fam in ring_order:
        r = rings[fam]
        lines.append(f"    {r['composite']} {ring_titles[fam]}: {r['composite_label']}")
        # show component lines for AI ring (cohort) — too verbose for single-name rings
        if fam == "ai_ring":
            for c in r["components"]:
                delta_str = ""
                if c["delta_5d"] is not None:
                    delta_str = f"  (5d Δ {c['delta_5d']*100:+.2f}pp {_arrow(c['delta_5d'])})"
                lines.append(f"      {c['status']} {c['label']}{delta_str}")
        elif r["components"]:
            c = r["components"][0]
            delta_str = ""
            if c["delta_5d"] is not None:
                delta_str = f"  (5d Δ {c['delta_5d']*100:+.2f}pp {_arrow(c['delta_5d'])})"
            lines.append(f"      {c['status']} {c['label']}{delta_str}")

    # Cascade ordering hint when ≥2 rings active
    if len(cascade["fire_order"]) >= 2:
        order_pretty = " → ".join(
            f.replace("_ring", "").upper() for f in cascade["fire_order"]
        )
        if cascade["direction"] == "thesis_decay":
            tag = "thesis-decay pattern (AI cracking first)"
        elif cascade["direction"] == "macro_shock":
            tag = "macro-shock pattern (SPY cracking first)"
        else:
            tag = "mixed signal"
        lines.append(f"    Cascade order ({cascade['window_days']}d window): "
                     f"{order_pretty} — {tag}")

    return lines


def _render_bear_call_census(census: dict, cascade: dict) -> list[str]:
    """One-line summary always; full names list when cascade is firing."""
    lines: list[str] = []
    n = census["n_passing"]
    cohort = census["cohort_size"]
    base = census.get("baseline_mean")
    base_str = (f"{base:.1f}" if base is not None else "—")
    base_tail = (f" (20d avg {base_str}, max {census.get('baseline_max', '—')})"
                 if census.get("baseline_n_days", 0) > 0
                 else " (no baseline yet — building history)")
    lines.append(
        f"  BEAR-CALL CENSUS (relaxed: per-name spot<200-DMA + IVR>"
        f"{census['ivr_threshold']:.2f}, ignores SPY macro gate)"
    )
    lines.append(f"    Today: {n} of {cohort} cohort names pass{base_tail}")

    # Full list when census is meaningful: all names whenever cascade in CASCADE
    # state (2+🔴 trigger), or when count is non-zero
    if n > 0 and (cascade["alert_state"] == "CASCADE" or n > (base or 0)):
        for nm in census["names_passing"]:
            lines.append(
                f"      • {nm['symbol']}: spot ${nm['spot']:.2f} "
                f"({nm['pct_to_ma200']*100:+.1f}% vs 200-DMA), IVR {nm['ivr']:.2f}"
            )
    if census.get("skipped"):
        skipped_syms = ", ".join(s for s, _ in census["skipped"])
        lines.append(f"      (skipped — insufficient data: {skipped_syms})")
    return lines


def render_text(assessment: dict) -> list[str]:
    """Returns a list of email-body lines for the REGIME HEALTH section."""
    if assessment.get("error"):
        return [f"  ⚠ Regime health: {assessment['error']}"]

    lines: list[str] = []
    fams = assessment["families"]
    pos = assessment["positions"]
    rings = assessment.get("rings")
    cascade = assessment.get("cascade")
    census = assessment.get("bear_call_census")

    # Cascade + rings (early-warning section)
    if rings and cascade:
        lines.extend(_render_cascade(cascade, rings))
        lines.append("")

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

    # Bear-call census trailer
    if census and cascade:
        lines.append("")
        lines.extend(_render_bear_call_census(census, cascade))

    return lines
