"""EV-rank PREVIEW for today's committed ZEBRA slate (per docs/EV_RANK_TIEBREAKER_SPEC.md).

Sketch of the ZEBRA branch of the (unbuilt) EV-rank tiebreaker, run against the live
construction path so we can SEE the rank order it would produce. NOT wired into the
qualifier — read-only preview.

ZEBRA rank metric (spec §"The metric"):
  - defined risk = debit; reward uncapped; no clean single-delta POP.
  - "stock-equivalent delta per dollar at risk" = net_delta * spot / debit  (HIGHER = better)
  - hard pre-gate: extrinsic_cushion >= 0
NOTE: structures.py stores notes["capital_efficiency"] = debit/spot (a COST ratio, lower=better),
which is the INVERSE of the spec's "higher=better" phrasing. We rank by the spec's intent
(exposure per dollar at risk) and surface debit/spot alongside. Flag for the real build.

Usage: python3.11 scripts/research/zebra_ev_rank_preview.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "backtest"))  # structures' `from legs import ...`

from lib.db import DB_PATH                                  # noqa: E402
from lib.schwab_options import fetch_chain_with_greeks      # noqa: E402
from scripts.backtest.structures import open_zebra          # noqa: E402

VERDICT_RANK = {"GO": 0, "DOWNSIZE": 1}


def committed(run_date=None):
    """ZEBRA GO/DOWNSIZE for a run. Defaults to the latest run that ACTUALLY had
    committed ZEBRA candidates (skips non-window days where the slate is empty).
    Pass a run_date (YYYY-MM-DD) as argv[1] to target a specific window."""
    conn = sqlite3.connect(DB_PATH)
    if run_date is None:
        run_date = conn.execute(
            "SELECT MAX(run_date) FROM cycle_qualifier_runs "
            "WHERE structure LIKE 'zebra%' AND verdict IN ('GO','DOWNSIZE')").fetchone()[0]
    rows = conn.execute(
        "SELECT symbol, structure, verdict, opex FROM cycle_qualifier_runs "
        "WHERE run_date=? AND structure LIKE 'zebra%' AND verdict IN ('GO','DOWNSIZE')",
        (run_date,)).fetchall()
    conn.close()
    return run_date, rows


def score(symbol, expiry):
    chain, spot = fetch_chain_with_greeks(symbol, expiry)
    if chain is None or chain.empty or not spot:
        return {"err": "no chain"}
    pos = open_zebra(chain, pd.Timestamp.today(), pd.Timestamp(expiry))
    if pos is None:
        return {"err": "no zebra structure"}
    n = pos.notes
    debit = n.get("debit")
    cushion = n.get("extrinsic_cushion")
    delta = n.get("entry_delta")
    if not debit or debit <= 0:
        return {"err": "bad debit"}
    return {
        "spot": spot, "debit": debit, "cost_ratio": debit / spot,   # = stored capital_efficiency
        "delta": delta, "cushion": cushion,
        "exposure_per_risk": (delta * spot / debit) if delta else None,  # spec intent, higher=better
        "passes_gate": cushion is not None and cushion >= 0,
        "err": None,
    }


def main():
    run_date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    latest, rows = committed(run_date_arg)
    print(f"ZEBRA EV-rank PREVIEW — qualifier run {latest} | {len(rows)} committed names")
    print("metric: stock-equiv delta per $ at risk = net_delta·spot/debit (higher=better); gate: extrinsic_cushion≥0")
    print("=" * 100)

    scored = []
    for sym, structure, verdict, opex in rows:
        s = score(sym, opex)
        s.update(sym=sym, verdict=verdict, opex=opex)
        scored.append(s)
        tag = s["err"] or ("PASS" if s["passes_gate"] else "GATE-FAIL")
        print(f"  {sym:<6} {verdict:<9} {opex}  {tag}")

    ok = [s for s in scored if not s["err"] and s["exposure_per_risk"] is not None]
    bad = [s for s in scored if s["err"]]

    # spec order: (verdict_rank, -exposure_per_risk, symbol); gate-fails sort after passers
    ok.sort(key=lambda s: (VERDICT_RANK.get(s["verdict"], 9),
                           0 if s["passes_gate"] else 1,
                           -s["exposure_per_risk"], s["sym"]))

    print("\n" + "=" * 100)
    print(f"{'#':<4}{'SYM':<6}{'VERD':<9}{'spot':>8}{'debit':>8}{'debit/spot':>11}"
          f"{'netΔ':>7}{'exp/$risk':>11}{'cushion':>10}")
    print("-" * 100)
    rank = 0
    last_verdict = None
    for s in ok:
        if s["verdict"] != last_verdict:
            rank = 0; last_verdict = s["verdict"]
            print(f"  -- {s['verdict']} --")
        rank += 1
        gate = "" if s["passes_gate"] else "  ⚠GATE-FAIL"
        print(f"{rank:<4}{s['sym']:<6}{s['verdict']:<9}{s['spot']:>8.2f}{s['debit']:>8.2f}"
              f"{s['cost_ratio']:>10.2f} {s['delta']:>6.2f}{s['exposure_per_risk']:>11.2f}"
              f"{s['cushion']:>+9.2f}{gate}")
    if bad:
        print("-" * 100)
        print("fail-open (kept at alphabetical in real pipeline): "
              + ", ".join(f"{s['sym']}({s['err']})" for s in bad))
    print("-" * 100)
    print("exp/$risk = net_delta·spot/debit  (how much stock-equivalent exposure each $ of defined risk buys)")


if __name__ == "__main__":
    main()
