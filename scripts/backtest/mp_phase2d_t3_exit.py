"""MP Phase 2d — T-3 exit + day-by-day MTM diagnostic on bull_put_mp.

Two questions answered in one run:
  1. Does exiting at T-3 (close when DTE ≤ 3 trading days) improve the
     bull_put_mp result vs hold-to-expiry from Phase 2c?
  2. For the losing cycles, WHEN during the 5-day hold does the loss actually
     materialize? Tests the user's gamma-explosion hypothesis.

Method:
  - Entry T-5 Monday (or nearest trading day on/before opex-5 calendar days)
  - Walk forward through each day of the hold
  - Compute daily MTM using that day's bid/ask at slip=0.25 for close-out cost
  - Record MTM on each day: T-5 entry, T-4, T-3, T-2, T-1, T-0 (final)
  - T-3 exit rule: first day where DTE ≤ 3 → that's the exit MTM
  - Hold-to-expiry rule: final close intrinsic (matches Phase 2c behavior)

Output: per-cycle MTM table with all day snapshots.
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
    t = idx + n
    if 0 <= t < len(strikes):
        return float(strikes[t])
    return None


def price_sell(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 - SLIP_FRAC * (ask - bid) / 2


def price_buy(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 + SLIP_FRAC * (ask - bid) / 2


def intrinsic_put(strike, spot):
    return max(0.0, strike - spot)


def mtm_bull_put(chain_today, short_K, long_K, entry_credit):
    """Cost to close the bull put now. P&L = entry_credit − close_cost."""
    sp_row = chain_today[chain_today["strike"] == short_K]
    lp_row = chain_today[chain_today["strike"] == long_K]
    if sp_row.empty or lp_row.empty:
        return None
    sp = sp_row.iloc[0]; lp = lp_row.iloc[0]
    # Closing short put: buy back. Closing long put: sell.
    buy_back = price_buy(sp["pBidPx"], sp["pAskPx"])
    sell_back = price_sell(lp["pBidPx"], lp["pAskPx"])
    if buy_back is None or sell_back is None:
        return None
    close_cost = buy_back - sell_back
    return entry_credit - close_cost


def settle_bull_put_at_expiry(short_K, long_K, spot_close, entry_credit):
    intrinsic = -intrinsic_put(short_K, spot_close) + intrinsic_put(long_K, spot_close)
    return entry_credit + intrinsic


def run_ticker(ticker, opex_list):
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path, columns=["trade_date","expirDate","strike","stkPx","cOi","pOi","pBidPx","pAskPx"])
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
        chain_entry = pre[pre["trade_date"] == t_entry].copy()
        if chain_entry.empty:
            continue
        mp = compute_max_pain(chain_entry)
        if mp is None:
            continue
        spot = float(chain_entry["stkPx"].iloc[0])

        # Only run bull_put_mp cycles (the structure of interest)
        if spot < mp:
            continue
        short_K = nth_strike_from(chain_entry, mp, 0)
        if short_K is None or short_K >= spot:
            continue
        long_K = nth_strike_from(chain_entry, short_K, -1)
        if long_K is None:
            continue

        sp_row = chain_entry[chain_entry["strike"] == short_K]
        lp_row = chain_entry[chain_entry["strike"] == long_K]
        if sp_row.empty or lp_row.empty:
            continue
        sp_row = sp_row.iloc[0]; lp_row = lp_row.iloc[0]
        sp_px = price_sell(sp_row["pBidPx"], sp_row["pAskPx"])
        lp_px = price_buy(lp_row["pBidPx"], lp_row["pAskPx"])
        if sp_px is None or lp_px is None:
            continue
        entry_credit = sp_px - lp_px
        if entry_credit <= 0:
            continue

        # Walk forward — collect MTM per day
        forward_days = sorted(sub[(sub["trade_date"] > t_entry) & (sub["trade_date"] <= opex)]["trade_date"].unique())
        mtm_by_day: dict[int, float] = {}
        for d in forward_days:
            chain_today = sub[sub["trade_date"] == d]
            if chain_today.empty:
                continue
            dte = max(0, (opex.date() - d.date()).days)
            if d == opex:
                # Settle with intrinsic (close cost might be stale on expiry day)
                spot_close = float(chain_today["stkPx"].iloc[0])
                pnl = settle_bull_put_at_expiry(short_K, long_K, spot_close, entry_credit)
            else:
                pnl = mtm_bull_put(chain_today, short_K, long_K, entry_credit)
            if pnl is None:
                continue
            mtm_by_day[dte] = pnl

        if not mtm_by_day:
            continue

        # T-3 exit rule: close at first day where DTE ≤ 3
        t3_candidates = sorted([d for d in mtm_by_day if d <= 3], reverse=True)
        pnl_t3 = mtm_by_day[t3_candidates[0]] if t3_candidates else None

        # Expiry exit: the DTE-0 entry
        pnl_exp = mtm_by_day.get(0)
        if pnl_exp is None:
            # fall back to lowest DTE available
            pnl_exp = mtm_by_day[min(mtm_by_day)]

        spot_close = float(sub[sub["trade_date"] == opex]["stkPx"].iloc[0]) if not sub[sub["trade_date"] == opex].empty else np.nan

        results.append({
            "ticker": ticker, "opex": opex,
            "spot_entry": spot, "spot_close": spot_close,
            "mp_k": mp, "short_K": short_K, "long_K": long_K,
            "entry_credit": entry_credit,
            "pnl_expiry": pnl_exp,
            "pnl_t3": pnl_t3,
            "mtm_d4": mtm_by_day.get(4),  # Tuesday (DTE=4)
            "mtm_d3": mtm_by_day.get(3),  # Tuesday EOD / Wed morning
            "mtm_d2": mtm_by_day.get(2),  # Wednesday
            "mtm_d1": mtm_by_day.get(1),  # Thursday
            "mtm_d0": mtm_by_day.get(0),  # Friday
        })

    return results


def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers — running bull_put_mp with daily MTM")

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        print(f"  [{i}/{len(COHORT)}] {t}: {len(rows)} cycles")

    df = pd.DataFrame(all_rows)
    print(f"\nTotal cycles: {len(df):,}")
    if df.empty:
        return

    # ── Question 1: does T-3 exit beat hold-to-expiry? ──
    valid = df.dropna(subset=["pnl_expiry", "pnl_t3"])
    print("\n═══ T-3 exit vs hold-to-expiry (bull_put_mp, all qualifying cycles) ═══")
    print(f"  N cycles: {len(valid):,}")
    print(f"  hold-to-expiry: mean ${valid['pnl_expiry'].mean():+.4f}  median ${valid['pnl_expiry'].median():+.4f}  win {(valid['pnl_expiry']>0).mean():.3f}  total ${valid['pnl_expiry'].sum():+.2f}  worst ${valid['pnl_expiry'].min():+.2f}")
    print(f"  T-3 exit:       mean ${valid['pnl_t3'].mean():+.4f}  median ${valid['pnl_t3'].median():+.4f}  win {(valid['pnl_t3']>0).mean():.3f}  total ${valid['pnl_t3'].sum():+.2f}  worst ${valid['pnl_t3'].min():+.2f}")

    # ── Question 2: when does loss materialize? ──
    # Classify cycle by final outcome: big loser = pnl_expiry < -credit (full wing breached net of credit), winner = pnl_expiry > 0
    print("\n═══ When does P&L go wrong? Mean MTM by day for each outcome bucket ═══")
    valid2 = valid.dropna(subset=["mtm_d4","mtm_d3","mtm_d2","mtm_d1","mtm_d0"])
    valid2 = valid2.copy()
    valid2["outcome"] = np.select(
        [valid2["pnl_expiry"] <= -valid2["entry_credit"]*2,
         valid2["pnl_expiry"] < 0,
         valid2["pnl_expiry"] >= 0],
        ["big_loss", "small_loss", "winner"],
        default="unknown"
    )
    agg = valid2.groupby("outcome").agg(
        n=("pnl_expiry","count"),
        mtm_d4=("mtm_d4","mean"),
        mtm_d3=("mtm_d3","mean"),
        mtm_d2=("mtm_d2","mean"),
        mtm_d1=("mtm_d1","mean"),
        mtm_d0=("pnl_expiry","mean"),
    ).round(3)
    print(agg.to_string())

    # ── When did big losers first turn red? ──
    big = valid2[valid2["outcome"] == "big_loss"].copy()
    if len(big) > 0:
        # For each big-loss cycle, find the first DTE (going T-4 → T-3 → T-2 → T-1 → T-0) where MTM < 0
        def first_red(r):
            for d in [4,3,2,1,0]:
                v = r.get(f"mtm_d{d}")
                if pd.notna(v) and v < 0:
                    return d
            return None
        big["first_red_dte"] = big.apply(first_red, axis=1)
        print(f"\n═══ For the {len(big)} BIG-LOSS cycles — DTE at which P&L first turned negative ═══")
        dist = big["first_red_dte"].value_counts().sort_index(ascending=False)
        print(dist.to_string())
        print(f"  i.e., DTE=4 means Tuesday EOD; DTE=3 Wednesday; DTE=2 Wed EOD/Thu; DTE=1 Thursday; DTE=0 Friday")

    # ── Per-ticker T-3 vs expiry ──
    print("\n═══ Per-ticker T-3 vs expiry mean P&L ═══")
    pt = valid.groupby("ticker").agg(
        n=("pnl_expiry","count"),
        mean_expiry=("pnl_expiry","mean"),
        mean_t3=("pnl_t3","mean"),
        win_expiry=("pnl_expiry", lambda s: (s>0).mean()),
        win_t3=("pnl_t3", lambda s: (s>0).mean()),
    ).round(4)
    pt["t3_lift"] = pt["mean_t3"] - pt["mean_expiry"]
    pt = pt.sort_values("t3_lift", ascending=False)
    print(pt.to_string())

    df.to_parquet(OUT_DIR / "mp_phase2d_bull_put_mp_daily_mtm.parquet", index=False)
    print("\nwrote: data/profile/mp_phase2d_bull_put_mp_daily_mtm.parquet")


if __name__ == "__main__":
    main()
