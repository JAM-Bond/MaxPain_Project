#!/usr/bin/env python3.11
"""
MaxPain cycle qualifier — daily GO/PENDING/SKIP grid for the deployable cohort
~/MaxPain_Project/scripts/qualifier/cycle_qualifier.py

Reads:
  - regime_state (populated by 9:20 ET research_cohort_snapshot.py)
  - research_cohort_v15.parquet (cohort + structure-membership flags)
  - earnings calendar (yfinance, cached daily)

Applies the gate logic from TRADING_PLAN.rtf v1.7 and emits per-(symbol,
structure) verdicts: GO, DOWNSIZE, PENDING, SKIP, PAUSE, NOT_IN_COHORT.

Design doc: project_cycle_qualifier_design.md memory.

Persistence:
  - cycle_qualifier_runs table in maxpain.db (one row per verdict)
  - parquet artifact at data/qualifier/qualifier_<run_date>.parquet
  - human-readable console output

Cadence: daily during entry windows; on-demand outside them.
First production runs:
  - 2026-05-05  (45-DTE for June OpEx 2026-06-19)  — IF window
  - 2026-05-08  (T-5 for May OpEx 2026-05-15)      — bull_put / bear_call
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH, connect  # noqa: E402
from lib.opex_calendar import (  # noqa: E402
    current_opex, next_n_opexes, trading_day_offset, trading_days_between,
    calendar_days_before,
)
from scripts.qualifier import gate_config as G  # noqa: E402
from scripts.qualifier.earnings_calendar import (  # noqa: E402
    upcoming_earnings, upcoming_earnings_with_status,
)
from lib.sector_map import get_sector, is_cap_exempt, ETF_SENTINEL, UNKNOWN_SENTINEL  # noqa: E402
from lib.adjusted_close import load_adjusted_close  # noqa: E402
from lib import macro_profile as macro_lib  # noqa: E402

log = logging.getLogger(__name__)

ROOT = Path.home() / "MaxPain_Project"
COHORT_PATH = ROOT / "data/profile/research_cohort_v15.parquet"
QUALIFIER_DIR = ROOT / "data/qualifier"
ORATS_BY_TICKER = ROOT / "data/orats/by_ticker"


import functools


@functools.lru_cache(maxsize=64)
def _adjusted_ma200(symbol: str) -> pd.DataFrame | None:
    """Split-adjusted close + 200-DMA for a symbol, or None when history is
    missing/short. Uses lib.adjusted_close so the 200-DMA isn't corrupted by an
    unadjusted ORATS split discontinuity (see reference_orats_split_adjustment).
    Back-adjustment leaves the most-recent segment unchanged, so the latest
    close still equals the true current price.

    Returns a DataFrame indexed by trade_date with columns [close, ma200],
    rows where ma200 is defined. None on missing file / read error / <200 rows.
    """
    p = ORATS_BY_TICKER / f"{symbol}.parquet"
    if not p.exists():
        return None
    try:
        s = load_adjusted_close(symbol).dropna().sort_index()
    except Exception:
        return None
    if len(s) < 200:
        return None
    df = pd.DataFrame({"close": s, "ma200": s.rolling(200).mean()})
    df = df.dropna(subset=["ma200"])
    return df if not df.empty else None


def zebra_trend_status(symbol: str) -> dict | None:
    """Persistence-trend check for ZEBRA: sustained downtrend = name has been
    below its 200-DMA for ≥G.ZEBRA_TREND_BELOW_200DMA_THRESHOLD of the last
    G.ZEBRA_TREND_LOOKBACK_DAYS trading days. Returns None when ORATS data
    is missing — the gate fails open in that case (legacy v1 cohort names
    may not be in the v2 ORATS pool).
    """
    df = _adjusted_ma200(symbol)
    if df is None:
        return None
    tail = df.tail(G.ZEBRA_TREND_LOOKBACK_DAYS)
    days_below = int((tail["close"] < tail["ma200"]).sum())
    return {
        "sustained_downtrend": days_below >= G.ZEBRA_TREND_BELOW_200DMA_THRESHOLD,
        "days_below": days_below,
        "lookback": len(tail),
    }


def bull_put_ma_pct(symbol: str) -> float | None:
    """Return (spot - ma200) / ma200 for the latest available trade_date.
    Used by the bull_put MA-bucket DOWNSIZE gate
    (project_bullput_below_ma_findings.md, 2026-05-05). Returns None when
    history is missing — caller fails open.
    """
    df = _adjusted_ma200(symbol)
    if df is None:
        return None
    last = df.iloc[-1]
    spot = float(last["close"])
    ma = float(last["ma200"])
    if ma <= 0:
        return None
    return (spot - ma) / ma


@functools.lru_cache(maxsize=1)
def _breadth_red_today() -> bool:
    """Latest breadth-ring 🔴 (narrowing + extended) state, for the sealed ZEBRA
    sizing gate (docs/BREADTH_RING_ZEBRA_SIZING_PREREG.md). Reads the most recent
    breadth_ring_daily row written by the 16:30 refresh cron (a ~1-day lag at the
    9:25 qualifier run, which the slow signal tolerates). Cached once per run.
    Fail-open to False (no downsize) if unavailable. Only ever called when the
    gate flag is ON — inert during the paper window."""
    try:
        import sqlite3
        from lib.db import DB_PATH
        from lib.breadth_ring import latest_persisted_ring
        conn = sqlite3.connect(DB_PATH)
        try:
            ring = latest_persisted_ring(conn)
        finally:
            conn.close()
        return bool(ring and ring.get("top_warning"))
    except Exception:
        return False


# ─── Live spot lookup for budget-cap gate ─────────────────────────────

def fetch_schwab_spots(symbols: list[str]) -> dict[str, float]:
    """Bulk live quote fetch from Schwab /marketdata/v1/quotes.

    Used by the budget-cap gate (G.BUDGET_CAPS) to apply the
    expensive-names → verticals-only rule from
    feedback_expensive_names_verticals_only.md.

    Returns {symbol: lastPrice}. Returns empty dict on auth failure — the
    caller falls back to "no budget gate" behavior so the qualifier never
    fails closed on a Schwab outage.
    """
    if not symbols:
        return {}
    try:
        from Schwab.auth import get_valid_token
    except Exception as e:
        log.warning("Schwab auth import failed; budget gate disabled: %s", e)
        return {}
    try:
        token = get_valid_token()
    except Exception as e:
        log.warning("Schwab token fetch failed; budget gate disabled: %s", e)
        return {}

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = "https://api.schwabapi.com/marketdata/v1/quotes"
    out = {}
    CHUNK = 50
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        try:
            resp = requests.get(
                url, headers=headers,
                params={"symbols": ",".join(chunk), "fields": "quote"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("Schwab /quotes chunk %d failed: %s", i, e)
            continue
        for sym, info in data.items():
            quote = info.get("quote", {})
            px = quote.get("lastPrice") or quote.get("regularMarketLastPrice")
            if px:
                out[sym.upper()] = float(px)
    return out


# ─── Regime state loader ──────────────────────────────────────────────

# Age bound on the regime row (go-live audit D4/C5). The 9:20 snapshot
# writer stamps snapshot_date = run day, so on a healthy morning the row is
# same-day. snapshot_date < run_date means the 9:20 cron failed or its
# stale-ORATS guard tripped (it already emailed) — verdicts still run but
# every actionable row is annotated. Beyond REGIME_REFUSE_AFTER_DAYS
# calendar days the pipeline is genuinely broken: refuse to emit OpEx
# verdicts at all rather than qualify on ancient regime data.
REGIME_REFUSE_AFTER_DAYS = 5


def load_regime_state(run_date: date) -> tuple[dict | None, str | None]:
    """Most recent regime_state row on or before run_date.

    Returns (row dict or None, staleness warning or None). The row is None
    when the table is empty OR the freshest row is older than
    REGIME_REFUSE_AFTER_DAYS — in both cases no OpEx verdicts are emitted.
    """
    conn = connect()
    try:
        cur = conn.execute(
            "SELECT * FROM regime_state WHERE snapshot_date <= ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (str(run_date),),
        )
        row = cur.fetchone()
        if row is None:
            return None, None
        cols = [d[0] for d in cur.description]
        regime = dict(zip(cols, row))
    finally:
        conn.close()

    snap = date.fromisoformat(str(regime["snapshot_date"]))
    age = (run_date - snap).days
    if age > REGIME_REFUSE_AFTER_DAYS:
        return None, (
            f"regime_state is {age} days old (snapshot {snap}, as-of close "
            f"{regime.get('as_of_close')}) — beyond the {REGIME_REFUSE_AFTER_DAYS}-day "
            f"refuse bound; NO OpEx verdicts emitted. Fix the 9:20 snapshot "
            f"pipeline (ORATS delivery / research_cohort cron)."
        )
    if age > 0:
        return regime, (
            f"regime_state is from {snap} ({age}d old; as-of close "
            f"{regime.get('as_of_close')}) — today's 9:20 snapshot writer did not "
            f"run or its stale-ORATS guard tripped. Verdicts below use the older "
            f"regime row; verify before acting."
        )
    return regime, None


# ─── Entry-window calculation ─────────────────────────────────────────

def compute_window_targets(run_date: date) -> dict:
    """For each entry window, return the next target entry day.

    Entry day = the date the qualifier would emit GO for that window's
    cohort, assuming all gates pass.

    Returns a dict keyed by window label, value = (target_date, target_opex,
    days_until).
    """
    out = {}
    # 5 OpEx lookahead so ZEBRA's 75-DTE entry (~2.5 cycles out) is always visible
    opexes = next_n_opexes(5, today=run_date)

    # ── Covered-call window: trading day AFTER prior monthly OpEx ──────
    # Logic: covered-call entry = first trading day after the OpEx that
    # opened the current monthly cycle, expiry = next monthly OpEx.
    # If today is past tolerance for the current cycle, look at the next.
    if opexes:
        from lib.opex_calendar import third_friday

        def _prior_opex(opex_d: date) -> date:
            yr = opex_d.year if opex_d.month > 1 else opex_d.year - 1
            mo = opex_d.month - 1 if opex_d.month > 1 else 12
            return third_friday(yr, mo)

        for opex in opexes[:3]:
            entry_day = trading_day_offset(_prior_opex(opex), 1)
            days_until = trading_days_between(run_date, entry_day)
            within_tolerance = days_until >= -G.WINDOW_COVERED_CALL_AFTER_OPEX_TOLERANCE
            if not within_tolerance:
                continue
            existing = out.get("covered_call_monthly")
            if existing is None or existing[2] > days_until:
                out["covered_call_monthly"] = (entry_day, opex, days_until)
            if days_until >= 0:
                break  # found the soonest forward entry; stop here

    for opex in opexes:
        # 45-DTE entry: calendar days back from OpEx, snapped to nearest weekday
        target_45 = calendar_days_before(opex, 45)
        if target_45.weekday() >= 5:
            # if it falls on weekend, shift to Friday (prior trading day)
            shift = target_45.weekday() - 4
            target_45 = target_45 - pd.Timedelta(days=shift).to_pytimedelta()
        days_until_45 = trading_days_between(run_date, target_45)
        if days_until_45 >= 0:  # only future or today
            for label in ("bull_put_45dte", "bear_call_45dte", "inverted_fly_45dte"):
                if label not in out or out[label][2] > days_until_45:
                    out[label] = (target_45, opex, days_until_45)

        # T-5 entry: 5 trading days before OpEx
        target_t5 = trading_day_offset(opex, -G.WINDOW_BULL_PUT_T5)
        days_until_t5 = trading_days_between(run_date, target_t5)
        if days_until_t5 >= 0:
            if "bull_put_t5" not in out or out["bull_put_t5"][2] > days_until_t5:
                out["bull_put_t5"] = (target_t5, opex, days_until_t5)

        # 75-DTE entry: ZEBRA
        target_75 = calendar_days_before(opex, 75)
        if target_75.weekday() >= 5:
            shift = target_75.weekday() - 4
            target_75 = target_75 - pd.Timedelta(days=shift).to_pytimedelta()
        days_until_75 = trading_days_between(run_date, target_75)
        if days_until_75 >= 0:
            if "zebra_75dte" not in out or out["zebra_75dte"][2] > days_until_75:
                out["zebra_75dte"] = (target_75, opex, days_until_75)

    return out


# ─── Per-(symbol, structure) verdict logic ───────────────────────────

def evaluate_opex_cell(symbol: str, structure: str, window_label: str,
                       target: date, opex: date, days_until: int,
                       regime: dict, run_date: date,
                       earnings_dates: list[date] | None = None,
                       spots: dict[str, float] | None = None) -> dict:
    """Apply gate + window logic to one (symbol, structure, window) tuple.

    Returns a row dict with fields: symbol, structure, window, target, opex,
    days_until, verdict, size, reason.
    """
    row = {
        "symbol": symbol, "structure": structure, "window": window_label,
        "target": str(target), "opex": str(opex),
        "days_until": days_until, "verdict": None, "size": 0.0, "reason": "",
    }

    # 0. Past target — calendar comparison; trading_days_between is unreliable
    # in sign across weekends.
    if target < run_date:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = f"target {target} already past"
        return row

    # 0.5 Budget cap (capital-outlay structures only).
    # Per feedback_expensive_names_verticals_only.md: ZEBRA / IF reserved
    # for sub-cap names. Skipped silently when spot is unavailable so a
    # Schwab outage does not fail the qualifier closed.
    cap = G.BUDGET_CAPS.get(structure)
    if cap is not None and spots:
        spot = spots.get(symbol)
        if spot is not None and spot > cap:
            row["verdict"] = G.VERDICT_SKIP
            row["reason"] = f"spot ${spot:.2f} > ${cap:.0f} budget cap ({structure})"
            return row

    # 0.6 ZEBRA persistence-trend gate. Suspend a delta-1 stock-replacement
    # candidate when it has been entrenched below the 200-DMA. Fails open
    # (no skip) when ORATS history is missing — the v1 cohort uses the
    # research_cohort_v15 parquet which may not have all symbols.
    if structure.startswith("zebra"):
        trend = zebra_trend_status(symbol)
        if trend is not None and trend["sustained_downtrend"]:
            row["verdict"] = G.VERDICT_SKIP
            row["reason"] = (
                f"sustained downtrend: {trend['days_below']}/{trend['lookback']} "
                f"days below 200-DMA (threshold: {G.ZEBRA_TREND_BELOW_200DMA_THRESHOLD})"
            )
            return row

    # 1. Hard-pause check (bull_put only)
    if structure.startswith("bull_put") and not structure.endswith("_earnings"):
        if regime.get("hard_pause_active"):
            row["verdict"] = G.VERDICT_PAUSE
            row["reason"] = "hard pause active (SPY<200dma + term_inv + IVR>0.5)"
            return row

    # 2. Regime gate per structure
    gate_ok = True
    gate_reason = ""
    if structure in ("bull_put", "bull_put_mp"):
        if not regime.get("bull_put_signal_active"):
            gate_ok = False
            gate_reason = f"{structure} gate off (need contango + VRP>0)"
    elif structure == "bear_call":
        if not regime.get("h1_active"):
            gate_ok = False
            gate_reason = "bear_call H1 gate off (need SPY<200dma + IVR>0.5)"
    elif structure == "anti_zebra":
        if not regime.get("h1_active"):
            gate_ok = False
            gate_reason = "anti_zebra H1 gate off (need SPY<200dma + IVR>0.5)"
    elif structure in ("inverted_fly_pair", "inverted_fly_single"):
        if symbol in G.IF_NO_GATE_NAMES:
            pass  # GOOGL etc. skip the gate
        elif not regime.get("if_gate_active"):
            gate_ok = False
            gate_reason = "IF term-inv gate off (need term_inverted)"
    # ZEBRA: no regime gate
    # covered_call: no regime gate — range-bound thesis is structural
    # (floating-rate senior loans / high-yield credit ETFs), not regime-driven

    if not gate_ok:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = gate_reason
        return row

    # 2.5 Earnings-in-holding-window gate. Plan v1.7: "No binary earnings
    # inside the DTE window — earnings moves are bimodal and break the carry
    # thesis." Earnings-bias structures bypass: their holding window
    # intentionally straddles the earnings event.
    #
    # ZEBRA bypass (added 2026-05-03): ZEBRA is structurally defined-risk
    # (max loss = debit at the lower long strike) and behaves like delta-1
    # stock-replacement. Earnings risk on ZEBRA is no worse than holding the
    # underlying through earnings. Auto-skip is too conservative — instead
    # the daily alert fires an earnings-lead warning N days before each
    # event so the user can decide hold/close per position.
    is_earnings_exempt = (
        structure.endswith("_earnings")
        or structure.startswith("zebra")
    )
    if not is_earnings_exempt and earnings_dates:
        in_window = [ed for ed in earnings_dates if target <= ed <= opex]
        if in_window:
            row["verdict"] = G.VERDICT_SKIP
            row["reason"] = (
                f"binary earnings {in_window[0]} inside holding window "
                f"[{target}, {opex}]"
            )
            return row

    # 3. Window proximity (target is today or future at this point)
    if days_until > G.ENTRY_WINDOW_TOLERANCE:
        row["verdict"] = G.VERDICT_PENDING
        row["reason"] = (
            f"entry window in {days_until} trading days "
            f"(target {target}, OpEx {opex})"
        )
        return row

    # 4. GO — but check soft-downsize for sizing
    if regime.get("soft_downsize_active") and structure.startswith("bull_put"):
        row["verdict"] = G.VERDICT_DOWNSIZE
        row["size"] = G.SIZE_DOWNSIZE
        row["reason"] = "GO at half size (soft-downsize trigger active)"
    elif structure in G.PAPER_SIZED_STRUCTURES:
        row["verdict"] = G.VERDICT_GO
        row["size"] = G.PAPER_SIZE_FACTOR
        row["reason"] = (f"entry day for {window_label} (OpEx {opex}) — "
                         f"paper-test sized at {G.PAPER_SIZE_FACTOR}x")
    else:
        row["verdict"] = G.VERDICT_GO
        row["size"] = G.SIZE_DEFAULT
        row["reason"] = f"entry day for {window_label} (OpEx {opex})"

    # 5. Bull_put MA-bucket downsize gate (added 2026-05-05).
    # Per project_bullput_below_ma_findings.md: bull_put on names trading more
    # than 10% BELOW their 200-DMA at entry has worse expectancy in the
    # OTM/ATM sub-cells (-$0.045 and -$0.034/cycle respectively at slip=0.50).
    # Don't SKIP — the held-to-expiry ITM cell is positive in this bucket
    # (+$0.037/cycle). Half-size to mark the regime risk; the position-health
    # monitor flags 🔴 separately so the human reviewer can override.
    if (row["verdict"] in (G.VERDICT_GO, G.VERDICT_DOWNSIZE)
            and structure.startswith("bull_put")):
        ma_pct = bull_put_ma_pct(symbol)
        if (ma_pct is not None
                and ma_pct < G.BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD):
            ma_note = (f"spot {ma_pct*100:+.1f}% vs 200-DMA "
                        f"(below {G.BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD*100:.0f}% "
                        "MA threshold — regime-conditional adverse)")
            if row["verdict"] == G.VERDICT_GO:
                row["verdict"] = G.VERDICT_DOWNSIZE
                row["size"] = G.SIZE_DOWNSIZE
                row["reason"] = f"DOWNSIZE: {ma_note} [orig: {row['reason']}]"
            else:
                # Already DOWNSIZE (soft-downsize); keep size, annotate
                row["reason"] = f"{row['reason']}; also {ma_note}"

    # 6. ZEBRA breadth-ring 🔴 tail-downsize gate (SEALED pre-reg
    # docs/BREADTH_RING_ZEBRA_SIZING_PREREG.md). 🔴 (narrowing+extended) zebra
    # entries carry a fatter left tail at ~break-even mean; half-size sheds tail
    # at ~zero cost (§5: CVaR-10 −10.7% for +0.07% total P&L). OFF during the
    # paper window (G.ZEBRA_BREADTH_HALFSIZE_ENABLED=False) — anti-censoring
    # tag-don't-downsize; the alert card already annotates 🔴 zebra entries.
    # The flag short-circuits first, so this is fully inert until promotion.
    if (G.ZEBRA_BREADTH_HALFSIZE_ENABLED
            and row["verdict"] in (G.VERDICT_GO, G.VERDICT_DOWNSIZE)
            and structure.startswith("zebra")
            and _breadth_red_today()):
        b_note = "breadth 🔴 (narrowing+extended) — tail-risk half-size [zebra sizing pre-reg]"
        if row["verdict"] == G.VERDICT_GO:
            row["verdict"] = G.VERDICT_DOWNSIZE
            row["size"] = G.SIZE_DOWNSIZE
            row["reason"] = f"DOWNSIZE: {b_note} [orig: {row['reason']}]"
        else:
            row["reason"] = f"{row['reason']}; also {b_note}"
    return row


def load_cohort_earnings(run_date: date) -> tuple[dict[str, list[date]], set[str]]:
    """Earnings calendar for the union of every OpEx + earnings cohort.

    Returns ({symbol: sorted upcoming earnings dates}, failed) where `failed`
    is the set of symbols whose fetch FAILED — earnings status UNKNOWN, not
    "no earnings". Used by the earnings-in-holding-window gate in
    evaluate_opex_cell; main() annotates actionable verdicts on failed names
    so a yfinance outage can never silently disable the gate (go-live audit
    C5). ETFs return verified-empty (sentinel rows in the cache) — that is
    the correct state: ETFs have no binary earnings event.
    """
    all_syms = sorted(set(
        G.COHORT_BULL_PUT
        + G.COHORT_BEAR_CALL
        + G.COHORT_INVERTED_FLY_PAIR
        + G.COHORT_INVERTED_FLY_SINGLE
        + G.COHORT_ZEBRA_TIER1
        + G.COHORT_ZEBRA_TIER2
        + G.COHORT_EARNINGS_BULL_PUT
        + G.COHORT_EARNINGS_BEAR_CALL
        + G.COHORT_EARNINGS_INVERTED_FLY
    ))
    # 180-day horizon covers the longest window: ZEBRA 75-DTE entry against
    # the 5th-out OpEx (~150d total).
    cal, failed = upcoming_earnings_with_status(all_syms, run_date, window_days=180)
    out: dict[str, list[date]] = {}
    if cal.empty:
        return out, failed
    for sym, grp in cal.groupby("ticker"):
        out[sym] = sorted(grp["earnings_date"].tolist())
    return out, failed


def build_opex_verdicts(regime: dict, windows: dict, run_date: date,
                         earnings_by_sym: dict[str, list[date]],
                         spots: dict[str, float] | None = None) -> list[dict]:
    """For each OpEx-anchored structure, generate one row per (symbol, window)."""
    if regime is None:
        return []
    rows = []

    def ed(sym: str) -> list[date] | None:
        return earnings_by_sym.get(sym)

    # Bull put — both Window A (45-DTE managed) and Window B (T-5)
    if "bull_put_45dte" in windows:
        target, opex, days_until = windows["bull_put_45dte"]
        for sym in G.COHORT_BULL_PUT:
            rows.append(evaluate_opex_cell(
                sym, "bull_put", "45-DTE-managed (Window A)",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))
    if "bull_put_t5" in windows:
        target, opex, days_until = windows["bull_put_t5"]
        for sym in G.COHORT_BULL_PUT_T5_PAPER:
            rows.append(evaluate_opex_cell(
                sym, "bull_put_mp", "T-5 (Window B / MP-anchored)",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))

    # Bear call — 45-DTE only
    if "bear_call_45dte" in windows:
        target, opex, days_until = windows["bear_call_45dte"]
        for sym in G.COHORT_BEAR_CALL:
            if sym == "SPX" and G.SPX_EXCLUDED_FROM_QUALIFIER:
                continue
            rows.append(evaluate_opex_cell(
                sym, "bear_call", "45-DTE",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))

    # Inverted fly — pair + singles, 45-DTE
    if "inverted_fly_45dte" in windows:
        target, opex, days_until = windows["inverted_fly_45dte"]
        for sym in G.COHORT_INVERTED_FLY_PAIR:
            if sym == "SPX" and G.SPX_EXCLUDED_FROM_QUALIFIER:
                continue
            rows.append(evaluate_opex_cell(
                sym, "inverted_fly_pair", "45-DTE",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))
        for sym in G.COHORT_INVERTED_FLY_SINGLE:
            rows.append(evaluate_opex_cell(
                sym, "inverted_fly_single", "45-DTE",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))

    # ZEBRA — Tier 1 + Tier 2, 75-DTE
    if "zebra_75dte" in windows:
        target, opex, days_until = windows["zebra_75dte"]
        for sym in G.COHORT_ZEBRA_TIER1:
            rows.append(evaluate_opex_cell(
                sym, "zebra_tier1", "75-DTE",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))
        for sym in G.COHORT_ZEBRA_TIER2:
            rows.append(evaluate_opex_cell(
                sym, "zebra_tier2", "75-DTE",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))
        # Anti-ZEBRA (bearish synthetic-short, H1-gated). Shares the 75-DTE
        # window; H1 gate in evaluate_opex_cell filters when SPY isn't in
        # the bear regime. Promoted 2026-05-17 per ANTI_ZEBRA_PREREG.md.
        for sym in G.COHORT_ANTI_ZEBRA_TIER1:
            rows.append(evaluate_opex_cell(
                sym, "anti_zebra", "75-DTE (H1-gated)",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))

    # Covered call — credit ETFs (BKLN/JNK/HYG), monthly cycle
    # Entry = trading day after prior monthly OpEx; expiry = next monthly OpEx
    # No regime gate; earnings gate naturally inert (ETFs don't report earnings)
    if "covered_call_monthly" in windows:
        target, opex, days_until = windows["covered_call_monthly"]
        for sym in G.COHORT_COVERED_CALL:
            rows.append(evaluate_opex_cell(
                sym, "covered_call", "monthly (post-OpEx entry)",
                target, opex, days_until, regime, run_date, ed(sym), spots,
            ))

    return rows


def evaluate_earnings_cell(symbol: str, structure: str, earnings_date: date,
                            run_date: date, days_before: int,
                            spots: dict[str, float] | None = None) -> dict:
    """Earnings-track verdict: GO if today is exactly T-N before earnings,
    PENDING if upcoming within tolerance window, SKIP otherwise.

    Earnings track is calendar-driven (no regime gate per plan v1.7).
    """
    target = trading_day_offset(earnings_date, -days_before)
    # Calendar comparison for past/future (trading_days_between sign is
    # unreliable across weekends).
    if target < run_date:
        days_until = -1  # sentinel: past
    elif target == run_date:
        days_until = 0
    else:
        days_until = trading_days_between(run_date, target)

    row = {
        "symbol": symbol, "structure": structure,
        "window": f"earnings T-{days_before} ({earnings_date})",
        "target": str(target), "opex": "(earnings-anchored)",
        "days_until": days_until, "verdict": None, "size": 0.0, "reason": "",
    }

    # Budget cap (capital-outlay structures only — inverted_fly_earnings).
    cap = G.BUDGET_CAPS.get(structure)
    if cap is not None and spots:
        spot = spots.get(symbol)
        if spot is not None and spot > cap:
            row["verdict"] = G.VERDICT_SKIP
            row["reason"] = f"spot ${spot:.2f} > ${cap:.0f} budget cap ({structure})"
            return row

    if target < run_date:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = f"target {target} already past"
    elif days_until > 5:
        row["verdict"] = G.VERDICT_SKIP
        row["reason"] = f"earnings {earnings_date} too far ({days_until} td)"
    elif days_until > G.ENTRY_WINDOW_TOLERANCE:
        row["verdict"] = G.VERDICT_PENDING
        row["reason"] = (
            f"T-{days_before} entry day for {symbol} earnings on {earnings_date} "
            f"in {days_until} trading days"
        )
    else:
        row["verdict"] = G.VERDICT_GO
        row["size"] = G.SIZE_DEFAULT
        row["reason"] = (
            f"T-{days_before} entry day for {symbol} earnings on {earnings_date}"
        )
    return row


def build_earnings_verdicts(run_date: date,
                             spots: dict[str, float] | None = None) -> list[dict]:
    """Look up upcoming earnings for the three earnings cohorts and emit verdicts."""
    all_earnings_names = sorted(set(
        G.COHORT_EARNINGS_BULL_PUT
        + G.COHORT_EARNINGS_BEAR_CALL
        + G.COHORT_EARNINGS_INVERTED_FLY
    ))
    cal = upcoming_earnings(all_earnings_names, run_date, window_days=30)
    if cal.empty:
        return []

    rows = []
    for _, ev in cal.iterrows():
        sym = ev["ticker"]
        ed = ev["earnings_date"]

        # Bull put earnings cohort
        if sym in G.COHORT_EARNINGS_BULL_PUT:
            days_before = (G.WINDOW_EARNINGS_T1
                           if sym in G.EARNINGS_T1_NAMES
                           else G.WINDOW_EARNINGS_T3)
            rows.append(evaluate_earnings_cell(
                sym, "bull_put_earnings", ed, run_date, days_before, spots
            ))

        # INTC bear_call earnings (T-1)
        if sym in G.COHORT_EARNINGS_BEAR_CALL:
            rows.append(evaluate_earnings_cell(
                sym, "bear_call_earnings", ed, run_date, G.WINDOW_EARNINGS_T1, spots
            ))

        # PLTR inverted_fly earnings (T-3)
        if sym in G.COHORT_EARNINGS_INVERTED_FLY:
            rows.append(evaluate_earnings_cell(
                sym, "inverted_fly_earnings", ed, run_date, G.WINDOW_EARNINGS_T3, spots
            ))

    return rows


# ─── Output formatting ────────────────────────────────────────────────

def format_regime_block(regime: dict, run_date: date) -> str:
    if regime is None:
        return f"  (no regime_state available for {run_date})"
    stage = regime.get("stage", "?")
    stage_label = {0: "calm/bull", 1: "soft-downsize", 2: "SPY<200dma",
                   3: "H1 (bear regime)"}.get(stage, f"stage {stage}")
    spy = regime.get("spy_close")
    ma = regime.get("spy_ma200")
    pct = regime.get("spy_pct_to_ma200")
    ivr = regime.get("spy_ivr_252")
    term = regime.get("spy_term_spread")
    vrp = regime.get("spy_vrp")
    h1 = bool(regime.get("h1_active"))
    pause = bool(regime.get("hard_pause_active"))
    soft = bool(regime.get("soft_downsize_active"))
    if_gate = bool(regime.get("if_gate_active"))
    bp_signal = bool(regime.get("bull_put_signal_active"))

    lines = [
        f"  Run date:          {run_date}",
        f"  Regime stage:      {stage} ({stage_label})",
        f"  SPY close (as of {regime.get('as_of_close')}):  ${spy:.2f}  "
        f"({'+' if pct is not None and pct >= 0 else ''}{pct*100 if pct is not None else 0:.1f}% vs 200dma ${ma:.2f})",
        f"  IVR (252-day):     {ivr:.3f}  ({'HIGH' if regime.get('ivr_high') else 'low'})",
        f"  Term spread:       {term:+.4f}  ({'INVERTED' if regime.get('term_inverted') else 'contango'})",
        f"  VRP:               {vrp:+.4f}",
        "",
        f"  H1 active:                 {'ON' if h1 else 'off'}",
        f"  Hard pause active:         {'ON' if pause else 'off'}",
        f"  Soft-downsize active:      {'ON' if soft else 'off'}",
        f"  IF term-inv gate active:   {'ON' if if_gate else 'off'}",
        f"  Bull-put signal active:    {'ON' if bp_signal else 'off'}",
    ]
    return "\n".join(lines)


def format_entry_windows(windows: dict, run_date: date) -> str:
    if not windows:
        return "  (no upcoming windows in next 3 OpEx cycles)"
    rows = []
    for label, (target, opex, days_until) in sorted(windows.items(), key=lambda kv: kv[1][2]):
        if days_until == 0:
            tag = " ← TODAY"
        elif days_until <= G.ENTRY_WINDOW_TOLERANCE:
            tag = f" ← {days_until} trading day{'s' if days_until > 1 else ''} (within tolerance)"
        else:
            tag = ""
        rows.append(
            f"  {label:<22}  target {target}  (OpEx {opex})  "
            f"{days_until} trading days away{tag}"
        )
    return "\n".join(rows)


def format_verdicts(verdict_rows: list[dict]) -> str:
    """Per-structure summary + the actionable cells (GO + DOWNSIZE only)."""
    if not verdict_rows:
        return "  (no opex-window verdicts to report)"

    df = pd.DataFrame(verdict_rows)

    # Per-structure summary table. CAPPED = SKIP_CONCENTRATION (sector cap) —
    # counted separately from SKIP so capped names can't silently vanish from
    # the human-readable report (go-live audit F3).
    summary_lines = ["  Per-structure summary:"]
    summary_lines.append(f"    {'structure':<22} {'window':<25} {'GO':>4} "
                         f"{'DOWN':>5} {'PEND':>5} {'SKIP':>5} {'CAPPED':>7} {'PAUSE':>6}")
    for (structure, window), grp in df.groupby(["structure", "window"]):
        counts = grp["verdict"].value_counts()
        summary_lines.append(
            f"    {structure:<22} {window:<25} "
            f"{counts.get(G.VERDICT_GO, 0):>4} "
            f"{counts.get(G.VERDICT_DOWNSIZE, 0):>5} "
            f"{counts.get(G.VERDICT_PENDING, 0):>5} "
            f"{counts.get(G.VERDICT_SKIP, 0):>5} "
            f"{counts.get(G.VERDICT_SKIP_CONCENTRATION, 0):>7} "
            f"{counts.get(G.VERDICT_PAUSE, 0):>6}"
        )

    # Actionable rows: GO + DOWNSIZE
    actionable = df[df["verdict"].isin([G.VERDICT_GO, G.VERDICT_DOWNSIZE])]
    pause_rows = df[df["verdict"] == G.VERDICT_PAUSE]
    capped_rows = df[df["verdict"] == G.VERDICT_SKIP_CONCENTRATION]

    out_lines = list(summary_lines)
    if not actionable.empty:
        out_lines.append("")
        out_lines.append("  Actionable today (GO/DOWNSIZE):")
        for _, r in actionable.iterrows():
            out_lines.append(
                f"    {r['symbol']:>6} | {r['structure']:<22} | "
                f"{r['window']:<25} | {r['verdict']:<8} | size={r['size']:.2f} "
                f"| {r['reason']}"
            )
    elif not pause_rows.empty:
        out_lines.append("")
        out_lines.append("  PAUSE active for some structures (no GO today).")
    else:
        out_lines.append("")
        out_lines.append("  No GO verdicts today (all PENDING or SKIP).")

    # Names cut by the sector-concentration cap — qualified on every gate but
    # dropped by ranking. Always listed so the cut is visible, never silent.
    if not capped_rows.empty:
        out_lines.append("")
        out_lines.append("  Capped out today (qualified, cut by concentration ranking):")
        for _, r in capped_rows.iterrows():
            out_lines.append(
                f"    {r['symbol']:>6} | {r['structure']:<22} | {r['reason']}"
            )

    return "\n".join(out_lines)


# ─── Sector-concentration cap ─────────────────────────────────────────

def _ev_rank_bucket(bucket: list[dict], verdict_rank: dict,
                    cache: dict | None) -> tuple[list[dict], dict | None]:
    """Rank an OVER-cap bucket by (verdict tier, EV reward/risk, alphabetical).

    Replaces the old alphabetical-only tiebreak (spec step C). The EV score is a
    cross-structure-comparable within-kind percentile (see lib.trade_ev.
    annotate_bucket_ev) so a zebra's larger raw ev_per_risk can't outrank a vertical
    purely on units. GO still beats DOWNSIZE first; alphabetical is the final
    deterministic tiebreak.

    Fail-open to the prior alphabetical order if the EV scorer can't be imported,
    the whole bucket raises, or every candidate fails to score (thin 9:25 chains /
    Schwab outage) — the qualifier never fails closed on a pricing problem. Returns
    (ranked_rows, coverage_dict_or_None).
    """
    alpha_key = lambda r: (verdict_rank.get(r["verdict"], 99), r["symbol"])
    try:
        from lib.trade_ev import annotate_bucket_ev
    except Exception as e:  # heavy import (schwab/construction) — degrade gracefully
        log.warning("EV scorer unavailable; cap tiebreak → alphabetical: %s", e)
        return sorted(bucket, key=alpha_key), None
    try:
        cov = annotate_bucket_ev(bucket, cache=cache)
    except Exception as e:
        log.warning("EV scoring raised for bucket; cap tiebreak → alphabetical: %s", e)
        return sorted(bucket, key=alpha_key), None
    if not cov.get("scored"):
        # nobody scored (all chains thin/failed) → identical to old behavior
        return sorted(bucket, key=alpha_key), cov
    ranked = sorted(
        bucket,
        key=lambda r: (
            verdict_rank.get(r["verdict"], 99),
            0 if r.get("_ev_norm") is not None else 1,   # usable EV before unknown
            -(r.get("_ev_norm") or 0.0),                 # best reward/risk kept
            r["symbol"],                                  # final deterministic tiebreak
        ),
    )
    return ranked, cov


def _ev_note(r: dict) -> str:
    """Short audit suffix recording the EV/risk used for the tiebreak (or fallback)."""
    epr = r.get("_ev_epr")
    if epr is not None:
        return f"EV/risk={epr:+.3f}"
    ev = r.get("_ev")
    why = (getattr(ev, "gate_note", "") or getattr(ev, "error", "")) if ev else ""
    return f"EV n/a ({why})" if why else "EV n/a"


def apply_sector_concentration_cap(rows: list[dict], ev_cache: dict | None = None) -> list[dict]:
    """Enforce max-N-single-names-per-GICS-sector-per-OpEx on GO/DOWNSIZE rows.

    Triggered by the WFC + JPM same-cycle stop pattern on 2026-05-12.
    Operates only on OpEx-anchored rows (earnings rows are not capped —
    they're a different time bucket). ETFs and unmapped symbols are exempt.

    Ranking within an over-concentrated sector:
      1. Verdict tier: GO ranks above DOWNSIZE (qualifier-decided confidence)
      2. EV reward/risk (best kept) — cross-structure-comparable within-kind
         percentile from lib.trade_ev (spec step C); fails open to alphabetical
      3. Alphabetical (deterministic final tiebreak)

    Lower-ranked candidates get verdict downgraded to SKIP_CONCENTRATION with
    sector_rank_position annotation (e.g. "3 of 4 in financials"). All rows
    receive a `sector` field for audit.

    Paired with apply_macro_concentration_cap, which MUST run after this one:
    this cap owns within-industry clusters (hard SKIP); the macro cap owns the
    cross-sector macro-factor residual (soft DOWNSIZE). They guard different
    correlations — see the "Division of labor" note in gate_config.

    Returns the list of rows (mutated in place; same order).
    """
    actionable_set = (G.VERDICT_GO, G.VERDICT_DOWNSIZE)
    verdict_rank = {G.VERDICT_GO: 0, G.VERDICT_DOWNSIZE: 1}

    # Stamp sector on every row for downstream audit. Earnings rows show
    # opex="(earnings-anchored)" and are excluded from cap grouping below.
    for r in rows:
        r["sector"] = get_sector(r.get("symbol", ""))
        r.setdefault("sector_rank_position", None)

    # Group only OpEx-anchored, actionable, non-exempt rows
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("verdict") not in actionable_set:
            continue
        opex = r.get("opex")
        if not opex or "earnings" in str(opex):
            continue
        if is_cap_exempt(r.get("symbol", "")):
            continue
        key = (opex, r["sector"])
        groups.setdefault(key, []).append(r)

    if ev_cache is None:
        ev_cache = {}
    n_capped = 0
    for (opex, sector), bucket in groups.items():
        if len(bucket) <= G.SECTOR_CAP_MAX_PER_OPEX:
            continue
        # Rank: GO before DOWNSIZE; then best EV reward/risk; alphabetical last.
        ranked, cov = _ev_rank_bucket(bucket, verdict_rank, ev_cache)
        if cov:
            log.info("Sector cap EV-tiebreak (%s/%s): %d/%d candidates scored",
                     sector, opex, cov["scored"], cov["n"])
        total = len(ranked)
        for idx, r in enumerate(ranked, start=1):
            r["sector_rank_position"] = f"{idx} of {total} in {sector}"
            if idx > G.SECTOR_CAP_MAX_PER_OPEX:
                original_verdict = r["verdict"]
                r["verdict"] = G.VERDICT_SKIP_CONCENTRATION
                r["size"] = 0.0
                cap_note = (
                    f"sector cap fired ({sector}, {idx}/{total} rank, {_ev_note(r)}); "
                    f"original verdict={original_verdict}"
                )
                r["reason"] = (
                    (r.get("reason") + " | " + cap_note) if r.get("reason")
                    else cap_note
                )
                n_capped += 1

    if n_capped:
        log.info("Sector-concentration cap downgraded %d row(s) to SKIP_CONCENTRATION",
                 n_capped)
    return rows


# ─── Macro-concentration cap ──────────────────────────────────────────

def apply_macro_concentration_cap(rows: list[dict], ev_cache: dict | None = None) -> list[dict]:
    """Soft-downsize names that over-concentrate a single macro regime bucket.

    Orthogonal to the GICS sector cap: two names in different sectors can be
    the same macro bet (both load PC1+ = pure reflation). The regime_primary
    bucket from the macro-sensitivity profile (lib.macro_profile) catches that.

    SOFT cap (G.MACRO_CAP_MAX_PER_OPEX): for each (opex, regime_primary) group
    of actionable OpEx-anchored rows, the top-ranked G.MACRO_CAP_MAX_PER_OPEX
    keep their verdict; the rest are DOWNSIZED (GO → DOWNSIZE at G.SIZE_DOWNSIZE;
    already-DOWNSIZE rows keep their size and are annotated). Nothing is skipped
    — macro is a risk descriptor, not a selection edge, and the buckets are
    coarse. Ranking: verdict tier (GO > DOWNSIZE), then EV reward/risk (best kept,
    cross-structure-comparable within-kind percentile; spec step C; fails open to
    alphabetical), then alphabetical.

    NEUTRAL / NA buckets are never capped (not a concentrated bet). Earnings-
    anchored rows are excluded (different time bucket). Runs AFTER the sector
    cap so it only sees still-actionable rows.

    Fails open: if the macro profile can't be read, or a symbol has no
    regime_primary (e.g. a name promoted tonight before the macro refresh),
    that row is left untouched. Stamps `regime_primary` and
    `macro_rank_position` on every actionable row for audit.

    DIVISION OF LABOR with the sector cap (see gate_config.MACRO_CAP_MAX_PER_OPEX):
    the two caps guard DIFFERENT correlations — GICS = shared industry risk,
    regime_primary = shared macro-factor risk — and neither subsumes the other.
    This cap MUST run after apply_sector_concentration_cap. Given the invariant
    SECTOR_CAP_MAX_PER_OPEX < MACRO_CAP_MAX_PER_OPEX, no single GICS sector can
    contribute more than the sector cap allows to the actionable set, so this cap
    fires ONLY on cross-sector concentration — it never re-penalizes a
    within-sector cluster the sector cap already thinned.

    Returns the list of rows (mutated in place; same order).
    """
    actionable_set = (G.VERDICT_GO, G.VERDICT_DOWNSIZE)
    verdict_rank = {G.VERDICT_GO: 0, G.VERDICT_DOWNSIZE: 1}

    # Guard the load-bearing invariant. If the sector cap is ever loosened to
    # ≥ the macro cap, the "cross-sector only" guarantee above breaks and the
    # two caps begin double-pruning pure-macro sectors (Energy/Financials/
    # Materials). Warn loudly rather than silently double-penalize.
    if G.SECTOR_CAP_MAX_PER_OPEX >= G.MACRO_CAP_MAX_PER_OPEX:
        log.warning(
            "Cap invariant violated: SECTOR_CAP_MAX_PER_OPEX (%d) >= "
            "MACRO_CAP_MAX_PER_OPEX (%d). The macro cap may now double-prune "
            "within-sector clusters the sector cap already cut. See "
            "gate_config 'Division of labor' note.",
            G.SECTOR_CAP_MAX_PER_OPEX, G.MACRO_CAP_MAX_PER_OPEX,
        )

    try:
        profile = macro_lib.load_profile()
    except Exception as e:
        log.warning("Macro profile unavailable; macro-concentration cap skipped: %s", e)
        return rows
    if "regime_primary" not in profile.columns:
        log.warning("Macro profile has no regime_primary column; cap skipped")
        return rows
    primary_by_sym = dict(zip(profile["ticker"], profile["regime_primary"]))

    # Stamp regime_primary on every actionable row for audit; group the
    # cappable ones (OpEx-anchored, real bucket).
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("verdict") not in actionable_set:
            continue
        rp = primary_by_sym.get(r.get("symbol", ""))
        r["regime_primary"] = rp
        r.setdefault("macro_rank_position", None)
        if rp in (None, "NEUTRAL", "NA"):
            continue
        opex = r.get("opex")
        if not opex or "earnings" in str(opex):
            continue
        groups.setdefault((opex, rp), []).append(r)

    if ev_cache is None:
        ev_cache = {}
    n_capped = 0
    for (opex, rp), bucket in groups.items():
        if len(bucket) <= G.MACRO_CAP_MAX_PER_OPEX:
            continue
        ranked, cov = _ev_rank_bucket(bucket, verdict_rank, ev_cache)
        if cov:
            log.info("Macro cap EV-tiebreak (%s/%s): %d/%d candidates scored",
                     rp, opex, cov["scored"], cov["n"])
        total = len(ranked)
        for idx, r in enumerate(ranked, start=1):
            r["macro_rank_position"] = f"{idx} of {total} in {rp}"
            if idx <= G.MACRO_CAP_MAX_PER_OPEX:
                continue
            cap_note = (
                f"macro cap fired ({rp}, {idx}/{total} rank, {_ev_note(r)}); "
                f"downsized for regime-bucket concentration"
            )
            if r["verdict"] == G.VERDICT_GO:
                r["verdict"] = G.VERDICT_DOWNSIZE
                r["size"] = G.SIZE_DOWNSIZE
            # already-DOWNSIZE rows keep their (already-reduced) size
            r["reason"] = (
                (r.get("reason") + " | " + cap_note) if r.get("reason")
                else cap_note
            )
            n_capped += 1

    if n_capped:
        log.info("Macro-concentration cap downsized %d row(s) for regime-bucket concentration",
                 n_capped)
    return rows


# ─── Persistence ──────────────────────────────────────────────────────

QUALIFIER_COLUMNS = [
    "run_date", "symbol", "structure", "window",
    "target", "opex", "days_until",
    "verdict", "size", "reason",
    "regime_stage", "regime_h1", "regime_if_gate", "regime_bp_signal",
    "sector", "sector_rank_position",
    "regime_primary", "macro_rank_position",
]


def write_qualifier_runs(rows: list[dict], regime: dict, run_date: date,
                         dry_run: bool = False) -> int:
    """Persist verdict rows to cycle_qualifier_runs in maxpain.db."""
    if not rows or dry_run:
        return 0
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cycle_qualifier_runs (
            run_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            structure TEXT NOT NULL,
            window TEXT,
            target TEXT,
            opex TEXT,
            days_until INTEGER,
            verdict TEXT,
            size REAL,
            reason TEXT,
            regime_stage INTEGER,
            regime_h1 INTEGER,
            regime_if_gate INTEGER,
            regime_bp_signal INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (run_date, symbol, structure, window)
        )
    """)
    # Idempotent column adds for the sector-concentration cap (2026-05-15)
    # and the macro-concentration cap (2026-06-04).
    existing_cols = {r[1] for r in cur.execute("PRAGMA table_info(cycle_qualifier_runs)").fetchall()}
    for col_name, col_type in [("sector", "TEXT"), ("sector_rank_position", "TEXT"),
                               ("regime_primary", "TEXT"), ("macro_rank_position", "TEXT")]:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE cycle_qualifier_runs ADD COLUMN {col_name} {col_type}")
    placeholders = ", ".join(["?"] * len(QUALIFIER_COLUMNS))
    col_list = ", ".join(QUALIFIER_COLUMNS)
    rs = regime or {}
    for r in rows:
        full_row = {
            **r,
            "run_date": str(run_date),
            "regime_stage": rs.get("stage"),
            "regime_h1": int(rs.get("h1_active", 0)) if rs else None,
            "regime_if_gate": int(rs.get("if_gate_active", 0)) if rs else None,
            "regime_bp_signal": int(rs.get("bull_put_signal_active", 0)) if rs else None,
        }
        cur.execute(
            f"INSERT OR REPLACE INTO cycle_qualifier_runs ({col_list}) "
            f"VALUES ({placeholders})",
            [full_row.get(c) for c in QUALIFIER_COLUMNS],
        )
    conn.commit()
    conn.close()
    return len(rows)


def write_parquet_artifact(rows: list[dict], regime: dict, run_date: date) -> Path:
    """Write parquet snapshot of this run for later inspection / dashboard use."""
    QUALIFIER_DIR.mkdir(parents=True, exist_ok=True)
    out = QUALIFIER_DIR / f"qualifier_{run_date}.parquet"
    if not rows:
        return out
    # Strip internal "_"-prefixed temp keys (e.g. the _ev EVScore object stamped
    # by annotate_bucket_ev when a concentration cap fires) — raw objects are not
    # parquet-serializable and would crash the run with pyarrow ArrowInvalid.
    df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in rows])
    df["run_date"] = str(run_date)
    if regime:
        df["regime_stage"] = regime.get("stage")
        df["regime_h1"] = int(regime.get("h1_active", 0))
        df["regime_if_gate"] = int(regime.get("if_gate_active", 0))
        df["regime_bp_signal"] = int(regime.get("bull_put_signal_active", 0))
    df.to_parquet(out, index=False)
    return out


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", default=None,
                        help="Override run date (YYYY-MM-DD). Default: today.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print only; skip DB write and parquet artifact")
    args = parser.parse_args()

    run_date = (
        datetime.strptime(args.run_date, "%Y-%m-%d").date()
        if args.run_date else date.today()
    )

    regime, regime_warning = load_regime_state(run_date)
    windows = compute_window_targets(run_date)
    earnings_by_sym, earnings_failed = load_cohort_earnings(run_date)

    # Live spots for the budget-cap gate (ZEBRA + IF only). Single bulk call.
    budget_gated_syms = sorted(set(
        G.COHORT_ZEBRA_TIER1
        + G.COHORT_ZEBRA_TIER2
        + G.COHORT_INVERTED_FLY_PAIR
        + G.COHORT_INVERTED_FLY_SINGLE
        + G.COHORT_EARNINGS_INVERTED_FLY
    ))
    spots = fetch_schwab_spots(budget_gated_syms)

    opex_rows = (
        build_opex_verdicts(regime, windows, run_date, earnings_by_sym, spots)
        if regime else []
    )
    earnings_rows = build_earnings_verdicts(run_date, spots)
    verdict_rows = opex_rows + earnings_rows

    # Shared per-run chain cache for the EV-rank tiebreak inside both caps, so a
    # chain fetched for an over-cap sector bucket is reused by the macro cap.
    ev_cache: dict = {}

    # Sector-concentration cap (max 2 single names per GICS sector per OpEx).
    # Runs after all per-structure verdicts so it sees the full candidate set.
    verdict_rows = apply_sector_concentration_cap(verdict_rows, ev_cache=ev_cache)

    # Macro-concentration cap (soft-downsize beyond G.MACRO_CAP_MAX_PER_OPEX
    # names per regime_primary bucket per OpEx). Orthogonal to the sector cap;
    # runs after it so it only re-sizes still-actionable rows.
    verdict_rows = apply_macro_concentration_cap(verdict_rows, ev_cache=ev_cache)

    # ── Fail-open visibility (go-live audit C5/F3) ────────────────────────
    # The earnings gate and the budget-cap gate silently disable themselves
    # when their input feed fails (yfinance / Schwab). That must never be
    # invisible: annotate every actionable verdict whose gate went unchecked
    # (the annotation persists to the DB reason and rides into the daily
    # alert), and collect loud warnings for the report header.
    gate_warnings: list[str] = []
    actionable_verdicts = (G.VERDICT_GO, G.VERDICT_DOWNSIZE)

    if regime_warning:
        gate_warnings.append(f"⚠ REGIME: {regime_warning}")
        # Earnings-track rows (opex = "(earnings-anchored)") are regime-free
        # by design — only OpEx-track verdicts ride on the stale regime row.
        for r in verdict_rows:
            if (r["verdict"] in actionable_verdicts
                    and "earnings-anchored" not in str(r.get("opex", ""))):
                r["reason"] += " ⚠ STALE REGIME (see run warnings)"

    if earnings_failed:
        hit = [r for r in verdict_rows
               if r["symbol"] in earnings_failed
               and r["verdict"] in actionable_verdicts
               and not (r["structure"].endswith("_earnings")
                        or r["structure"].startswith("zebra"))]
        for r in hit:
            r["reason"] += " ⚠ EARNINGS UNVERIFIED (calendar fetch failed)"
        gate_warnings.append(
            f"⚠ EARNINGS: calendar fetch FAILED for {len(earnings_failed)} "
            f"symbol(s) ({', '.join(sorted(earnings_failed))}) — the "
            f"binary-earnings gate was NOT applied for them"
            + (f"; {len(hit)} actionable verdict(s) annotated" if hit else "")
        )

    budget_unchecked = [r for r in verdict_rows
                        if G.BUDGET_CAPS.get(r["structure"]) is not None
                        and r["verdict"] in actionable_verdicts
                        and spots.get(r["symbol"]) is None]
    if budget_unchecked:
        for r in budget_unchecked:
            cap = G.BUDGET_CAPS[r["structure"]]
            r["reason"] += f" ⚠ BUDGET CAP UNCHECKED (no Schwab quote; cap ${cap:.0f})"
        names = ", ".join(sorted({r["symbol"] for r in budget_unchecked}))
        gate_warnings.append(
            f"⚠ BUDGET: no Schwab quote for {names} — the ${'/'.join(f'{c:.0f}' for c in sorted(set(G.BUDGET_CAPS.values())))} "
            f"budget cap was NOT checked on their actionable verdicts"
        )

    print()
    print("=" * 78)
    print(f"  MaxPain Cycle Qualifier — {run_date}")
    print("=" * 78)
    if gate_warnings:
        print()
        print("RUN WARNINGS — gates that could not be fully applied")
        print("-" * 78)
        for w in gate_warnings:
            print(f"  {w}")
    print()
    print("REGIME STATE")
    print("-" * 78)
    print(format_regime_block(regime, run_date))
    print()
    print("UPCOMING ENTRY WINDOWS")
    print("-" * 78)
    print(format_entry_windows(windows, run_date))
    print()
    print("PER-STRUCTURE VERDICTS")
    print("-" * 78)
    print(format_verdicts(verdict_rows))
    print()

    # Persist
    if not args.dry_run and verdict_rows:
        n = write_qualifier_runs(verdict_rows, regime, run_date)
        artifact = write_parquet_artifact(verdict_rows, regime, run_date)
        print(f"  ✓ Persisted {n} rows to cycle_qualifier_runs")
        print(f"  ✓ Parquet artifact: {artifact}")
    elif args.dry_run:
        print("  (dry-run — DB and parquet writes skipped)")

    print("=" * 78)


if __name__ == "__main__":
    main()
