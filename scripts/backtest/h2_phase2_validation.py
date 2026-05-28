"""H2 Phase 2 — walk-forward + live-failure validation of 5 candidate
weakness definitions per docs/H2_PHASE2_PREREG.md.

Four sealed gates evaluated per definition (R1, R2, R3, R4, R5):
  A. Pooled crash-rate ≥ 2.0× SPY baseline
  B. ≥3 of 4 walk-forward windows show crash-rate ratio ≥ 1.5×
  C. ≥3 of 5 bull_put losers had the definition firing at their entry date
  D. ≤15% of bull_put winners had the definition firing at their entry date

Decision per pre-reg §6:
  PROMOTE the simplest single-condition definition (R1/R2/R3) that passes
  ALL FOUR gates. R4 (compound) and R5 (cohort-level) only evaluated if all
  of R1/R2/R3 fail. If multiple single-conditions pass, prefer highest
  hit-rate × (1 - false-positive rate).

Outputs:
  data/profile/h2_phase2_validation.parquet  — per-definition × per-gate results
  reports/h2_phase2_validation_<date>.md     — human-readable summary
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
OUT_PARQUET = ROOT / "data/profile/h2_phase2_validation.parquet"
OUT_REPORT_DIR = ROOT / "reports"

sys.path.insert(0, str(ROOT))
from lib.h2_phase2_definitions import (  # noqa: E402
    compute_r1, compute_r2, compute_r3, compute_r4, compute_r5,
    SECTOR_ETF_MAP,
)
from lib.sector_map import get_sector  # noqa: E402
from lib.db import DB_PATH  # noqa: E402

import sqlite3  # noqa: E402

# ── Sealed pre-reg parameters ────────────────────────────────────────────────
FORWARD_DAYS = 75              # crash-window horizon (same as Phase 1)
CRASH_THRESHOLD = -0.20        # 75d return below this = crash
BULL_EXTENSION_THRESHOLD = 0.07  # SPY ≥ 1.07 × 200dma = bull-extended

# Gate A: pooled crash-rate ratio
GATE_A_RATIO = 2.0

# Gate B: walk-forward
GATE_B_RATIO = 1.5
GATE_B_MIN_WINDOWS = 3

# Gate C: bull_put loser hit rate (≥3 of 5)
GATE_C_MIN_MATCHES = 3
LIVE_FAILURES_BULLPUT = [
    ("B",   date(2026, 4, 16)),
    ("PFE", date(2026, 4, 16)),
    ("FCX", date(2026, 4, 16)),
    ("XLU", date(2026, 4, 17)),
    ("WFC", date(2026, 5, 5)),
]

# Gate D: false-positive rate on bull_put winners
GATE_D_MAX_FPR = 0.15

# Walk-forward windows (sealed)
WALK_FORWARD_WINDOWS = [
    ("2021-2023", range(2021, 2024)),
    ("2022-2024", range(2022, 2025)),
    ("2023-2025", range(2023, 2026)),
    ("2024-2026", range(2024, 2027)),
]


# ─── Data loaders ────────────────────────────────────────────────────────

def build_close_panel() -> pd.DataFrame:
    """Build dates × tickers close-price panel from by_ticker parquets."""
    closes = {}
    log = logging.getLogger("h2p2val")
    files = sorted(BY_TICKER.glob("*.parquet"))
    log.info("Loading %d ticker parquets...", len(files))
    for i, p in enumerate(files, 1):
        ticker = p.stem
        try:
            df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
        except Exception:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df.drop_duplicates(subset=["trade_date"], keep="first")
        s = df.set_index("trade_date")["stkPx"].astype(float)
        closes[ticker] = s
        if i % 50 == 0 or i == len(files):
            log.info("  loaded %d/%d", i, len(files))
    panel = pd.DataFrame(closes)
    panel.index = pd.to_datetime(panel.index)
    return panel.sort_index()


def build_bullput_winners_event_set() -> list[tuple[str, date]]:
    """Bull_put winners from the live ledger (placed=1, status=closed, pnl>0)."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""
        SELECT symbol, entry_date FROM spread_score_trades
        WHERE status='closed' AND placed=1 AND spread_type='bull_put'
          AND final_pnl > 0
        ORDER BY entry_date, symbol
    """).fetchall()
    conn.close()
    return [(s, date.fromisoformat(d)) for (s, d) in rows]


# ─── Gate A + B: crash-rate analysis ──────────────────────────────────────

def collect_firing_observations(mask: pd.DataFrame, panel: pd.DataFrame,
                                  fwd_ret: pd.DataFrame, spy_fwd: pd.Series,
                                  bull_dates: pd.Index) -> pd.DataFrame:
    """For each (date, ticker) where mask is True on a bull-extended date,
    collect the forward 75d return of the ticker + SPY for the baseline.
    """
    rows = []
    for dt in bull_dates:
        if dt not in mask.index:
            continue
        row_mask = mask.loc[dt]
        if not row_mask.any():
            continue
        if dt not in fwd_ret.index:
            continue
        date_fwd = fwd_ret.loc[dt]
        spy_f = spy_fwd.get(dt)
        if pd.isna(spy_f):
            continue
        for ticker in row_mask[row_mask].index:
            fwd = date_fwd.get(ticker)
            if pd.isna(fwd):
                continue
            rows.append({
                "date": dt, "ticker": ticker,
                "fwd": float(fwd), "spy_fwd": float(spy_f),
                "year": dt.year,
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["date", "ticker", "fwd", "spy_fwd", "year"]
    )


def evaluate_gate_a_b(obs: pd.DataFrame) -> dict:
    """Returns dict with gate_a_ratio, gate_a_pass, per-window walk-forward
    results, gate_b_pass."""
    result = {
        "n_obs": len(obs),
        "n_dates": obs["date"].nunique() if not obs.empty else 0,
    }
    if obs.empty:
        result.update({"gate_a_ratio": float("nan"), "gate_a_pass": False,
                       "gate_b_pass": False, "gate_b_windows": []})
        return result

    spy_dates = pd.Series(obs["spy_fwd"].values, index=obs["date"]).drop_duplicates()
    spy_baseline_crash = (spy_dates < CRASH_THRESHOLD).mean()
    def_crash = (obs["fwd"] < CRASH_THRESHOLD).mean()
    if spy_baseline_crash > 0:
        pooled_ratio = def_crash / spy_baseline_crash
    else:
        pooled_ratio = float("inf")
    result["spy_baseline_crash"] = float(spy_baseline_crash)
    result["def_crash_rate"] = float(def_crash)
    result["gate_a_ratio"] = float(pooled_ratio)
    result["gate_a_pass"] = pooled_ratio >= GATE_A_RATIO

    # Walk-forward
    pass_windows = 0
    windows_detail = []
    for label, yrs in WALK_FORWARD_WINDOWS:
        win = obs[obs["year"].isin(list(yrs))]
        if win.empty:
            windows_detail.append((label, 0, float("nan"), float("nan"), float("nan"), False))
            continue
        win_spy = pd.Series(win["spy_fwd"].values, index=win["date"]).drop_duplicates()
        spy_c = (win_spy < CRASH_THRESHOLD).mean() if not win_spy.empty else float("nan")
        def_c = (win["fwd"] < CRASH_THRESHOLD).mean()
        ratio = def_c / spy_c if spy_c > 0 else float("inf")
        ok = ratio >= GATE_B_RATIO
        if ok:
            pass_windows += 1
        windows_detail.append((label, len(win), float(spy_c), float(def_c), float(ratio), ok))
    result["gate_b_windows"] = windows_detail
    result["gate_b_pass"] = pass_windows >= GATE_B_MIN_WINDOWS
    result["gate_b_passing"] = pass_windows
    return result


# ─── Gate C + D: live-failure / winner event-set match ────────────────────

def evaluate_gates_c_d(mask: pd.DataFrame,
                        losers: list[tuple[str, date]],
                        winners: list[tuple[str, date]]) -> dict:
    """Hit rate on bull_put losers + false-positive rate on winners."""
    def matches_at(symbol: str, dt: date) -> bool | None:
        ts = pd.Timestamp(dt)
        if ts not in mask.index or symbol not in mask.columns:
            return None  # data-unavailable
        return bool(mask.loc[ts, symbol])

    loser_results = [(s, d, matches_at(s, d)) for (s, d) in losers]
    winner_results = [(s, d, matches_at(s, d)) for (s, d) in winners]

    loser_matches = sum(1 for (_, _, m) in loser_results if m is True)
    loser_unavail = sum(1 for (_, _, m) in loser_results if m is None)
    loser_evaluable = len(losers) - loser_unavail

    winner_matches = sum(1 for (_, _, m) in winner_results if m is True)
    winner_unavail = sum(1 for (_, _, m) in winner_results if m is None)
    winner_evaluable = len(winners) - winner_unavail

    fpr = (winner_matches / winner_evaluable) if winner_evaluable > 0 else float("nan")

    return {
        "loser_results": loser_results,
        "loser_matches": loser_matches,
        "loser_evaluable": loser_evaluable,
        "loser_unavailable": loser_unavail,
        "gate_c_pass": loser_matches >= GATE_C_MIN_MATCHES,
        "winner_results_summary": {
            "n_winners": len(winners),
            "matches": winner_matches,
            "evaluable": winner_evaluable,
            "unavailable": winner_unavail,
        },
        "false_positive_rate": fpr,
        "gate_d_pass": (not np.isnan(fpr)) and fpr <= GATE_D_MAX_FPR,
    }


# ─── Pretty-print + reports ──────────────────────────────────────────────

def print_definition_results(name: str, ab: dict, cd: dict):
    print("\n" + "═" * 76)
    print(f"  {name}")
    print("═" * 76)
    print(f"  Observations: N={ab['n_obs']:,} across {ab['n_dates']:,} bull-extended dates")
    print()
    print(f"  --- Gate A: pooled crash-rate ratio ≥ {GATE_A_RATIO}× ---")
    if not np.isnan(ab.get("gate_a_ratio", float('nan'))):
        print(f"    SPY baseline crash rate:  {ab['spy_baseline_crash']:.2%}")
        print(f"    Definition crash rate:    {ab['def_crash_rate']:.2%}")
        r = ab["gate_a_ratio"]
        r_disp = "∞" if np.isinf(r) else f"{r:.2f}×"
        print(f"    Ratio:                    {r_disp}")
    print(f"    Gate A: {'✓ PASS' if ab['gate_a_pass'] else '✗ FAIL'}")
    print()
    print(f"  --- Gate B: walk-forward ratio ≥ {GATE_B_RATIO}× in ≥{GATE_B_MIN_WINDOWS}/4 windows ---")
    for (label, n, spy_c, def_c, ratio, ok) in ab["gate_b_windows"]:
        marker = "✓" if ok else "✗"
        r_disp = "∞" if np.isinf(ratio) else f"{ratio:.2f}×" if not np.isnan(ratio) else "—"
        spy_disp = f"{spy_c:.2%}" if not np.isnan(spy_c) else "—"
        def_disp = f"{def_c:.2%}" if not np.isnan(def_c) else "—"
        print(f"    {label}  N={n:>6,d}  SPY={spy_disp}  def={def_disp}  ratio={r_disp:>7s}  {marker}")
    print(f"    Gate B: {'✓ PASS' if ab['gate_b_pass'] else '✗ FAIL'} "
          f"({ab.get('gate_b_passing', 0)}/4)")
    print()
    print(f"  --- Gate C: bull_put loser hit rate (≥{GATE_C_MIN_MATCHES}/5) ---")
    for (s, d, m) in cd["loser_results"]:
        marker = "✓" if m is True else ("✗" if m is False else "?")
        m_disp = "MATCH" if m is True else ("no match" if m is False else "data N/A")
        print(f"    {s:>5s} @ {d}  {marker}  {m_disp}")
    print(f"    Hits: {cd['loser_matches']} / {cd['loser_evaluable']} evaluable "
          f"({cd['loser_unavailable']} unavailable)")
    print(f"    Gate C: {'✓ PASS' if cd['gate_c_pass'] else '✗ FAIL'}")
    print()
    print(f"  --- Gate D: false-positive rate on bull_put winners ≤ {GATE_D_MAX_FPR:.0%} ---")
    ws = cd["winner_results_summary"]
    print(f"    Total winners: {ws['n_winners']}  evaluable: {ws['evaluable']}  unavail: {ws['unavailable']}")
    if not np.isnan(cd["false_positive_rate"]):
        print(f"    FPR: {cd['false_positive_rate']:.1%}")
    print(f"    Gate D: {'✓ PASS' if cd['gate_d_pass'] else '✗ FAIL'}")
    print()
    overall = ab["gate_a_pass"] and ab["gate_b_pass"] and cd["gate_c_pass"] and cd["gate_d_pass"]
    print(f"  OVERALL: {'✓ ALL GATES PASS — eligible for promotion' if overall else '✗ FAIL — does NOT promote'}")


def write_results_parquet(results: dict[str, dict]):
    """Flatten results dict to one row per (definition, gate) for parquet."""
    rows = []
    for defn, r in results.items():
        ab, cd = r["ab"], r["cd"]
        rows.append({
            "definition": defn,
            "n_obs": ab["n_obs"],
            "n_dates": ab["n_dates"],
            "gate_a_ratio": ab.get("gate_a_ratio"),
            "gate_a_pass": ab["gate_a_pass"],
            "gate_b_pass": ab["gate_b_pass"],
            "gate_b_passing": ab.get("gate_b_passing", 0),
            "loser_matches": cd["loser_matches"],
            "loser_evaluable": cd["loser_evaluable"],
            "gate_c_pass": cd["gate_c_pass"],
            "false_positive_rate": cd["false_positive_rate"],
            "gate_d_pass": cd["gate_d_pass"],
            "overall_pass": all([ab["gate_a_pass"], ab["gate_b_pass"],
                                  cd["gate_c_pass"], cd["gate_d_pass"]]),
        })
    df = pd.DataFrame(rows)
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET, index=False)
    return df


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("h2p2val")

    log.info("Building close panel...")
    panel = build_close_panel()
    log.info("Panel shape: %s", panel.shape)
    if "SPY" not in panel.columns:
        log.error("SPY missing from panel — aborting")
        sys.exit(1)

    spy = panel["SPY"].dropna()
    spy_200dma = spy.rolling(200, min_periods=100).mean()
    spy_ext = spy / spy_200dma - 1.0
    bull_extended = spy_ext >= BULL_EXTENSION_THRESHOLD
    bull_dates = bull_extended[bull_extended].index
    log.info("Bull-extended dates: %d / %d (%.1f%%)",
             int(bull_extended.sum()), len(spy_ext), bull_extended.mean() * 100)

    # Forward 75d returns + SPY forward
    fwd_ret = panel.pct_change(FORWARD_DAYS, fill_method=None).shift(-FORWARD_DAYS)
    spy_fwd = spy.pct_change(FORWARD_DAYS, fill_method=None).shift(-FORWARD_DAYS)

    # Sector ETF 60d returns table
    log.info("Building sector ETF 60d return table...")
    sector_etf_returns = pd.DataFrame(index=panel.index)
    for sector, etf in SECTOR_ETF_MAP.items():
        if etf in panel.columns:
            sector_etf_returns[etf] = panel[etf].pct_change(60, fill_method=None)
        else:
            log.warning("Sector ETF %s missing from panel; sector %s excluded from R2/R5", etf, sector)

    # Sector-of-ticker map (using lib.sector_map for current universe)
    sector_of = {t: get_sector(t) for t in panel.columns}

    # Compute all 5 definitions
    log.info("Computing R1 (rotation 60d)...")
    r1 = compute_r1(panel, spy)
    log.info("Computing R2 (sector-relative 60d)...")
    r2 = compute_r2(panel, sector_etf_returns, sector_of)
    log.info("Computing R3 (stage-2 break)...")
    r3 = compute_r3(panel)
    log.info("Computing R4 (compound W3 ∪ R3)...")
    r4 = compute_r4(panel)
    # R5 is sector-level; evaluated differently
    log.info("Computing R5 (sector-load cohort gate)...")
    r5 = compute_r5({"r1": r1, "r2": r2, "r3": r3}, sector_of)

    log.info("Loading bull_put winners from ledger...")
    winners = build_bullput_winners_event_set()
    log.info("Bull_put winners in event set: %d", len(winners))

    print("=" * 76)
    print("H2 PHASE 2 VALIDATION — Pre-Registered Decision Rule (docs/H2_PHASE2_PREREG.md)")
    print("=" * 76)

    results = {}
    for name, mask in [("R1 — rotation 60d", r1),
                        ("R2 — sector-relative 60d", r2),
                        ("R3 — stage-2 break", r3),
                        ("R4 — compound W3 ∪ R3", r4)]:
        log.info("Evaluating %s ...", name)
        obs = collect_firing_observations(mask, panel, fwd_ret, spy_fwd, bull_dates)
        ab = evaluate_gate_a_b(obs)
        cd = evaluate_gates_c_d(mask, LIVE_FAILURES_BULLPUT, winners)
        results[name] = {"ab": ab, "cd": cd}
        print_definition_results(name, ab, cd)

    # R5 is per-sector. Special handling: report fire rate per sector and overall.
    print("\n" + "═" * 76)
    print("  R5 — sector-load cohort gate (qualitative reporting only)")
    print("═" * 76)
    print(f"  Sectors evaluated: {list(r5.columns)}")
    for sec in r5.columns:
        fire_dates = r5.index[r5[sec]]
        recent = sum(1 for d in fire_dates if d >= pd.Timestamp("2024-01-01"))
        print(f"    {sec:>22s}  fires: {int(r5[sec].sum()):>5,d}  "
              f"(since 2024: {recent})")
    # R5 promotion only attempted if all R1-R4 fail per pre-reg
    promoted_anyone = any(
        r["ab"]["gate_a_pass"] and r["ab"]["gate_b_pass"]
        and r["cd"]["gate_c_pass"] and r["cd"]["gate_d_pass"]
        for r in results.values()
    )
    print(f"  R5 detailed validation: {'SKIPPED (a per-name definition passed)' if promoted_anyone else 'WOULD RUN (all per-name failed)'}")

    df = write_results_parquet(results)
    log.info("Wrote results to %s", OUT_PARQUET)

    print("\n" + "═" * 76)
    print("  DECISION (per H2_PHASE2_PREREG.md §6)")
    print("═" * 76)
    passing = df[df["overall_pass"]]
    if passing.empty:
        print("  No definition passes all gates.")
        print("  → R4 (compound) and R5 (cohort) would be evaluated for promotion.")
        print("  → If they also fail, the conceptual H2 case is rejected at the operational")
        print("    level for two consecutive phases. No filter integrated.")
    else:
        # Tiebreak by hit_rate × (1 - FPR)
        passing = passing.copy()
        passing["hit_rate"] = passing["loser_matches"] / passing["loser_evaluable"]
        passing["score"] = passing["hit_rate"] * (1 - passing["false_positive_rate"].fillna(0))
        winner = passing.sort_values("score", ascending=False).iloc[0]
        print(f"  ✓ {len(passing)} definition(s) pass all gates.")
        print(f"  → PROMOTE: {winner['definition']}")
        print(f"    hit_rate={winner['hit_rate']:.1%}  fpr={winner['false_positive_rate']:.1%}  "
              f"score={winner['score']:.3f}")
        print()
        print(f"  Integration: lib/h2_weakness.py + gate_config.py + cycle_qualifier.py")
        print(f"  See pre-reg §7 for full integration plan.")


if __name__ == "__main__":
    main()
