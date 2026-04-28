"""MP Phase 2c — Credit verticals in the 5-day OpEx window.

After Phase 2 showed MP-anchored flies don't profit, test whether short credit
spreads at T-5 capture edge from the short 5-day theta alone, optionally using
MP position as directional context.

Structures (pre-registered):
  1. bull_put_30d   — short 30Δ put, long 1 strike lower (TT canonical)
  2. bear_call_30d  — short 30Δ call, long 1 strike higher
  3. bull_put_mp    — short put at strike nearest MP, long 1 strike lower.
                      Only enter when spot ≥ MP (betting price doesn't fall through MP).
  4. bear_call_mp   — short call at strike nearest MP, long 1 strike higher.
                      Only enter when spot ≤ MP (betting price doesn't rise through MP).

Cohort: Phase 1 pinners (19 names). Entry T-5, hold to expiry. Slip=0.25.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
OUT_DIR = ROOT / "data/profile"

TIER1 = ["BKLN", "HYG", "JNK", "TLT"]
TIER2 = ["SPX", "SPY", "DIA", "QQQ", "IWM"]
TIER3 = ["XLU", "XLV", "IYR", "GLD", "VZ", "KO", "PG", "WMT", "EFA", "VNQ"]
COHORT = TIER1 + TIER2 + TIER3

SLIP_FRAC = 0.25


def third_friday(year, month):
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


def compute_max_pain(chain):
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


def nth_strike_from(chain, reference, n):
    strikes = sorted(chain["strike"].dropna().unique())
    arr = np.array(strikes)
    idx = int(np.argmin(np.abs(arr - reference)))
    target_idx = idx + n
    if 0 <= target_idx < len(strikes):
        return float(strikes[target_idx])
    return None


def get_row(chain, K):
    rows = chain[chain["strike"] == K]
    if rows.empty:
        return None
    return rows.iloc[0]


def price_sell(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 - SLIP_FRAC * (ask - bid) / 2


def price_buy(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 + SLIP_FRAC * (ask - bid) / 2


def select_short_put_30d(chain):
    """Select the put whose put-delta is closest to -0.30 (call delta closest to 0.70)."""
    c = chain.dropna(subset=["delta", "strike", "pBidPx", "pAskPx"])
    if c.empty:
        return None
    idx = (c["delta"] - 0.70).abs().idxmin()
    row = c.loc[idx]
    if abs(row["delta"] - 0.70) > 0.08:
        return None
    return row


def select_short_call_30d(chain):
    c = chain.dropna(subset=["delta", "strike", "cBidPx", "cAskPx"])
    if c.empty:
        return None
    idx = (c["delta"] - 0.30).abs().idxmin()
    row = c.loc[idx]
    if abs(row["delta"] - 0.30) > 0.08:
        return None
    return row


def build_bull_put(chain, short_put_K):
    sp_row = get_row(chain, short_put_K)
    long_K = nth_strike_from(chain, short_put_K, -1)
    if long_K is None:
        return None
    lp_row = get_row(chain, long_K)
    if sp_row is None or lp_row is None:
        return None
    sp = price_sell(sp_row["pBidPx"], sp_row["pAskPx"])
    lp = price_buy(lp_row["pBidPx"], lp_row["pAskPx"])
    if sp is None or lp is None:
        return None
    credit = sp - lp
    if credit <= 0:
        return None
    return {
        "entry_credit": credit,
        "short_K": short_put_K, "long_K": long_K,
        "wing_width": short_put_K - long_K,
        "legs": [("short", "put", short_put_K), ("long", "put", long_K)],
    }


def build_bear_call(chain, short_call_K):
    sc_row = get_row(chain, short_call_K)
    long_K = nth_strike_from(chain, short_call_K, +1)
    if long_K is None:
        return None
    lc_row = get_row(chain, long_K)
    if sc_row is None or lc_row is None:
        return None
    sc = price_sell(sc_row["cBidPx"], sc_row["cAskPx"])
    lc = price_buy(lc_row["cBidPx"], lc_row["cAskPx"])
    if sc is None or lc is None:
        return None
    credit = sc - lc
    if credit <= 0:
        return None
    return {
        "entry_credit": credit,
        "short_K": short_call_K, "long_K": long_K,
        "wing_width": long_K - short_call_K,
        "legs": [("short", "call", short_call_K), ("long", "call", long_K)],
    }


def intrinsic_leg(side, opt_type, strike, close):
    v = max(0.0, close - strike) if opt_type == "call" else max(0.0, strike - close)
    return v if side == "long" else -v


def settle_pnl(structure, close):
    intrinsic = sum(intrinsic_leg(s, t, k, close) for s, t, k in structure["legs"])
    return structure["entry_credit"] + intrinsic


def run_ticker(ticker, opex_list):
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path, columns=["trade_date","expirDate","strike","stkPx","delta","cOi","pOi","cBidPx","cAskPx","pBidPx","pAskPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["exp_dt"] = df["expirDate"].map(parse_exp)

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
        target = opex - pd.Timedelta(days=5)
        pre = sub[sub["trade_date"] <= target]
        if pre.empty:
            continue
        t_entry = pre["trade_date"].max()
        chain = pre[pre["trade_date"] == t_entry].copy()
        if chain.empty:
            continue
        mp = compute_max_pain(chain)
        if mp is None:
            continue
        spot = float(chain["stkPx"].iloc[0])

        final = df[df["trade_date"] == opex]
        if final.empty:
            continue
        close = float(final["stkPx"].iloc[0])

        base = {
            "ticker": ticker, "opex": opex, "t_entry": t_entry,
            "spot_entry": spot, "spot_close": close,
            "mp_k": mp, "spot_minus_mp": spot - mp,
        }

        # ── Structure 1: bull_put_30d ──
        sp30 = select_short_put_30d(chain)
        if sp30 is not None:
            bp = build_bull_put(chain, float(sp30["strike"]))
            if bp is not None:
                results.append({**base, "structure": "bull_put_30d",
                                **{k: bp[k] for k in ["entry_credit","short_K","long_K","wing_width"]},
                                "pnl": settle_pnl(bp, close)})

        # ── Structure 2: bear_call_30d ──
        sc30 = select_short_call_30d(chain)
        if sc30 is not None:
            bc = build_bear_call(chain, float(sc30["strike"]))
            if bc is not None:
                results.append({**base, "structure": "bear_call_30d",
                                **{k: bc[k] for k in ["entry_credit","short_K","long_K","wing_width"]},
                                "pnl": settle_pnl(bc, close)})

        # ── Structure 3: bull_put_mp (only when spot ≥ MP) ──
        if spot >= mp:
            mp_put_K = nth_strike_from(chain, mp, 0)  # strike at/near MP
            if mp_put_K is not None and mp_put_K < spot:  # put short must be below spot
                bpm = build_bull_put(chain, mp_put_K)
                if bpm is not None:
                    results.append({**base, "structure": "bull_put_mp",
                                    **{k: bpm[k] for k in ["entry_credit","short_K","long_K","wing_width"]},
                                    "pnl": settle_pnl(bpm, close)})

        # ── Structure 4: bear_call_mp (only when spot ≤ MP) ──
        if spot <= mp:
            mp_call_K = nth_strike_from(chain, mp, 0)
            if mp_call_K is not None and mp_call_K > spot:
                bcm = build_bear_call(chain, mp_call_K)
                if bcm is not None:
                    results.append({**base, "structure": "bear_call_mp",
                                    **{k: bcm[k] for k in ["entry_credit","short_K","long_K","wing_width"]},
                                    "pnl": settle_pnl(bcm, close)})

    return results


def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers")

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        print(f"  [{i}/{len(COHORT)}] {t}: {len(rows)} rows")

    df = pd.DataFrame(all_rows)
    print(f"\nTotal rows: {len(df):,}")
    if df.empty:
        return

    tier_map = {**{t:0 for t in TIER1}, **{t:1 for t in TIER2}, **{t:2 for t in TIER3}}

    g = df.groupby(["structure","ticker"]).agg(
        n=("pnl","count"), mean=("pnl","mean"), median=("pnl","median"),
        win=("pnl", lambda s: (s>0).mean()),
        worst=("pnl","min"), best=("pnl","max"),
    ).reset_index()
    g["tier"] = g["ticker"].map(tier_map)

    print("\n═══ Per-ticker × structure (mean P&L, $) ═══")
    pv = g.pivot_table(index=["tier","ticker"], columns="structure", values="mean", aggfunc="first").round(3)
    print(pv.to_string())
    print()
    print("═══ Per-ticker × structure (win rate) ═══")
    pvw = g.pivot_table(index=["tier","ticker"], columns="structure", values="win", aggfunc="first").round(3)
    print(pvw.to_string())
    print()
    print("═══ Per-ticker × structure (N) ═══")
    pvn = g.pivot_table(index=["tier","ticker"], columns="structure", values="n", aggfunc="first")
    print(pvn.to_string())
    print()
    print("═══ Structure totals across cohort ═══")
    tot = df.groupby("structure").agg(
        n=("pnl","count"), mean=("pnl","mean"), median=("pnl","median"),
        win=("pnl", lambda s: (s>0).mean()),
        total=("pnl","sum"),
    ).round(3)
    print(tot.to_string())
    print()

    # Head-to-head: bull_put_30d vs bull_put_mp on overlapping cycles
    print("═══ Head-to-head: 30Δ vs MP-anchored on cycles where both built ═══")
    for a,b in [("bull_put_30d","bull_put_mp"),("bear_call_30d","bear_call_mp")]:
        aa = df[df["structure"]==a][["ticker","opex","pnl"]].rename(columns={"pnl":f"pnl_{a}"})
        bb = df[df["structure"]==b][["ticker","opex","pnl"]].rename(columns={"pnl":f"pnl_{b}"})
        m = aa.merge(bb, on=["ticker","opex"], how="inner")
        if m.empty:
            print(f"  {a} vs {b}: no overlapping cycles"); continue
        print(f"  {a} vs {b}: N overlap = {len(m):,}")
        print(f"    {a}: mean {m[f'pnl_{a}'].mean():+.3f}  median {m[f'pnl_{a}'].median():+.3f}  win {(m[f'pnl_{a}']>0).mean():.3f}")
        print(f"    {b}: mean {m[f'pnl_{b}'].mean():+.3f}  median {m[f'pnl_{b}'].median():+.3f}  win {(m[f'pnl_{b}']>0).mean():.3f}")
        print(f"    mp-anchor lift: {m[f'pnl_{b}'].mean()-m[f'pnl_{a}'].mean():+.3f}")

    df.to_parquet(OUT_DIR / "mp_phase2c_verticals.parquet", index=False)
    g.to_parquet(OUT_DIR / "mp_phase2c_by_ticker_structure.parquet", index=False)
    print("\nwrote: data/profile/mp_phase2c_verticals.parquet + by-ticker summary")


if __name__ == "__main__":
    main()
