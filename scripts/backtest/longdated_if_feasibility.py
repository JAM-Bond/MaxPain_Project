"""Study A, Gate 1 — Long-dated inverted-fly DTE feasibility frontier.

Per LONGDATED_IF_VOLUME_SIGNAL_PREREG.md (sealed 2026-06-03).

Question: across a DTE ladder, can the 4-leg inverted fly (long ATM call+put,
short OTM wings) actually be PUT ON — i.e. is the combined bid-ask width small
relative to the debit? Burry's warning is that long-dated OTM legs go illiquid
and wide. Output DTE* = the LONGEST maturity where the structure clears the bar.

Feasibility bar (sealed): combined natural-worst bid-ask width <= 40% of the
mid debit, on >= 70% of attempted entries. Six-month floor: hope DTE* >= 180.

Conservative by design: an "attempt" = a date where the ATM strike quotes and
wing strikes exist on the grid; if a wing leg has no real bid (deep-OTM
illiquidity) the structure can't be sold and the attempt FAILS. That is exactly
the wide/illiquid-leg risk we are measuring.

Reads ORATS data/orats/by_ticker/<SYM>.parquet directly. No new data.
Writes data/profile/longdated_if_feasibility.parquet + prints the frontier table.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data" / "orats" / "by_ticker"
OUT = ROOT / "data" / "profile" / "longdated_if_feasibility.parquet"

# Universe = IF cohort (gate_config COHORT_INVERTED_FLY_PAIR + _SINGLE, sourced
# 2026-06-03) + earnings IF carve-out PLTR + mania tag. Deduped.
IF_UNIVERSE = sorted(set([
    # PAIR
    "SPX", "SPY", "QQQ", "GLD", "EFA", "WMT", "NEM", "XOM",
    "PG", "WFC", "GE", "INTC", "BABA",
    # SINGLE
    "TSLA", "AMD", "NVDA", "CAR", "AMZN", "GOOGL", "SCCO", "GOLD",
    "CLF", "ISRG", "XLK", "PEP", "STX", "LRCX", "MCD", "JNJ", "PDD",
    "AG", "DELL", "AFRM",
    # earnings carve-out + mania
    "PLTR", "AVGO",
]))

DTE_RUNGS = [90, 120, 180, 270, 290, 365]
DTE_TOL = 30          # accept an expiry within +-30 days of the rung
WING_PCTS = [0.05, 0.10, 0.15]
WIDTH_BAR = 0.40      # combined bid-ask width <= 40% of debit
PASS_RATE_BAR = 0.70  # >= 70% of attempts must clear the bar
SIX_MONTH_FLOOR = 180

COLS = ["trade_date", "yte", "strike", "stkPx",
        "cBidPx", "cAskPx", "pBidPx", "pAskPx"]


def _valid(bid, ask):
    return pd.notna(bid) and pd.notna(ask) and bid > 0 and ask >= bid


def _monthly_entry_dates(dates: pd.Series) -> list[pd.Timestamp]:
    """First available trade_date in each calendar month."""
    s = pd.Series(sorted(dates.unique()))
    s = pd.to_datetime(s)
    return list(s.groupby([s.dt.year, s.dt.month]).min())


def _pick_expiry(day_chain: pd.DataFrame, rung: int):
    """Return the sub-chain for the expiry whose DTE is closest to rung (+-TOL)."""
    dte = day_chain["yte"] * 365.0
    # group by expiry via its dte (rounded) — expiries are discrete
    uniq = day_chain.assign(_dte=dte.round().astype(int))
    cand = uniq[(uniq["_dte"] - rung).abs() <= DTE_TOL]
    if cand.empty:
        return None, None
    best_dte = cand.iloc[(cand["_dte"] - rung).abs().argmin()]["_dte"]
    return uniq[uniq["_dte"] == best_dte], int(best_dte)


def _nearest_strike(chain: pd.DataFrame, target: float, above: bool, ref: float):
    side = chain[chain["strike"] > ref] if above else chain[chain["strike"] < ref]
    if side.empty:
        return None
    return side.iloc[(side["strike"] - target).abs().argmin()]


def _assess_entry(day_chain: pd.DataFrame, rung: int, wing_pct: float) -> dict | None:
    sub, real_dte = _pick_expiry(day_chain, rung)
    if sub is None or sub.empty:
        return None
    sub = sub.dropna(subset=["strike", "stkPx"])
    if sub.empty:
        return None
    spot = float(sub["stkPx"].iloc[0])
    if spot <= 0:
        return None
    # ATM strike closest to spot, must have valid call+put quotes
    atm = sub.iloc[(sub["strike"] - spot).abs().argmin()]
    if not (_valid(atm["cBidPx"], atm["cAskPx"]) and _valid(atm["pBidPx"], atm["pAskPx"])):
        return None  # not even an attempt
    atm_k = float(atm["strike"])
    wing = wing_pct * spot
    wc = _nearest_strike(sub, atm_k + wing, above=True, ref=atm_k)
    wp = _nearest_strike(sub, atm_k - wing, above=False, ref=atm_k)
    if wc is None or wp is None:
        return None  # wing strikes not on the grid → not an attempt at this wing
    # This IS an attempt. Now: is it tradeable?
    rec = {"rung": rung, "real_dte": real_dte, "wing_pct": wing_pct,
           "spot": spot, "attempt": 1, "pass": 0,
           "debit": np.nan, "width": np.nan, "ratio": np.nan}
    # wing legs must have real bids to be SOLD; else structure can't be put on → fail
    if not (_valid(wc["cBidPx"], wc["cAskPx"]) and _valid(wp["pBidPx"], wp["pAskPx"])):
        return rec  # attempt, fail (illiquid wing)
    ac_mid = (atm["cBidPx"] + atm["cAskPx"]) / 2
    ap_mid = (atm["pBidPx"] + atm["pAskPx"]) / 2
    wc_mid = (wc["cBidPx"] + wc["cAskPx"]) / 2
    wp_mid = (wp["pBidPx"] + wp["pAskPx"]) / 2
    debit = (ac_mid + ap_mid) - (wc_mid + wp_mid)
    if debit <= 0:
        return rec  # attempt, fail (no debit — degenerate)
    width = ((atm["cAskPx"] - atm["cBidPx"]) + (atm["pAskPx"] - atm["pBidPx"])
             + (wc["cAskPx"] - wc["cBidPx"]) + (wp["pAskPx"] - wp["pBidPx"]))
    ratio = width / debit
    rec.update(debit=float(debit), width=float(width), ratio=float(ratio),
               **{"pass": int(ratio <= WIDTH_BAR)})
    return rec


def run():
    rows = []
    avail = {p.stem for p in BY_TICKER.glob("*.parquet")}
    universe = [s for s in IF_UNIVERSE if s in avail]
    missing = [s for s in IF_UNIVERSE if s not in avail]
    if missing:
        print(f"  (not in archive, skipped: {', '.join(missing)})")
    print(f"  universe: {len(universe)} names\n")
    for i, sym in enumerate(universe, 1):
        df = pd.read_parquet(BY_TICKER / f"{sym}.parquet", columns=COLS)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        entries = _monthly_entry_dates(df["trade_date"])
        by_day = {d: g for d, g in df.groupby("trade_date")}
        n_sym = 0
        for d in entries:
            day_chain = by_day.get(d)
            if day_chain is None:
                continue
            for rung in DTE_RUNGS:
                for wp in WING_PCTS:
                    rec = _assess_entry(day_chain, rung, wp)
                    if rec is None:
                        continue
                    rec["ticker"] = sym
                    rec["entry_date"] = d
                    rows.append(rec)
                    n_sym += 1
        print(f"  [{i:2d}/{len(universe)}] {sym:5s} entries_assessed={n_sym}")
    res = pd.DataFrame(rows)
    res.to_parquet(OUT, index=False)
    print(f"\n  wrote {len(res):,} rows -> {OUT}\n")
    _report(res)


def _report(res: pd.DataFrame):
    print("=" * 78)
    print("  DTE FEASIBILITY FRONTIER  (bar: width/debit <= 0.40 on >= 70% of attempts)")
    print("=" * 78)
    for wp in WING_PCTS:
        print(f"\n  Wing = {wp*100:.0f}% of spot")
        print(f"  {'rung':>5} {'~realDTE':>9} {'attempts':>9} {'pass%':>7} "
              f"{'med ratio':>10} {'med debit':>10}")
        sub = res[res["wing_pct"] == wp]
        for rung in DTE_RUNGS:
            r = sub[sub["rung"] == rung]
            if r.empty:
                print(f"  {rung:>5} {'—':>9} {0:>9}")
                continue
            pr = r["pass"].mean()
            med_dte = r["real_dte"].median()
            med_ratio = r["ratio"].median()
            med_debit = r["debit"].median()
            flag = "  <-- clears bar" if pr >= PASS_RATE_BAR else ""
            print(f"  {rung:>5} {med_dte:>9.0f} {len(r):>9} {pr*100:>6.0f}% "
                  f"{med_ratio:>10.2f} {med_debit:>10.2f}{flag}")
    # DTE* = longest rung clearing the bar at the most-feasible wing
    print("\n  " + "-" * 74)
    best = {}
    for wp in WING_PCTS:
        sub = res[res["wing_pct"] == wp]
        passing = [rung for rung in DTE_RUNGS
                   if not sub[sub["rung"] == rung].empty
                   and sub[sub["rung"] == rung]["pass"].mean() >= PASS_RATE_BAR]
        best[wp] = max(passing) if passing else None
    overall = [v for v in best.values() if v is not None]
    dte_star = max(overall) if overall else None
    for wp in WING_PCTS:
        print(f"  longest feasible DTE @ {wp*100:.0f}% wing: "
              f"{best[wp] if best[wp] else 'NONE'}")
    print(f"\n  DTE* (longest feasible, any wing) = "
          f"{dte_star if dte_star else 'NONE — STOP'}")
    if dte_star is None:
        print("  VERDICT: no rung >= 90 DTE clears the bar → long-dated IF not tradeable.")
    elif dte_star < SIX_MONTH_FLOOR:
        print(f"  VERDICT: DTE* {dte_star} < 180 → LIQUIDITY-CONSTRAINED. "
              f"Run structure study at {dte_star}, flag the limit.")
    else:
        print(f"  VERDICT: DTE* {dte_star} >= 180 → six-month floor cleared. "
              f"Run structure study at DTE*.")
    print("=" * 78)


if __name__ == "__main__":
    run()
