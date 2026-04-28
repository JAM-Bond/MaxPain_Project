"""MP test suite Phase 2 — MP-anchored vs ATM-anchored structure backtest.

For each reliable pinner × monthly OpEx cycle:
  - Entry at T-5 (Monday of OpEx week, or latest trading day on/before opex-5)
  - Compute MP from T-5 OI snapshot (the entry-day max pain, not T-1)
  - Find ATM strike (closest to spot at T-5)
  - Build multiple structures and settle at expiry intrinsic

Structures tested (pre-registered):
  1. iron_fly_atm     — conventional short fly centered on ATM (baseline)
  2. iron_fly_mp      — short fly centered on MP strike (the MP bet)
  3. iron_condor_mp   — short condor straddling MP (±1 spacing shorts, ±2 spacing wings)
  4. butterfly_mp     — long call butterfly centered on MP (debit, max profit at MP)

Pricing: slip=0.25 (consistent with Track A sensitivity studies).

No mid-cycle management — hold to expiry. Short duration (5-day trade) so theta dominates.

Output: data/profile/mp_phase2_results.parquet (per-cycle) and per-ticker summary.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
OUT_DIR = ROOT / "data/profile"

# Pre-registered pinner cohort from Phase 1
TIER1 = ["BKLN", "HYG", "JNK", "TLT"]
TIER2 = ["SPX", "SPY", "DIA", "QQQ", "IWM"]
TIER3 = ["XLU", "XLV", "IYR", "GLD", "VZ", "KO", "PG", "WMT", "EFA", "VNQ"]
COHORT = TIER1 + TIER2 + TIER3  # 19 names

SLIP_FRAC = 0.25


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    offset = (4 - d.weekday()) % 7
    return d + timedelta(days=offset + 14)


def monthly_opex(sy, ey):
    return [third_friday(y, m) for y in range(sy, ey + 1) for m in range(1, 13)]


def parse_exp(s):
    try:
        p = s.split("/")
        return pd.Timestamp(year=int(p[2]), month=int(p[0]), day=int(p[1]))
    except Exception:
        return None


def compute_max_pain(chain: pd.DataFrame) -> float | None:
    c = chain.dropna(subset=["strike", "cOi", "pOi"])
    if c.empty:
        return None
    strikes = c["strike"].values
    call_oi = c["cOi"].values
    put_oi  = c["pOi"].values
    best_K, best_pain = None, None
    for K in strikes:
        total = (call_oi * np.maximum(0.0, K - strikes)).sum() + \
                (put_oi  * np.maximum(0.0, strikes - K)).sum()
        if best_pain is None or total < best_pain:
            best_pain = total
            best_K = float(K)
    return best_K


def atm_strike(chain: pd.DataFrame, spot: float) -> float | None:
    c = chain.dropna(subset=["strike"])
    if c.empty:
        return None
    idx = (c["strike"] - spot).abs().idxmin()
    return float(c.loc[idx, "strike"])


def nth_strike_from(chain: pd.DataFrame, reference: float, n: int) -> float | None:
    """Return the strike that is n positions up (n>0) or down (n<0) from reference in the grid."""
    strikes = sorted(chain["strike"].dropna().unique())
    try:
        idx = strikes.index(reference)
    except ValueError:
        # reference not in grid — find closest
        arr = np.array(strikes)
        idx = int(np.argmin(np.abs(arr - reference)))
    target_idx = idx + n
    if 0 <= target_idx < len(strikes):
        return float(strikes[target_idx])
    return None


def price_sell(bid, ask, frac=SLIP_FRAC):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 - frac * (ask - bid) / 2


def price_buy(bid, ask, frac=SLIP_FRAC):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 + frac * (ask - bid) / 2


def get_row(chain: pd.DataFrame, K: float) -> pd.Series | None:
    rows = chain[chain["strike"] == K]
    if rows.empty:
        return None
    return rows.iloc[0]


def build_iron_fly(chain: pd.DataFrame, center_K: float) -> dict | None:
    """Short call+put at center_K, long wings at ±1 strike spacing."""
    up_K = nth_strike_from(chain, center_K, +1)
    dn_K = nth_strike_from(chain, center_K, -1)
    if up_K is None or dn_K is None:
        return None
    ctr = get_row(chain, center_K)
    up = get_row(chain, up_K)
    dn = get_row(chain, dn_K)
    if ctr is None or up is None or dn is None:
        return None
    sc = price_sell(ctr["cBidPx"], ctr["cAskPx"])
    sp = price_sell(ctr["pBidPx"], ctr["pAskPx"])
    lc = price_buy(up["cBidPx"], up["cAskPx"])
    lp = price_buy(dn["pBidPx"], dn["pAskPx"])
    if None in (sc, sp, lc, lp):
        return None
    credit = sc + sp - lc - lp
    if credit <= 0:
        return None
    return {
        "center_K": center_K, "up_K": up_K, "dn_K": dn_K,
        "entry_credit": credit,
        "legs": [("short", "call", center_K), ("short", "put", center_K),
                 ("long", "call", up_K), ("long", "put", dn_K)],
    }


def build_iron_condor(chain: pd.DataFrame, center_K: float) -> dict | None:
    """Short call at +1, short put at -1, long wings at ±2 from center."""
    up1 = nth_strike_from(chain, center_K, +1)
    dn1 = nth_strike_from(chain, center_K, -1)
    up2 = nth_strike_from(chain, center_K, +2)
    dn2 = nth_strike_from(chain, center_K, -2)
    if any(x is None for x in [up1, dn1, up2, dn2]):
        return None
    r_up1 = get_row(chain, up1); r_dn1 = get_row(chain, dn1)
    r_up2 = get_row(chain, up2); r_dn2 = get_row(chain, dn2)
    if any(r is None for r in [r_up1, r_dn1, r_up2, r_dn2]):
        return None
    sc = price_sell(r_up1["cBidPx"], r_up1["cAskPx"])
    sp = price_sell(r_dn1["pBidPx"], r_dn1["pAskPx"])
    lc = price_buy(r_up2["cBidPx"], r_up2["cAskPx"])
    lp = price_buy(r_dn2["pBidPx"], r_dn2["pAskPx"])
    if None in (sc, sp, lc, lp):
        return None
    credit = sc + sp - lc - lp
    if credit <= 0:
        return None
    return {
        "center_K": center_K, "short_call_K": up1, "short_put_K": dn1,
        "long_call_K": up2, "long_put_K": dn2,
        "entry_credit": credit,
        "legs": [("short", "call", up1), ("short", "put", dn1),
                 ("long", "call", up2), ("long", "put", dn2)],
    }


def build_long_butterfly(chain: pd.DataFrame, center_K: float) -> dict | None:
    """Long call fly: +1C at K-1, -2C at K, +1C at K+1 (debit; max at K)."""
    up_K = nth_strike_from(chain, center_K, +1)
    dn_K = nth_strike_from(chain, center_K, -1)
    if up_K is None or dn_K is None:
        return None
    ctr = get_row(chain, center_K)
    up = get_row(chain, up_K)
    dn = get_row(chain, dn_K)
    if ctr is None or up is None or dn is None:
        return None
    # pay debit: long K-1 call, short 2×K call, long K+1 call
    l_dn = price_buy(dn["cBidPx"], dn["cAskPx"])
    s_ctr = price_sell(ctr["cBidPx"], ctr["cAskPx"])
    l_up = price_buy(up["cBidPx"], up["cAskPx"])
    if None in (l_dn, s_ctr, l_up):
        return None
    debit = l_dn + l_up - 2 * s_ctr
    if debit <= 0:
        return None  # weird market state
    return {
        "center_K": center_K, "up_K": up_K, "dn_K": dn_K,
        "entry_credit": -debit,  # negative = debit paid
        "legs": [("long", "call", dn_K), ("short", "call", center_K),
                 ("short", "call", center_K), ("long", "call", up_K)],
    }


def intrinsic_leg(side: str, opt_type: str, strike: float, close: float) -> float:
    if opt_type == "call":
        v = max(0.0, close - strike)
    else:
        v = max(0.0, strike - close)
    return v if side == "long" else -v


def settle_pnl(structure: dict, close: float) -> float:
    total_intrinsic = sum(intrinsic_leg(s, t, k, close) for s, t, k in structure["legs"])
    return structure["entry_credit"] + total_intrinsic


def run_ticker(ticker: str, opex_list: list[pd.Timestamp]) -> list[dict]:
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path, columns=["trade_date","expirDate","strike","stkPx","cOi","pOi","cBidPx","cAskPx","pBidPx","pAskPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["exp_dt"] = df["expirDate"].map(parse_exp)

    # Build opex→exp_str mapping
    exp_map = {}
    for s in df["expirDate"].unique():
        d = parse_exp(s)
        if d is None:
            continue
        for opex in opex_list:
            if abs((d - opex).days) <= 1:
                exp_map[opex] = s
                break

    results = []
    for opex, exp_str in exp_map.items():
        sub = df[df["expirDate"] == exp_str]
        if sub.empty:
            continue
        # T-5 entry: latest trade_date on or before (opex - 5 calendar days)
        target = opex - pd.Timedelta(days=5)
        pre = sub[sub["trade_date"] <= target]
        if pre.empty:
            continue
        t_entry = pre["trade_date"].max()
        chain_entry = pre[pre["trade_date"] == t_entry].copy()
        if chain_entry.empty:
            continue
        mp = compute_max_pain(chain_entry)
        if mp is None:
            continue
        spot_entry = float(chain_entry["stkPx"].iloc[0])
        atm = atm_strike(chain_entry, spot_entry)
        if atm is None:
            continue

        # Final close at expiry
        final = df[df["trade_date"] == opex]
        if final.empty:
            continue
        close = float(final["stkPx"].iloc[0])

        # Build structures
        structs = {
            "iron_fly_atm": build_iron_fly(chain_entry, atm),
            "iron_fly_mp":  build_iron_fly(chain_entry, mp),
            "iron_condor_mp": build_iron_condor(chain_entry, mp),
            "butterfly_mp":   build_long_butterfly(chain_entry, mp),
        }
        for name, s in structs.items():
            if s is None:
                continue
            pnl = settle_pnl(s, close)
            results.append({
                "ticker": ticker, "opex": opex, "t_entry": t_entry,
                "structure": name,
                "spot_entry": spot_entry, "spot_close": close,
                "atm_k": atm, "mp_k": mp, "center_k": s["center_K"],
                "entry_credit": s["entry_credit"], "pnl": pnl,
                "mp_minus_atm": mp - atm,
                "close_minus_mp": close - mp,
                "close_pct_off_mp": (close - mp) / close,
            })
    return results


def main() -> None:
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Phase 2 cohort: {len(COHORT)} tickers — {', '.join(COHORT)}")

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        print(f"  [{i}/{len(COHORT)}] {t}: {len(rows)} cycle-structures")
    df = pd.DataFrame(all_rows)
    print(f"\nTotal rows: {len(df):,}")
    if df.empty:
        print("No results.")
        return

    # Per-ticker × structure summary
    g = df.groupby(["structure", "ticker"]).agg(
        n=("pnl", "count"),
        mean=("pnl", "mean"),
        median=("pnl", "median"),
        win=("pnl", lambda s: (s > 0).mean()),
        total=("pnl", "sum"),
        worst=("pnl", "min"),
        best=("pnl", "max"),
    ).reset_index()
    # Order tickers by Phase 1 tier
    tier_order = {t: 0 for t in TIER1}
    tier_order.update({t: 1 for t in TIER2})
    tier_order.update({t: 2 for t in TIER3})
    g["tier"] = g["ticker"].map(tier_order)

    print("\n═══ Per-ticker × structure (mean P&L, $) ═══")
    pivot_mean = g.pivot_table(index=["tier","ticker"], columns="structure", values="mean", aggfunc="first").round(3)
    print(pivot_mean.to_string())
    print()
    print("═══ Per-ticker × structure (median P&L, $) ═══")
    pivot_med = g.pivot_table(index=["tier","ticker"], columns="structure", values="median", aggfunc="first").round(3)
    print(pivot_med.to_string())
    print()
    print("═══ Per-ticker × structure (win rate) ═══")
    pivot_win = g.pivot_table(index=["tier","ticker"], columns="structure", values="win", aggfunc="first").round(3)
    print(pivot_win.to_string())
    print()
    print("═══ Per-ticker × structure (N cycles) ═══")
    pivot_n = g.pivot_table(index=["tier","ticker"], columns="structure", values="n", aggfunc="first")
    print(pivot_n.to_string())
    print()

    # ATM-fly vs MP-fly head-to-head
    print("═══ MP-fly vs ATM-fly head-to-head (by tier) ═══")
    atm = df[df["structure"] == "iron_fly_atm"].groupby("ticker")["pnl"].mean()
    mpf = df[df["structure"] == "iron_fly_mp"].groupby("ticker")["pnl"].mean()
    h2h = pd.DataFrame({"atm_fly_mean": atm, "mp_fly_mean": mpf}).reset_index()
    h2h["mp_lift"] = h2h["mp_fly_mean"] - h2h["atm_fly_mean"]
    h2h["tier"] = h2h["ticker"].map(tier_order)
    h2h = h2h.sort_values(["tier","mp_lift"], ascending=[True, False])
    print(h2h.to_string(index=False, float_format=lambda x: f"{x:+.3f}" if isinstance(x, float) else str(x)))
    print()

    # Aggregate cross-structure winner
    print("═══ Cross-structure winner per ticker (by mean P&L) ═══")
    best_struct = g.loc[g.groupby("ticker")["mean"].idxmax()][["tier","ticker","structure","mean","median","win","n"]]
    best_struct = best_struct.sort_values(["tier","mean"], ascending=[True, False])
    print(best_struct.to_string(index=False, float_format=lambda x: f"{x:+.3f}" if isinstance(x, float) else str(x)))

    # Save
    df.to_parquet(OUT_DIR / "mp_phase2_results.parquet", index=False)
    g.to_parquet(OUT_DIR / "mp_phase2_by_ticker_structure.parquet", index=False)
    print()
    print("wrote: data/profile/mp_phase2_results.parquet + mp_phase2_by_ticker_structure.parquet")


if __name__ == "__main__":
    main()
