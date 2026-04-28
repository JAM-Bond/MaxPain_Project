"""MP Phase 2e — T-3 exit daily-MTM diagnostic for ALL near-OpEx structures.

Extends Phase 2d (bull_put_mp) to the remaining 7 structures:
  - From Phase 2: iron_fly_atm, iron_fly_mp, iron_condor_mp, butterfly_mp
  - From Phase 2c: bull_put_30d, bear_call_30d, bear_call_mp

All structures: T-5 entry, walk forward day-by-day, record MTM on DTE=4/3/2/1/0.
Two exit rules compared: hold-to-expiry and T-3 exit (close at first day DTE ≤ 3).

The point: ATM short-gamma structures (iron fly, iron butterfly) should be the ones
that blow up in the last 2-3 days. Credit verticals (Phase 2d result) were not.
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


def third_friday(y, m):
    d = date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7 + 14)


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
    ks = c["strike"].values; co = c["cOi"].values; po = c["pOi"].values
    best_K, best = None, None
    for K in ks:
        t = (co * np.maximum(0.0, K - ks)).sum() + (po * np.maximum(0.0, ks - K)).sum()
        if best is None or t < best:
            best, best_K = t, float(K)
    return best_K


def nth(chain, ref, n):
    ks = sorted(chain["strike"].dropna().unique())
    arr = np.array(ks)
    i = int(np.argmin(np.abs(arr - ref)))
    t = i + n
    return float(ks[t]) if 0 <= t < len(ks) else None


def sell(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 - SLIP_FRAC * (ask - bid) / 2


def buy(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 + SLIP_FRAC * (ask - bid) / 2


def get(chain, K):
    r = chain[chain["strike"] == K]
    return r.iloc[0] if not r.empty else None


def build_short_fly(chain, center):
    up = nth(chain, center, +1); dn = nth(chain, center, -1)
    if up is None or dn is None:
        return None
    c = get(chain, center); u = get(chain, up); d = get(chain, dn)
    if any(x is None for x in [c, u, d]):
        return None
    sc = sell(c["cBidPx"], c["cAskPx"]); sp = sell(c["pBidPx"], c["pAskPx"])
    lc = buy(u["cBidPx"], u["cAskPx"]);  lp = buy(d["pBidPx"], d["pAskPx"])
    if None in (sc, sp, lc, lp):
        return None
    credit = sc + sp - lc - lp
    if credit <= 0:
        return None
    return {"entry_credit": credit,
            "legs": [("short","call",center),("short","put",center),
                     ("long","call",up),("long","put",dn)]}


def build_ic(chain, center):
    u1 = nth(chain, center, +1); d1 = nth(chain, center, -1)
    u2 = nth(chain, center, +2); d2 = nth(chain, center, -2)
    if any(x is None for x in [u1, d1, u2, d2]):
        return None
    ru1 = get(chain, u1); rd1 = get(chain, d1); ru2 = get(chain, u2); rd2 = get(chain, d2)
    if any(r is None for r in [ru1, rd1, ru2, rd2]):
        return None
    sc = sell(ru1["cBidPx"], ru1["cAskPx"]); sp = sell(rd1["pBidPx"], rd1["pAskPx"])
    lc = buy(ru2["cBidPx"], ru2["cAskPx"]);  lp = buy(rd2["pBidPx"], rd2["pAskPx"])
    if None in (sc, sp, lc, lp):
        return None
    credit = sc + sp - lc - lp
    if credit <= 0:
        return None
    return {"entry_credit": credit,
            "legs": [("short","call",u1),("short","put",d1),
                     ("long","call",u2),("long","put",d2)]}


def build_long_bfly(chain, center):
    up = nth(chain, center, +1); dn = nth(chain, center, -1)
    if up is None or dn is None:
        return None
    c = get(chain, center); u = get(chain, up); d = get(chain, dn)
    if any(x is None for x in [c, u, d]):
        return None
    ld = buy(d["cBidPx"], d["cAskPx"]); sc = sell(c["cBidPx"], c["cAskPx"]); lu = buy(u["cBidPx"], u["cAskPx"])
    if None in (ld, sc, lu):
        return None
    debit = ld + lu - 2 * sc
    if debit <= 0:
        return None
    return {"entry_credit": -debit,
            "legs": [("long","call",dn),("short","call",center),
                     ("short","call",center),("long","call",up)]}


def build_bull_put(chain, short_K):
    sp_row = get(chain, short_K); long_K = nth(chain, short_K, -1)
    if sp_row is None or long_K is None:
        return None
    lp_row = get(chain, long_K)
    if lp_row is None:
        return None
    sp = sell(sp_row["pBidPx"], sp_row["pAskPx"]); lp = buy(lp_row["pBidPx"], lp_row["pAskPx"])
    if sp is None or lp is None:
        return None
    credit = sp - lp
    if credit <= 0:
        return None
    return {"entry_credit": credit,
            "legs": [("short","put",short_K),("long","put",long_K)]}


def build_bear_call(chain, short_K):
    sc_row = get(chain, short_K); long_K = nth(chain, short_K, +1)
    if sc_row is None or long_K is None:
        return None
    lc_row = get(chain, long_K)
    if lc_row is None:
        return None
    sc = sell(sc_row["cBidPx"], sc_row["cAskPx"]); lc = buy(lc_row["cBidPx"], lc_row["cAskPx"])
    if sc is None or lc is None:
        return None
    credit = sc - lc
    if credit <= 0:
        return None
    return {"entry_credit": credit,
            "legs": [("short","call",short_K),("long","call",long_K)]}


def select_short_put_30d(chain):
    c = chain.dropna(subset=["delta","strike","pBidPx","pAskPx"])
    if c.empty:
        return None
    i = (c["delta"] - 0.70).abs().idxmin()
    r = c.loc[i]
    return r if abs(r["delta"] - 0.70) <= 0.08 else None


def select_short_call_30d(chain):
    c = chain.dropna(subset=["delta","strike","cBidPx","cAskPx"])
    if c.empty:
        return None
    i = (c["delta"] - 0.30).abs().idxmin()
    r = c.loc[i]
    return r if abs(r["delta"] - 0.30) <= 0.08 else None


def close_cost(structure, chain_today):
    """Cost to exit at today's snapshot; returns None if any leg can't be priced."""
    total = 0.0
    for side, opt, K in structure["legs"]:
        r = chain_today[chain_today["strike"] == K]
        if r.empty:
            return None
        r = r.iloc[0]
        if opt == "call":
            if side == "short":
                px = buy(r["cBidPx"], r["cAskPx"])
            else:
                px = sell(r["cBidPx"], r["cAskPx"])
        else:
            if side == "short":
                px = buy(r["pBidPx"], r["pAskPx"])
            else:
                px = sell(r["pBidPx"], r["pAskPx"])
        if px is None:
            return None
        if side == "short":
            total += px
        else:
            total -= px
    return total


def intrinsic(structure, spot):
    t = 0.0
    for side, opt, K in structure["legs"]:
        v = max(0.0, spot - K) if opt == "call" else max(0.0, K - spot)
        t += v if side == "long" else -v
    return t


def walk_forward(structure, sub, t_entry, opex):
    """Return dict {dte: mtm_pnl}. dte=0 uses intrinsic at close."""
    days = sorted(sub[(sub["trade_date"] > t_entry) & (sub["trade_date"] <= opex)]["trade_date"].unique())
    out = {}
    for d in days:
        ch = sub[sub["trade_date"] == d]
        if ch.empty:
            continue
        dte = max(0, (opex.date() - d.date()).days)
        if d == opex:
            spot_close = float(ch["stkPx"].iloc[0])
            out[dte] = structure["entry_credit"] + intrinsic(structure, spot_close)
        else:
            cc = close_cost(structure, ch)
            if cc is not None:
                out[dte] = structure["entry_credit"] - cc
    return out


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

    rows = []
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
        atm = nth(chain, spot, 0)
        if atm is None:
            continue

        structs_to_try = {}
        structs_to_try["iron_fly_atm"]   = build_short_fly(chain, atm)
        structs_to_try["iron_fly_mp"]    = build_short_fly(chain, mp)
        structs_to_try["iron_condor_mp"] = build_ic(chain, mp)
        structs_to_try["butterfly_mp"]   = build_long_bfly(chain, mp)

        sp30 = select_short_put_30d(chain)
        if sp30 is not None:
            structs_to_try["bull_put_30d"] = build_bull_put(chain, float(sp30["strike"]))
        sc30 = select_short_call_30d(chain)
        if sc30 is not None:
            structs_to_try["bear_call_30d"] = build_bear_call(chain, float(sc30["strike"]))

        if spot >= mp:
            mp_put_K = nth(chain, mp, 0)
            if mp_put_K is not None and mp_put_K < spot:
                structs_to_try["bull_put_mp"] = build_bull_put(chain, mp_put_K)
        if spot <= mp:
            mp_call_K = nth(chain, mp, 0)
            if mp_call_K is not None and mp_call_K > spot:
                structs_to_try["bear_call_mp"] = build_bear_call(chain, mp_call_K)

        spot_close = float(sub[sub["trade_date"] == opex]["stkPx"].iloc[0]) if not sub[sub["trade_date"] == opex].empty else np.nan

        for sname, s in structs_to_try.items():
            if s is None:
                continue
            mtm = walk_forward(s, sub, t_entry, opex)
            if not mtm:
                continue
            pnl_exp = mtm.get(0)
            if pnl_exp is None:
                pnl_exp = mtm[min(mtm)]
            t3_cands = sorted([d for d in mtm if d <= 3], reverse=True)
            pnl_t3 = mtm[t3_cands[0]] if t3_cands else None
            rows.append({
                "ticker": ticker, "opex": opex, "structure": sname,
                "spot_entry": spot, "spot_close": spot_close, "mp_k": mp,
                "entry_credit": s["entry_credit"],
                "pnl_expiry": pnl_exp, "pnl_t3": pnl_t3,
                "mtm_d4": mtm.get(4), "mtm_d3": mtm.get(3),
                "mtm_d2": mtm.get(2), "mtm_d1": mtm.get(1),
                "mtm_d0": mtm.get(0),
            })
    return rows


def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers — 8 structures × daily MTM")

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        print(f"  [{i}/{len(COHORT)}] {t}: {len(rows)} rows")

    df = pd.DataFrame(all_rows)
    print(f"\nTotal rows: {len(df):,}")
    if df.empty:
        return

    # ── For each structure: hold-to-expiry vs T-3 exit ──
    print("\n═══ Hold-to-expiry vs T-3 exit (by structure, cohort-pooled) ═══")
    for s in ["iron_fly_atm","iron_fly_mp","iron_condor_mp","butterfly_mp",
              "bull_put_30d","bear_call_30d","bull_put_mp","bear_call_mp"]:
        sub = df[df["structure"] == s].dropna(subset=["pnl_expiry","pnl_t3"])
        if len(sub) == 0:
            continue
        me = sub["pnl_expiry"].mean(); we = (sub["pnl_expiry"]>0).mean(); wk = sub["pnl_expiry"].min()
        mt = sub["pnl_t3"].mean();     wt = (sub["pnl_t3"]>0).mean();     kt = sub["pnl_t3"].min()
        lift = mt - me
        print(f"  {s:<18} N={len(sub):4d}  expiry: mean {me:+.4f} win {we:.3f} worst {wk:+7.2f}  |  T-3: mean {mt:+.4f} win {wt:.3f} worst {kt:+7.2f}  |  lift {lift:+.4f}")

    # ── Day-by-day MTM for each structure, winners vs big-losers ──
    print("\n═══ Mean MTM by day, winners vs big-losers ═══")
    for s in ["iron_fly_atm","iron_fly_mp","iron_condor_mp","butterfly_mp",
              "bull_put_30d","bear_call_30d","bull_put_mp","bear_call_mp"]:
        sub = df[df["structure"] == s].dropna(subset=["mtm_d4","mtm_d3","mtm_d2","mtm_d1","mtm_d0"]).copy()
        if len(sub) == 0:
            continue
        sub["bucket"] = np.where(sub["pnl_expiry"] <= -sub["entry_credit"].abs()*2, "big_loss",
                          np.where(sub["pnl_expiry"] < 0, "small_loss", "winner"))
        agg = sub.groupby("bucket").agg(
            n=("pnl_expiry","count"),
            d4=("mtm_d4","mean"), d3=("mtm_d3","mean"),
            d2=("mtm_d2","mean"), d1=("mtm_d1","mean"),
            d0=("pnl_expiry","mean"),
        ).round(3)
        print(f"\n  {s}:")
        print(agg.to_string())

    df.to_parquet(OUT_DIR / "mp_phase2e_t3_all_structures.parquet", index=False)
    print("\nwrote: data/profile/mp_phase2e_t3_all_structures.parquet")


if __name__ == "__main__":
    main()
