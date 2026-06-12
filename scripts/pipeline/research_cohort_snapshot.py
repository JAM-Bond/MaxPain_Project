#!/usr/bin/env python3.11
"""
MaxPain research cohort daily snapshot
~/MaxPain_Project/scripts/pipeline/research_cohort_snapshot.py

Captures the same summary metrics that the prior 9:15 ET
yfinance snapshot did, but pulled live from Schwab and scoped to the
deployable cohort union from scripts/qualifier/gate_config.py. Writes to
the unified `live_snapshots` table in maxpain.db (since 2026-05-02;
formerly `research_cohort_snapshots`).

Cohort source: union of every COHORT_* list in gate_config.py. Adding a
ticker to any cohort there auto-cascades here on the next run — no
parquet edit needed. The earlier frozen v1.5 parquet
(data/profile/research_cohort_v15.parquet) is kept as historical reference
but no longer read; it was missing post-v1.5 promotions like KRE.

Reuses MaxPain_Project's take_snapshot() (lib/snapshot.py — schema parity
with the legacy daily_snapshots table). SPX is excluded by default —
Schwab equity-chain endpoint does not handle index tickers; use SPY as
the proxy for index signals.

Run:
    python3.11 research_cohort_snapshot.py            # full cohort
    python3.11 research_cohort_snapshot.py --symbol GOOGL META  # subset
    python3.11 research_cohort_snapshot.py --dry-run  # no DB write
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH, connect  # noqa: E402
from lib.snapshot import take_snapshot, current_opex  # noqa: E402
from lib.adjusted_close import load_adjusted_close  # noqa: E402
from scripts.maintenance.orats_health_check import previous_business_day  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"

# SPX-side index requires a different Schwab endpoint; skip in equity-chain capture
SKIP = {"SPX"}

COLUMNS = [
    "symbol", "snapshot_date", "opex_date",
    "current_price", "max_pain", "distance_pct",
    "pin_zone_low", "pin_zone_high", "pin_zone_width",
    "pcr", "total_call_oi", "total_put_oi",
    "expected_move", "atm_iv_pct",
    "net_gamma", "net_gamma_sign", "gamma_flip_strike", "oi_concentration_at_mp",
    "dividend_flag", "ex_div_date", "dte", "data_source",
]


def load_cohort() -> list[str]:
    """Union of every COHORT_* list in gate_config.py, minus SKIP, sorted.

    Auto-cascades cohort additions: a name added to gate_config.py will be
    captured on the next run with no parquet edits required.
    """
    from scripts.qualifier import gate_config as G
    union: set[str] = set()
    for attr in dir(G):
        if attr.startswith("COHORT_"):
            value = getattr(G, attr)
            if isinstance(value, (list, tuple, set)):
                union.update(value)
    return sorted(s for s in union if s not in SKIP)


def write_snapshots(snaps: list[dict]) -> int:
    if not snaps:
        return 0
    conn = connect()
    cur = conn.cursor()
    placeholders = ", ".join(["?"] * len(COLUMNS))
    col_list = ", ".join(COLUMNS)
    rows = []
    for s in snaps:
        s["dividend_flag"] = 1 if s.get("dividend_flag") else 0
        s.setdefault("data_source", "schwab")
        rows.append([s.get(c) for c in COLUMNS])
    cur.executemany(
        f"INSERT OR REPLACE INTO live_snapshots ({col_list}) "
        f"VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    n = cur.rowcount
    conn.close()
    return n


def compute_regime_state(today: date) -> dict | None:
    """Compute daily SPY regime state using ORATS by-ticker history.

    Read SPY history (last ~300 trading days), compute:
      - 200dma + below_200dma flag (yesterday's close)
      - 252-day IVR on ATM 30-DTE IV
      - term_spread = 30-DTE ATM IV - 75-DTE ATM IV (negative = contango)
      - VRP = ATM IV30 - RV20 (RV20 from close-to-close)
      - h1_active, hard_pause_active, soft_downsize_active, if_gate_active
      - bull_put_signal_active
      - stage (1-5 per Regime Transition section of TRADING_PLAN.rtf)

    Returns a dict ready for INSERT, or None if SPY history is missing.
    """
    import numpy as np
    import pandas as pd

    spy_path = Path.home() / "MaxPain_Project/data/orats/by_ticker/SPY.parquet"
    if not spy_path.exists():
        return None
    spy = pd.read_parquet(spy_path,
                          columns=["trade_date", "expirDate", "strike", "stkPx",
                                   "delta", "cMidIv"])
    spy["trade_date"] = pd.to_datetime(spy["trade_date"])
    spy["exp_dt"] = pd.to_datetime(spy["expirDate"], format="%m/%d/%Y", errors="coerce")
    spy["dte"] = (spy["exp_dt"] - spy["trade_date"]).dt.days

    # Build daily series: close + 30-DTE ATM IV + 75-DTE ATM IV
    spy["delta_dist"] = (spy["delta"] - 0.50).abs()
    front = spy[(spy["dte"] >= 25) & (spy["dte"] <= 35)].sort_values(
        ["trade_date", "delta_dist"]).drop_duplicates("trade_date")
    back = spy[(spy["dte"] >= 65) & (spy["dte"] <= 85)].sort_values(
        ["trade_date", "delta_dist"]).drop_duplicates("trade_date")
    daily = front.set_index("trade_date")[["stkPx", "cMidIv"]].copy()
    daily.columns = ["close", "atm_iv30"]
    daily["atm_iv75"] = back.set_index("trade_date")["cMidIv"]
    daily = daily.sort_index()
    if len(daily) < 252:
        return None

    # Split-adjust the close series so the 200-DMA / log-returns aren't corrupted
    # by an unadjusted split discontinuity (reference_orats_split_adjustment).
    # SPY has never split, so this is defensive — but it keeps every MA reader on
    # one code path. Back-adjustment leaves the latest segment unchanged, so the
    # reported spy_close stays the true current price. Only swap if the adjusted
    # series aligns cleanly; otherwise fall back to the raw front-month close.
    try:
        adj = load_adjusted_close("SPY")
        adj.index = pd.to_datetime(adj.index)
        aligned = adj.reindex(daily.index)
        if float(aligned.notna().mean()) > 0.99:
            daily["close"] = aligned
    except Exception:
        pass

    daily["ma200"] = daily["close"].rolling(200, min_periods=100).mean()
    daily["below_200dma"] = daily["close"] < daily["ma200"]
    rmin = daily["atm_iv30"].rolling(252, min_periods=120).min()
    rmax = daily["atm_iv30"].rolling(252, min_periods=120).max()
    daily["ivr_252"] = (daily["atm_iv30"] - rmin) / (rmax - rmin).replace(0, np.nan)
    daily["term_spread"] = daily["atm_iv30"] - daily["atm_iv75"]
    daily["log_ret"] = np.log(daily["close"] / daily["close"].shift(1))
    daily["rv20"] = daily["log_ret"].rolling(20).std() * np.sqrt(252)
    daily["vrp"] = daily["atm_iv30"] - daily["rv20"]

    # Use most recent row available (typically yesterday's close)
    last = daily.iloc[-1]
    if pd.isna(last["ma200"]) or pd.isna(last["ivr_252"]):
        return None

    spy_close = float(last["close"])
    ma200 = float(last["ma200"])
    pct_to_ma200 = (spy_close / ma200 - 1) if ma200 > 0 else np.nan
    iv_rank = float(last["ivr_252"])
    term_spread = float(last["term_spread"]) if pd.notna(last["term_spread"]) else None
    vrp = float(last["vrp"]) if pd.notna(last["vrp"]) else None
    below_200dma = bool(spy_close < ma200)
    ivr_high = bool(iv_rank > 0.5)
    term_inverted = bool(term_spread > 0) if term_spread is not None else False
    contango = bool(term_spread < 0) if term_spread is not None else False
    vrp_positive = bool(vrp > 0) if vrp is not None else False
    h1_active = bool(below_200dma and ivr_high)
    hard_pause_active = bool(below_200dma and term_inverted and ivr_high)
    bull_put_signal_active = bool(contango and vrp_positive)
    if_gate_active = bool(term_inverted)

    # Fetch VIX (close-to-close) via yfinance. If fetch fails, fall back to
    # ATM IV30 > 20% proxy. VIX is on the same scale as IV (decimal form, so
    # 17.50 VIX = 17.50, not 0.175).
    vix_value = None
    try:
        import yfinance as yf
        vix_hist = yf.Ticker("^VIX").history(period="5d")
        if not vix_hist.empty:
            vix_value = float(vix_hist["Close"].iloc[-1])
    except Exception:
        pass

    # Soft-downsize: any of (IVR>0.7, |spy/ma200 - 1| <= 0.02 trending down,
    # term_inv AND VIX > 20).
    near_ma200 = abs(pct_to_ma200) <= 0.02 if pct_to_ma200 is not None else False
    trending_down = below_200dma  # simple proxy: actually below the line
    if vix_value is not None:
        vix_high = vix_value > 20.0
    else:
        # Fallback: ATM IV30 > 20% proxy if VIX feed failed
        vix_high = float(last["atm_iv30"]) > 0.20
    soft_downsize_active = bool(
        iv_rank > 0.7
        or (near_ma200 and trending_down)
        or (term_inverted and vix_high)
    )

    # Stage classification (per TRADING_PLAN.rtf v1.7 Regime Transition section)
    if h1_active:
        stage = 3  # Default for H1 active. Could refine to Stage 4 if drawdown is deep,
                   # Stage 5 if recovering — but those need historical context the daily
                   # signal alone can't provide. Stage 3 is the default once H1 fires;
                   # 4/5 distinctions are surfaced by the cycle qualifier from this state.
    elif below_200dma:
        stage = 2
    elif soft_downsize_active:
        stage = 1
    else:
        stage = 0  # calm/bull

    return {
        "snapshot_date": str(today),
        "as_of_close": str(last.name.date()),
        "spy_close": spy_close,
        "spy_ma200": round(ma200, 4),
        "spy_pct_to_ma200": round(pct_to_ma200, 5) if pct_to_ma200 is not None else None,
        "spy_atm_iv30": round(float(last["atm_iv30"]), 4),
        "spy_ivr_252": round(iv_rank, 4),
        "spy_term_spread": round(term_spread, 5) if term_spread is not None else None,
        "spy_vrp": round(vrp, 5) if vrp is not None else None,
        "spy_vix": round(vix_value, 2) if vix_value is not None else None,
        "below_200dma": int(below_200dma),
        "ivr_high": int(ivr_high),
        "term_inverted": int(term_inverted),
        "h1_active": int(h1_active),
        "hard_pause_active": int(hard_pause_active),
        "soft_downsize_active": int(soft_downsize_active),
        "if_gate_active": int(if_gate_active),
        "bull_put_signal_active": int(bull_put_signal_active),
        "stage": stage,
    }


REGIME_COLUMNS = [
    "snapshot_date", "as_of_close",
    "spy_close", "spy_ma200", "spy_pct_to_ma200",
    "spy_atm_iv30", "spy_ivr_252", "spy_term_spread", "spy_vrp", "spy_vix",
    "below_200dma", "ivr_high", "term_inverted",
    "h1_active", "hard_pause_active", "soft_downsize_active",
    "if_gate_active", "bull_put_signal_active",
    "stage",
]


def write_regime_state(state: dict) -> None:
    if state is None:
        return
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS regime_state (
            snapshot_date TEXT PRIMARY KEY,
            as_of_close TEXT,
            spy_close REAL, spy_ma200 REAL, spy_pct_to_ma200 REAL,
            spy_atm_iv30 REAL, spy_ivr_252 REAL,
            spy_term_spread REAL, spy_vrp REAL, spy_vix REAL,
            below_200dma INTEGER, ivr_high INTEGER, term_inverted INTEGER,
            h1_active INTEGER, hard_pause_active INTEGER,
            soft_downsize_active INTEGER, if_gate_active INTEGER,
            bull_put_signal_active INTEGER,
            stage INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    placeholders = ", ".join(["?"] * len(REGIME_COLUMNS))
    col_list = ", ".join(REGIME_COLUMNS)
    cur.execute(
        f"INSERT OR REPLACE INTO regime_state ({col_list}) VALUES ({placeholders})",
        [state.get(c) for c in REGIME_COLUMNS],
    )
    conn.commit()
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", nargs="+", help="Subset of cohort to capture")
    parser.add_argument("--dry-run", action="store_true", help="No DB write")
    parser.add_argument("--regime-only", action="store_true",
                        help="Skip cohort capture, only compute regime state")
    args = parser.parse_args()

    cohort = args.symbol if args.symbol else load_cohort()
    opex = current_opex()
    today = date.today()

    print(f"\n{'='*65}")
    print(f"  Research Cohort Snapshot — {today}  |  OpEx: {opex}  |  "
          f"{len(cohort)} symbols  (gate_config union)")
    print(f"{'='*65}\n")

    snaps = []
    if not args.regime_only:
        for sym in cohort:
            try:
                snap = take_snapshot(sym, opex, today)
                if snap:
                    snaps.append(snap)
            except Exception as e:
                print(f"  {sym}... ERROR: {e}")

    # Compute regime state from SPY ORATS history (yesterday's close)
    print("\n  Computing daily regime state from SPY ORATS history...")
    regime = compute_regime_state(today)
    if regime is not None:
        stage_label = {
            0: "calm/bull",
            1: "soft-downsize triggered",
            2: "SPY < 200dma (no IVR confirmation)",
            3: "H1 active (bear regime)",
        }.get(regime["stage"], f"stage {regime['stage']}")
        print(f"    SPY close (as of {regime['as_of_close']}): ${regime['spy_close']:.2f}")
        print(f"    SPY 200dma:                {regime['spy_ma200']:.2f}  "
              f"({'BELOW' if regime['below_200dma'] else 'above'})")
        print(f"    SPY 252-day IVR:           {regime['spy_ivr_252']:.3f}  "
              f"({'HIGH' if regime['ivr_high'] else 'low'})")
        if regime['spy_term_spread'] is not None:
            print(f"    SPY term spread (30-75d):  {regime['spy_term_spread']:+.4f}  "
                  f"({'INVERTED' if regime['term_inverted'] else 'contango'})")
        if regime['spy_vrp'] is not None:
            print(f"    SPY VRP (IV30 - RV20):     {regime['spy_vrp']:+.4f}")
        if regime.get('spy_vix') is not None:
            print(f"    VIX (close-to-close):      {regime['spy_vix']:.2f}")
        else:
            print(f"    VIX:                       (fetch failed; using ATM IV30 proxy)")
        print(f"    H1 active:                 {bool(regime['h1_active'])}")
        print(f"    Hard pause active:         {bool(regime['hard_pause_active'])}")
        print(f"    Soft-downsize active:      {bool(regime['soft_downsize_active'])}")
        print(f"    IF gate active:            {bool(regime['if_gate_active'])}")
        print(f"    Bull-put signal active:    {bool(regime['bull_put_signal_active'])}")
        print(f"    Stage: {regime['stage']} ({stage_label})")
    else:
        print("    (insufficient SPY history; regime computation skipped)")

    # Go-live guard (D4): refuse to stamp today's snapshot_date on stale ORATS
    # data. This is the 2026-05-21→26 incident mode — SFTP delivery breaks,
    # daily.iloc[-1] is days old, and a fresh-dated regime row silently drives
    # the 9:25 qualifier.
    #
    # Expectation is TWO business days back, not one: ORATS delivers trade
    # date D at ~19:00 on D+1 (verified from parquet mtimes 2026-06), so at
    # this 9:20 run the freshest possible close is D-2. A 1-day expectation
    # would false-alarm every morning. Day-1 stalls are caught the same
    # evening by orats_health (19:40, expects D-1); this guard catches day-2+
    # at 9:20 — before the 9:25 qualifier — and withholds the regime write.
    regime_stale = False
    if regime is not None:
        expected_close = previous_business_day(previous_business_day(today))
        as_of = date.fromisoformat(regime["as_of_close"])
        if as_of < expected_close:
            regime_stale = True
            print(f"    ✗ STALE ORATS INPUT: regime as_of_close {as_of} is older "
                  f"than expected {expected_close} — regime_state will NOT be written.")

    if args.dry_run:
        print(f"\nDry-run: {len(snaps)}/{len(cohort)} snapshots captured (not written).")
        if regime is not None:
            print("Regime state computed but not written.")
        return

    if not args.regime_only:
        n = write_snapshots(snaps)
        print(f"\n  ✓ live_snapshots updated  ({n} rows upserted)")
    if regime is not None and not regime_stale:
        write_regime_state(regime)
        print(f"  ✓ regime_state updated  (stage={regime['stage']})")
    print(f"✓ Snapshot complete — {len(snaps)}/{len(cohort)} symbols captured.\n")

    # Trap silent failure: cohort non-empty but every snapshot failed (chain
    # endpoint down) — exit non-zero so run_cron alerts rather than leaving the
    # qualifier to run on stale max_pain.
    if not args.regime_only and cohort and not snaps:
        print("✗ 0 snapshots captured — exiting 1 so cron traps it.")
        sys.exit(1)

    # Stale-ORATS guard tripped: live_snapshots (Schwab, live) were still
    # written above, but regime_state was withheld. Exit non-zero so run_cron
    # emails at 9:20 — BEFORE the 9:25 qualifier runs on yesterday's regime row.
    if regime_stale:
        print("✗ Stale ORATS regime input — exiting 1 so cron traps it "
              "(regime_state not updated; check ORATS SFTP delivery).")
        sys.exit(1)


if __name__ == "__main__":
    main()
