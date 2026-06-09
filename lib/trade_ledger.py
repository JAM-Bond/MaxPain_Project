"""
Trade ledger as a learning substrate.

Enriches spread_score_trades with regime context (at entry + exit), qualifier
provenance (verdict + reason), MAE during hold, and an inferred exit_type
classification. The output is a single per-trade DataFrame that downstream
analytics modules slice + group.

Per project_trade_ledger_learning.md: the regime + qualifier fields are
"populated AT TIME OF ENTRY" in the live system. For backfill (closed trades
that predate the qualifier_run_date link), we best-effort match by entry_date
+ symbol + structure-prefix, and fall back to NULL when no qualifier row
exists. Trades without a qualifier match are flagged off_script=1.

Adequacy thresholds match the project convention (PRELIMINARY <10,
SUGGESTIVE <20, DEVELOPING <30, ADEQUATE ≥30).

Usage:
    from lib.trade_ledger import load_trade_ledger
    df = load_trade_ledger(conn)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd


def adequacy_flag(n: int) -> Literal["PRELIMINARY", "SUGGESTIVE", "DEVELOPING", "ADEQUATE"]:
    if n < 10:
        return "PRELIMINARY"
    if n < 20:
        return "SUGGESTIVE"
    if n < 30:
        return "DEVELOPING"
    return "ADEQUATE"


def ensure_sector_column(conn: sqlite3.Connection) -> None:
    """Idempotently add the `sector` column to spread_score_trades."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(spread_score_trades)")]
    if "sector" not in cols:
        conn.execute("ALTER TABLE spread_score_trades ADD COLUMN sector TEXT")
        conn.commit()


# Canonical exit-type vocabulary recorded explicitly at close (vs. inferred).
# Keep in sync with _classify_exit_type's inferred labels.
EXIT_TYPES = {
    "profit_target",   # 50% (verticals) / 50% (IF) credit-capture target fired
    "t21_managed",     # credit vertical closed at the T-21 time stop
    "t21_roll",        # zebra rolled at the T-21 cue
    "t3_5_window",     # OpEx-week (T-3..T-5) managed close
    "stop_loss",       # STP LMT / discretionary stop hit
    "expiry",          # settled at expiration
    "rolled",          # rolled to a later expiry (lineage in roll_from_trade_id if tracked)
    "manual_close",    # discretionary close, none of the above
}


def ensure_exit_type_column(conn: sqlite3.Connection) -> None:
    """Idempotently add the `exit_type` column to spread_score_trades. This is the
    structured close telemetry the ledger spec asks for, so the loader no longer
    has to INFER the exit reason for trades closed after this lands."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(spread_score_trades)")]
    if "exit_type" not in cols:
        conn.execute("ALTER TABLE spread_score_trades ADD COLUMN exit_type TEXT")
        conn.commit()


def record_close(conn: sqlite3.Connection, trade_id: int, *, exit_date: str,
                 exit_type: str, exit_price: float | None = None,
                 exit_credit: float | None = None, final_pnl: float | None = None,
                 status: str = "closed") -> dict:
    """Record a trade close WITH explicit exit-type telemetry (the close-protocol
    path from feedback_close_trade_protocol). SELECT-confirm-UPDATE: returns the
    pre-update row so the caller can confirm identity before trusting the write.

    exit_type must be one of EXIT_TYPES — this is the whole point: capture the
    reason at close instead of inferring it later from DTE heuristics. Raises on an
    unknown exit_type or a missing/non-open trade."""
    if exit_type not in EXIT_TYPES:
        raise ValueError(f"exit_type {exit_type!r} not in {sorted(EXIT_TYPES)}")
    ensure_exit_type_column(conn)
    cur = conn.execute(
        "SELECT id, symbol, spread_type, status, opex_date, entry_credit "
        "FROM spread_score_trades WHERE id=?", (trade_id,)).fetchone()
    if cur is None:
        raise ValueError(f"no trade with id={trade_id}")
    before = {"id": cur[0], "symbol": cur[1], "spread_type": cur[2],
              "status": cur[3], "opex_date": cur[4], "entry_credit": cur[5]}
    conn.execute(
        "UPDATE spread_score_trades SET exit_date=?, exit_price=?, exit_credit=?, "
        "final_pnl=?, status=?, exit_type=? WHERE id=?",
        (exit_date, exit_price, exit_credit, final_pnl, status, exit_type, trade_id))
    conn.commit()
    return before


def backfill_sectors(conn: sqlite3.Connection, force: bool = False) -> int:
    """Populate spread_score_trades.sector from lib.sector_map.get_sector(symbol).

    Idempotent. By default fills only NULL/empty sectors (never clobbers a manual
    override); force=True re-derives every row (e.g. after a sector_map.py update).
    Returns rows updated. Lets the placed book be sliced by GICS sector for
    rotation / post-mortem, complementing the recommendation-slate drift watch.
    """
    from lib.sector_map import get_sector
    ensure_sector_column(conn)
    cond = "" if force else " AND (sector IS NULL OR sector='')"
    where = "" if force else "WHERE sector IS NULL OR sector=''"
    syms = [r[0] for r in conn.execute(
        f"SELECT DISTINCT symbol FROM spread_score_trades {where}")]
    n = 0
    for sym in syms:
        n += conn.execute(
            f"UPDATE spread_score_trades SET sector=? WHERE symbol=?{cond}",
            (get_sector(sym), sym)).rowcount
    conn.commit()
    return n


def _load_trades(conn: sqlite3.Connection) -> pd.DataFrame:
    """Pull spread_score_trades + already-attached entry-context columns."""
    return pd.read_sql_query("""
        SELECT
            id AS trade_id, symbol, opex_date, spread_type AS structure,
            short_strike, long_strike, width, entry_credit, entry_date,
            entry_price, exit_date, exit_credit, exit_price, final_pnl,
            status, placed, shares,
            target_hit_date, target_hit_pnl, target_hit_days_held,
            entry_iv_rank, entry_iv_percentile, entry_vrp, entry_gex_z,
            entry_skew, entry_short_delta, entry_composite, entry_vix,
            qualifier_run_date, exit_type AS exit_type_recorded
        FROM spread_score_trades
    """, conn)


def _load_regime(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("""
        SELECT snapshot_date, stage, spy_pct_to_ma200, spy_ivr_252,
               spy_term_spread, spy_vrp, h1_active, bull_put_signal_active,
               if_gate_active, hard_pause_active, soft_downsize_active,
               below_200dma, ivr_high, term_inverted, spy_vix
        FROM regime_state
    """, conn)


def _load_qualifier(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("""
        SELECT run_date, symbol, structure, verdict, size, reason,
               regime_stage AS qualifier_regime_stage,
               regime_h1, regime_if_gate, regime_bp_signal,
               window, target, opex, days_until
        FROM cycle_qualifier_runs
    """, conn)


def _load_marks(conn: sqlite3.Connection) -> pd.DataFrame:
    """Daily marks per trade — used to compute MAE + days held."""
    return pd.read_sql_query("""
        SELECT trade_id, mark_date, unrealized_pnl, pnl_pct,
               underlying_price, dte
        FROM spread_score_daily
    """, conn)


# ── Mark-derived metrics ────────────────────────────────────────────────────

def _mae_per_trade(marks: pd.DataFrame) -> pd.DataFrame:
    """Compute per-trade max adverse excursion + N-marks count.

    MAE here = the worst (most negative) unrealized_pnl observed during the
    hold. If unrealized_pnl never went negative, MAE = 0. Trades with zero
    marks return NaN (mark daemon was off or trade closed same-day).
    """
    if marks.empty:
        return pd.DataFrame(columns=["trade_id", "mae", "n_marks"])
    g = marks.groupby("trade_id")
    return pd.DataFrame({
        "trade_id": g.size().index,
        "mae": g["unrealized_pnl"].min().clip(upper=0).values,
        "n_marks": g.size().values,
    })


# ── Qualifier match (with fallback for pre-link trades) ────────────────────

# spread_type → list of qualifier-side structure prefixes to try, in order.
# Matches the live qualifier output naming (`bull_put_earnings`,
# `zebra_tier1/2`, `inverted_fly_pair/single`).
STRUCTURE_PREFIX_FALLBACK = {
    "bull_put": ["bull_put", "bull_put_earnings"],
    "bear_call": ["bear_call", "bear_call_earnings"],
    "inverted_fly": ["inverted_fly_pair", "inverted_fly_single",
                     "inverted_fly_earnings"],
    "zebra": ["zebra_tier1", "zebra_tier2"],
    "zebra_protected": ["zebra_tier1", "zebra_tier2"],
}


_VERDICT_RANK = {"GO": 0, "DOWNSIZE": 1, "PENDING": 2, "SKIP": 3, "PAUSE": 4,
                 "NOT_IN_COHORT": 5}


def _attach_qualifier(trades: pd.DataFrame, qual: pd.DataFrame) -> pd.DataFrame:
    """For each trade, attach qualifier verdict.

    Strategy:
      A) When trades.qualifier_run_date is set, direct join on
         (qualifier_run_date, symbol, structure, opex). The opex match
         (added 2026-05-05) is required so a trade for OpEx X doesn't get
         linked to a qualifier verdict for OpEx Y on the same name/date.
      B) Otherwise, fall back to (entry_date, symbol, structure-prefix-list,
         opex). Pick the qualifier row with the best verdict rank
         (GO > DOWNSIZE > ...).
      C) If no match, leave qualifier columns NULL and set off_script=1.
    """
    if qual.empty:
        for col in ("qualifier_verdict", "qualifier_size", "qualifier_reason",
                    "qualifier_regime_stage", "qualifier_window",
                    "qualifier_target", "qualifier_days_until"):
            trades[col] = np.nan
        trades["off_script"] = 1
        return trades

    qual = qual.copy()
    qual["_vrank"] = qual["verdict"].map(_VERDICT_RANK).fillna(99)

    # Normalize opex columns to strings for safe comparison (qualifier stores
    # ISO strings; trades.opex_date may be a Timestamp after _add_derived ran,
    # but _attach_qualifier is called BEFORE _add_derived so it should still be
    # the raw string from spread_score_trades).
    trades = trades.copy()
    trades["_opex_str"] = trades["opex_date"].astype(str).str[:10]
    qual["_opex_str"] = qual["opex"].astype(str).str[:10]

    # Strategy A: direct link — now also matched on opex
    direct = trades.dropna(subset=["qualifier_run_date"]).merge(
        qual, how="left",
        left_on=["qualifier_run_date", "symbol", "structure", "_opex_str"],
        right_on=["run_date", "symbol", "structure", "_opex_str"],
        suffixes=("", "_q"),
    )

    # Strategy B: best-effort by entry_date + structure-prefix + opex
    fallback_rows = []
    for _, t in trades[trades["qualifier_run_date"].isna()].iterrows():
        prefixes = STRUCTURE_PREFIX_FALLBACK.get(t["structure"],
                                                  [t["structure"]])
        cand = qual[
            (qual["run_date"] == t["entry_date"])
            & (qual["symbol"] == t["symbol"])
            & (qual["structure"].isin(prefixes))
            & (qual["_opex_str"] == t["_opex_str"])
        ]
        if cand.empty:
            row = t.to_dict()
            for col in ("verdict", "size", "reason", "qualifier_regime_stage",
                        "regime_h1", "regime_if_gate", "regime_bp_signal",
                        "window", "target", "opex", "days_until", "run_date"):
                row[col] = np.nan
            fallback_rows.append(row)
        else:
            best = cand.sort_values("_vrank").iloc[0].to_dict()
            row = t.to_dict()
            for col in ("verdict", "size", "reason", "qualifier_regime_stage",
                        "regime_h1", "regime_if_gate", "regime_bp_signal",
                        "window", "target", "opex", "days_until", "run_date"):
                row[col] = best.get(col, np.nan)
            fallback_rows.append(row)
    fallback = pd.DataFrame(fallback_rows) if fallback_rows else pd.DataFrame(
        columns=trades.columns.tolist() + [
            "verdict", "size", "reason", "qualifier_regime_stage",
            "regime_h1", "regime_if_gate", "regime_bp_signal",
            "window", "target", "opex", "days_until", "run_date",
        ]
    )

    # Drop the helper column from outputs
    direct = direct.drop(columns=["_opex_str"], errors="ignore")
    if not fallback.empty:
        fallback = fallback.drop(columns=["_opex_str"], errors="ignore")

    out = pd.concat([direct, fallback], ignore_index=True, sort=False)
    out = out.rename(columns={
        "verdict": "qualifier_verdict",
        "size": "qualifier_size",
        "reason": "qualifier_reason",
        "window": "qualifier_window",
        "target": "qualifier_target",
        "days_until": "qualifier_days_until",
    })
    if "_vrank" in out.columns:
        out = out.drop(columns=["_vrank"])
    out["off_script"] = out["qualifier_verdict"].isna().astype(int)
    return out


# ── Regime context attach ───────────────────────────────────────────────────

def _attach_regime(trades: pd.DataFrame, regime: pd.DataFrame,
                   when: str, prefix: str) -> pd.DataFrame:
    """Attach regime_state row matched on snapshot_date == trades[when].

    Adds columns prefixed with `prefix_` (e.g., `entry_stage`, `exit_stage`).
    Falls back to NULL when no exact-date match (weekends, holidays).
    """
    if regime.empty:
        for col in ("stage", "spy_pct_to_ma200", "spy_ivr_252", "h1_active",
                    "bull_put_signal_active", "if_gate_active",
                    "hard_pause_active", "soft_downsize_active"):
            trades[f"{prefix}_{col}"] = np.nan
        return trades
    sub = regime.rename(columns={c: f"{prefix}_{c}" for c in regime.columns
                                   if c != "snapshot_date"})
    out = trades.merge(sub, how="left", left_on=when,
                       right_on="snapshot_date")
    out = out.drop(columns=["snapshot_date"], errors="ignore")
    return out


# ── Exit-type classifier ────────────────────────────────────────────────────

# Calendar-day windows around the canonical exit triggers.
# T-21 rule (TastyTrade): close credit verticals at 21 DTE if 50% target
# hasn't fired. Window 14-25 days captures the full "around T-21" cluster
# (decisions naturally drift ±1 week of the exact 21-day mark).
T21_WINDOW = (14, 25)
# T-3 to T-5 = the OpEx-week managed-exit window for verticals that didn't
# trigger T-21 or 50%. Past T-3, gamma + assignment risk dominate.
T3_5_WINDOW = (1, 5)


def _classify_exit_type(row: pd.Series) -> str:
    """Structure-aware exit-type classifier. Order of checks per structure
    family is rule-driven so the labels reflect the trading plan's two
    exit triggers (50% capture OR T-21) rather than just "managed_close".

    Credit verticals (bull_put / bear_call):
      profit_target  — target_hit_date set (50% credit-capture rule fired)
      t21_managed    — exit at 17–25 DTE (T-21 rule fired)
      t3_5_window    — exit at 1–5 DTE (OpEx-week close)
      expiry         — exit on/past opex_date
      manual_close   — none of the above

    Inverted fly (50%-only rule, no time stop per plan):
      profit_target  — target_hit_date set
      expiry         — exit on/past opex_date
      manual_close   — else

    Zebra (delta-1, T-21 roll cue per memory):
      t21_roll       — exit at 17–25 DTE
      expiry         — exit on/past opex_date
      manual_close   — else
      (stop_loss logged via daily_alert; will surface here when written
       to a status field in a later iteration)

    Other / stock / unknown:
      open/expiry/managed_close as before.

    If an explicit exit_type was RECORDED at close (`exit_type` column, set by
    record_close() / the Schwab fills cron), that is authoritative and returned
    verbatim — we only infer for trades that predate explicit telemetry.
    """
    recorded = row.get("exit_type_recorded")
    if isinstance(recorded, str) and recorded.strip():
        return recorded
    status = (row.get("status") or "").lower()
    if status == "open":
        return "open"
    if status in ("rolled", "rolled_out"):
        return "rolled"

    exit_date = row.get("exit_date")
    opex = row.get("opex_date")
    if pd.isna(exit_date) or pd.isna(opex):
        return "unknown"

    if pd.Timestamp(exit_date) >= pd.Timestamp(opex):
        return "expiry"

    dte = row.get("dte_at_exit")
    structure = (row.get("structure") or "").lower()

    # Credit verticals — dual exit rule (50% OR T-21)
    if structure in ("bull_put", "bear_call"):
        if pd.notna(row.get("target_hit_date")):
            return "profit_target"
        if pd.notna(dte) and T21_WINDOW[0] <= dte <= T21_WINDOW[1]:
            return "t21_managed"
        if pd.notna(dte) and T3_5_WINDOW[0] <= dte <= T3_5_WINDOW[1]:
            return "t3_5_window"
        return "manual_close"

    # Inverted fly — 50%-only rule (no time stop per trading plan)
    if structure.startswith("inverted_fly"):
        if pd.notna(row.get("target_hit_date")):
            return "profit_target"
        return "manual_close"

    # Zebra — T-21 roll cue per project_zebra_t21_roll_rule.md
    if structure.startswith("zebra"):
        if pd.notna(dte) and T21_WINDOW[0] <= dte <= T21_WINDOW[1]:
            return "t21_roll"
        return "manual_close"

    # Stock / other — no systematic exit rule, just bucket by timing
    if pd.notna(dte) and T3_5_WINDOW[0] <= dte <= T3_5_WINDOW[1]:
        return "t3_5_window"
    return "managed_close"


# ── Days-held + cycle-traversal ─────────────────────────────────────────────

def _add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Add days_held, win flag, cycle-traversal stage delta."""
    df = df.copy()
    for c in ("entry_date", "exit_date", "opex_date"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["days_held_calendar"] = (df["exit_date"] - df["entry_date"]).dt.days
    df["dte_at_exit"] = (df["opex_date"] - df["exit_date"]).dt.days
    df["win"] = (df["final_pnl"] > 0).where(df["final_pnl"].notna(), np.nan)
    # % of entry credit captured at close. Only meaningful for credit
    # spreads with a positive entry_credit; NaN otherwise.
    df["pct_credit_captured"] = np.where(
        (df["entry_credit"].fillna(0) > 0) & df["exit_credit"].notna(),
        (df["entry_credit"] - df["exit_credit"]) / df["entry_credit"],
        np.nan,
    )
    df["regime_stage_delta"] = (df["exit_stage"] - df["entry_stage"]).where(
        df["entry_stage"].notna() & df["exit_stage"].notna()
    )
    df["regime_transitioned"] = (df["regime_stage_delta"].abs() > 0).astype(
        "Int64"
    )
    df["exit_type"] = df.apply(_classify_exit_type, axis=1)
    return df


# ── Public entry point ─────────────────────────────────────────────────────

def load_trade_ledger(conn: sqlite3.Connection) -> pd.DataFrame:
    """Return the full enriched per-trade DataFrame.

    One row per trade. Includes regime context at entry + exit, qualifier
    provenance (verdict / reason / off_script flag), MAE during hold,
    days held, exit-type classification.

    Filters: none — caller decides whether to restrict to placed=1 or
    status='closed'. Open trades get exit-side fields as NaN and
    exit_type='open'.
    """
    ensure_exit_type_column(conn)  # so _load_trades can SELECT exit_type
    trades = _load_trades(conn)
    regime = _load_regime(conn)
    qual = _load_qualifier(conn)
    marks = _load_marks(conn)

    out = _attach_regime(trades, regime, when="entry_date", prefix="entry")
    out = _attach_regime(out, regime, when="exit_date", prefix="exit")
    out = _attach_qualifier(out, qual)
    # Snapshot-at-entry fidelity: where a frozen entry-context row exists, it is
    # the source of truth (the live recompute above is the fallback for trades
    # not yet materialized). See snapshot_entry().
    out = _overlay_entry_snapshot(conn, out)
    mae = _mae_per_trade(marks)
    out = out.merge(mae, on="trade_id", how="left")
    out = _add_derived(out)
    return out


# ── Snapshot-at-entry materialization ───────────────────────────────────────
#
# project_trade_ledger_learning.md: "the regime + qualifier context fields are
# populated AT TIME OF ENTRY ... They do NOT update later." The live loader
# above recomputes those joins on every call, so an old trade's entry context
# would silently change if regime_state history were later corrected. The
# trade_ledger_enriched table freezes the entry-side context ONCE (at/after the
# entry date, when that day's regime_state row exists) and is never updated for a
# given trade. Exit-side + MAE stay live — they legitimately evolve until close.

ENTRY_REGIME_COLS = [
    "entry_stage", "entry_spy_pct_to_ma200", "entry_spy_ivr_252",
    "entry_spy_term_spread", "entry_spy_vrp", "entry_h1_active",
    "entry_bull_put_signal_active", "entry_if_gate_active",
    "entry_hard_pause_active", "entry_soft_downsize_active",
    "entry_below_200dma", "entry_ivr_high", "entry_term_inverted",
    "entry_spy_vix",
]
ENTRY_QUAL_COLS = [
    "qualifier_verdict", "qualifier_size", "qualifier_reason",
    "qualifier_regime_stage", "regime_h1", "regime_if_gate", "regime_bp_signal",
    "qualifier_window", "qualifier_target", "qualifier_days_until", "off_script",
]
# TEXT-affinity frozen columns; everything else is numeric.
_SNAPSHOT_TEXT_COLS = {
    "qualifier_verdict", "qualifier_reason", "qualifier_window",
    "qualifier_target",
}
_SNAPSHOT_COLS = ENTRY_REGIME_COLS + ENTRY_QUAL_COLS


def ensure_enriched_table(conn: sqlite3.Connection) -> None:
    """Idempotently create trade_ledger_enriched (frozen entry-context store)."""
    col_defs = ", ".join(
        f'"{c}" {"TEXT" if c in _SNAPSHOT_TEXT_COLS else "REAL"}'
        for c in _SNAPSHOT_COLS
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS trade_ledger_enriched ("
        f"trade_id INTEGER PRIMARY KEY, {col_defs}, snapshotted_at TEXT)"
    )
    conn.commit()


def _compute_entry_context(conn: sqlite3.Connection) -> pd.DataFrame:
    """Compute the entry-side context for every trade using the SAME join logic
    as the live loader (so frozen == what the loader would have produced on the
    snapshot date). Returns trade_id + the frozen columns."""
    trades = _load_trades(conn)
    regime = _load_regime(conn)
    qual = _load_qualifier(conn)
    out = _attach_regime(trades, regime, when="entry_date", prefix="entry")
    out = _attach_qualifier(out, qual)
    cols = ["trade_id"] + [c for c in _SNAPSHOT_COLS if c in out.columns]
    return out[cols]


def _load_entry_snapshot(conn: sqlite3.Connection) -> pd.DataFrame:
    """Read the frozen entry-context table (empty df if it doesn't exist yet)."""
    try:
        return pd.read_sql_query("SELECT * FROM trade_ledger_enriched", conn)
    except (pd.errors.DatabaseError, sqlite3.OperationalError):
        return pd.DataFrame()


def _overlay_entry_snapshot(conn: sqlite3.Connection,
                            out: pd.DataFrame) -> pd.DataFrame:
    """Hard-overwrite entry-context columns with frozen values for any trade that
    has a snapshot row. No-op (returns out unchanged) when the table is missing
    or empty, so behavior is identical to pre-materialization until snapshots
    exist."""
    frozen = _load_entry_snapshot(conn)
    if frozen.empty or "trade_id" not in frozen.columns:
        return out
    common = [c for c in _SNAPSHOT_COLS if c in out.columns and c in frozen.columns]
    if not common:
        return out
    out = out.set_index("trade_id")
    fz = frozen.set_index("trade_id")
    idx = out.index.intersection(fz.index)
    for c in common:
        out.loc[idx, c] = fz.loc[idx, c]
    return out.reset_index()


def snapshot_entry(conn: sqlite3.Connection, refresh: bool = False) -> int:
    """Freeze entry-side context for trades not yet snapshotted (the at-trade-open
    materialization). Idempotent: an existing snapshot is NEVER overwritten unless
    refresh=True (which re-freezes every trade — use only to repair a bug, since
    it discards the original immutable entry context). Returns rows written.

    Run daily (e.g. piggybacked on the EOD reconcile): a trade placed today has
    its entry-date regime_state row available by close, so the snapshot captures
    the correct entry context that same evening."""
    ensure_enriched_table(conn)
    ctx = _compute_entry_context(conn)
    # Defer trades whose entry-date regime_state row doesn't exist yet (e.g. a
    # trade placed today, snapshotted before the EOD regime pipeline writes the
    # day's row). Freezing now would lock in NULL entry context forever; instead
    # we skip and re-attempt on the next daily run once the regime row lands.
    regime_present = [c for c in ENTRY_REGIME_COLS if c in ctx.columns]
    if regime_present:
        ctx = ctx[ctx[regime_present].notna().any(axis=1)]
    if not refresh:
        existing = {r[0] for r in conn.execute(
            "SELECT trade_id FROM trade_ledger_enriched")}
        ctx = ctx[~ctx["trade_id"].isin(existing)]
    if ctx.empty:
        return 0
    stamp = datetime.now().isoformat(timespec="seconds")
    cols = [c for c in _SNAPSHOT_COLS if c in ctx.columns]
    placeholders = ", ".join(["?"] * (len(cols) + 2))
    collist = ", ".join(['trade_id'] + [f'"{c}"' for c in cols] + ['snapshotted_at'])
    rows = []
    for _, r in ctx.iterrows():
        vals = [int(r["trade_id"])]
        for c in cols:
            v = r[c]
            vals.append(None if pd.isna(v) else (str(v) if c in _SNAPSHOT_TEXT_COLS else float(v) if isinstance(v, (int, float, np.floating)) else str(v)))
        vals.append(stamp)
        rows.append(vals)
    conn.executemany(
        f"INSERT OR REPLACE INTO trade_ledger_enriched ({collist}) "
        f"VALUES ({placeholders})", rows)
    conn.commit()
    return len(rows)
