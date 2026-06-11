"""Per-ticker breach-recovery / stop-loss profile — DB-backed lookup + scan-on-miss.

Canonical store: the `ticker_stop_profile` table (operational state), one row per
(ticker, structure). Built by scripts/backtest/per_ticker_stop_study.py and
refreshed twice a year; new cohort names are filled on demand (ensure_profile /
ensure_cohort_profiles). Both the daily alert and the promotion path are then a
simple DB lookup.

Answers, per (ticker, structure):
  • MEAN-REVERTER → breaches recover; hold. Surfaces recovery rate + median days
    to recovery ("how long to wait").
  • NON-REVERTER (robust only) → breaches keep going; surfaces a per-ticker STOP
    depth (% beyond the short strike).

Robustness gate (applied at READ time, so it can be tuned without repopulating):
a name is only treated NON-REVERTER if its stop signal is walk-forward stable
(train/test same sign). Non-robust "non-reverters" fall back to mean-reverter.

DESCRIPTIVE only: informs MANUAL stop placement; does not gate size or auto-place
stops. (Pre-register before it gates anything.) Soft-fails (returns None) if the
table is missing/empty.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

import pandas as pd

from lib.db import DB_PATH

MIN_BREACHED = 12
STALE_DAYS = 200          # ensure_* re-scans a profile older than this
TABLE = "ticker_stop_profile"
PROFILE_PARQUET = Path.home() / "MaxPain_Project" / "data/profile/per_ticker_stop_profile.parquet"

_COLS = ("ticker", "structure", "n_cycles", "n_breached", "classification",
         "stop_depth", "recovery_rate", "median_recovery_days", "stop_value",
         "wf_train", "wf_test", "wf_stable", "as_of_date")


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            ticker TEXT, structure TEXT,
            n_cycles INTEGER, n_breached INTEGER,
            classification TEXT, stop_depth REAL,
            recovery_rate REAL, median_recovery_days REAL,
            stop_value REAL, wf_train REAL, wf_test REAL, wf_stable INTEGER,
            as_of_date TEXT,
            PRIMARY KEY (ticker, structure))""")


def upsert_profiles(conn: sqlite3.Connection, prof: pd.DataFrame,
                    as_of: Optional[str] = None) -> int:
    """INSERT OR REPLACE rows from a build_profile() DataFrame. Maps the study's
    `stop_value_at_dstar_or_7` column to `stop_value`. Returns rows written."""
    ensure_table(conn)
    as_of = as_of or date.today().isoformat()
    n = 0
    for _, r in prof.iterrows():
        sv = r.get("stop_value", r.get("stop_value_at_dstar_or_7"))
        conn.execute(
            f"INSERT OR REPLACE INTO {TABLE} "
            f"({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
            (r["ticker"], r["structure"], int(r["n_cycles"]), int(r["n_breached"]),
             r["classification"],
             float(r["stop_depth"]) if pd.notna(r["stop_depth"]) else None,
             float(r["recovery_rate"]) if pd.notna(r["recovery_rate"]) else None,
             float(r["median_recovery_days"]) if pd.notna(r["median_recovery_days"]) else None,
             float(sv) if pd.notna(sv) else None,
             float(r["wf_train"]) if pd.notna(r["wf_train"]) else None,
             float(r["wf_test"]) if pd.notna(r["wf_test"]) else None,
             int(bool(r["wf_stable"])), as_of))
        n += 1
    conn.commit()
    _load.cache_clear()
    return n


@lru_cache(maxsize=1)
def _load() -> Optional[pd.DataFrame]:
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            df = pd.read_sql(f"SELECT * FROM {TABLE}", conn)
        finally:
            conn.close()
        return df if not df.empty else None
    except Exception:
        return None


def _norm_structure(structure: str) -> Optional[str]:
    s = (structure or "").lower()
    if s.startswith("bull_put"):
        return "bull_put"
    if s.startswith("bear_call"):
        return "bear_call"
    return None


def lookup(ticker: str, structure: str) -> Optional[dict]:
    """Return the (ticker, structure) profile with a robustness-gated effective
    classification, or None if unavailable/insufficient."""
    struct = _norm_structure(structure)
    if struct is None:
        return None
    df = _load()
    if df is None:
        return None
    m = df[(df["ticker"] == ticker) & (df["structure"] == struct)]
    if m.empty:
        return None
    r = m.iloc[0]
    if int(r["n_breached"]) < MIN_BREACHED or r["classification"] == "INSUFFICIENT":
        return None
    robust_nonrevert = (r["classification"] == "NON_REVERT") and bool(r["wf_stable"]) \
        and pd.notna(r["stop_depth"])
    effective = "NON_REVERT" if robust_nonrevert else "MEAN_REVERT"
    return {
        "ticker": ticker, "structure": struct, "effective": effective,
        "raw_classification": r["classification"], "wf_stable": bool(r["wf_stable"]),
        "stop_depth": float(r["stop_depth"]) if pd.notna(r["stop_depth"]) else None,
        "recovery_rate": float(r["recovery_rate"]) if pd.notna(r["recovery_rate"]) else None,
        "median_recovery_days": float(r["median_recovery_days"]) if pd.notna(r["median_recovery_days"]) else None,
        "n_breached": int(r["n_breached"]),
        "as_of_date": r.get("as_of_date"),
    }


# ─── scan-on-miss / refresh ──────────────────────────────────────────────

def _scan_ticker(ticker: str) -> Optional[pd.DataFrame]:
    """Run the breach study for ONE ticker (both verticals); return a profile df.
    Imports the backtest engine lazily so the alert's read path stays light."""
    import sys
    root = Path.home() / "MaxPain_Project"
    for p in (str(root), str(root / "scripts/backtest")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import per_ticker_stop_study as S  # noqa: E402
    rows = S.simulate_ticker(ticker)
    if not rows:
        return None
    return S.build_profile(pd.DataFrame(rows))


def ensure_profile(ticker: str, conn: Optional[sqlite3.Connection] = None,
                   max_age_days: int = STALE_DAYS) -> bool:
    """Make sure `ticker` has a fresh profile; scan + upsert if missing or stale.
    Returns True if a scan ran. Use from the promotion path / maintenance."""
    own = conn is None
    conn = conn or sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        row = conn.execute(
            f"SELECT MAX(as_of_date) FROM {TABLE} WHERE ticker=?", (ticker,)).fetchone()
        as_of = row[0] if row else None
        fresh = False
        if as_of:
            try:
                fresh = (date.today() - date.fromisoformat(as_of)).days <= max_age_days
            except Exception:
                fresh = False
        if fresh:
            return False
        prof = _scan_ticker(ticker)
        if prof is None or prof.empty:
            return False
        upsert_profiles(conn, prof)
        return True
    finally:
        if own:
            conn.close()


def ensure_cohort_profiles(only_missing: bool = True) -> dict:
    """Fill (scan + upsert) profiles for every current cohort credit-vertical name
    that is missing (only_missing=True) or stale. Cheap when nothing is due.
    Returns {scanned: [...], skipped_present: n}."""
    import sys
    root = Path.home() / "MaxPain_Project"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import scripts.qualifier.gate_config as G
    names = sorted(set(G.COHORT_BULL_PUT) | set(G.COHORT_BEAR_CALL))
    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)
    scanned, present = [], 0
    try:
        for tk in names:
            have = conn.execute(f"SELECT 1 FROM {TABLE} WHERE ticker=? LIMIT 1", (tk,)).fetchone()
            if have and only_missing:
                present += 1
                continue
            if ensure_profile(tk, conn=conn):
                scanned.append(tk)
    finally:
        conn.close()
    return {"scanned": scanned, "skipped_present": present}


# ─── alert rendering ─────────────────────────────────────────────────────

def card_note(ticker: str, structure: str) -> Optional[dict]:
    """One-line breach-recovery note for a credit-vertical construction card.
    Returns {'text','html'} or None. Descriptive — informs manual stop placement."""
    p = lookup(ticker, structure)
    if p is None:
        return None
    rate = f"{p['recovery_rate']*100:.0f}%" if p["recovery_rate"] is not None else "?"
    if p["effective"] == "NON_REVERT":
        days = (f", and when it does, ~{p['median_recovery_days']:.0f}d"
                if p["median_recovery_days"] is not None else "")
        text = (f"  ⛔ BREACH PROFILE: {ticker} {p['structure']} historically does NOT mean-revert "
                f"(only {rate} of breaches recovered{days}) — set a STOP ~{p['stop_depth']*100:.0f}% "
                f"beyond the short strike rather than riding the 2× rule. [walk-forward stable]")
        color, bg = "#a00", "#fdecea"
    else:
        days = (f"~{p['median_recovery_days']:.0f} trading days"
                if p["median_recovery_days"] is not None else "a few days")
        soft = " (a no-revert read existed but failed walk-forward — treat as revert)" \
            if p["raw_classification"] == "NON_REVERT" else ""
        text = (f"  🔁 BREACH PROFILE: {ticker} {p['structure']} mean-reverts — {rate} of short-strike "
                f"breaches recover, typically within {days}; hold through a transient breach{soft}.")
        color, bg = "#1a5fb4", "#f0f6ff"
    html = (f"<div style='font-size:12px;color:{color};margin:4px 0 12px 0;"
            f"padding:6px 10px;background:{bg};border-left:3px solid {color}'>{text.strip()}</div>")
    return {"text": text, "html": html}


# ─── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Per-ticker stop-profile maintenance.")
    ap.add_argument("--ensure-cohort", action="store_true",
                    help="scan + fill profiles for cohort names missing an entry")
    ap.add_argument("--ensure", metavar="TICKER", help="scan + (re)fill one ticker")
    ap.add_argument("--load-parquet", action="store_true",
                    help="one-time: load the study's profile parquet into the DB")
    args = ap.parse_args()
    if args.load_parquet:
        c = sqlite3.connect(DB_PATH)
        n = upsert_profiles(c, pd.read_parquet(PROFILE_PARQUET))
        c.close()
        print(f"loaded {n} profiles from {PROFILE_PARQUET}")
    if args.ensure:
        print("scanned" if ensure_profile(args.ensure) else "already fresh / no data")
    if args.ensure_cohort:
        print(ensure_cohort_profiles())
