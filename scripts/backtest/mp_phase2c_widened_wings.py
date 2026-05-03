"""MP Phase 2c (variant) — bull_put_mp with wings widened to satisfy 0.50 floor.

Tests whether the +$0.072/cycle MP-anchor lift survives when the long leg
is extended outward until credit/width >= MIN_CREDIT_WIDTH instead of being
fixed at one strike below short. Walks the same 13-yr ORATS history and
the same Phase 2c cohort as mp_phase2c_verticals.py — only the long-leg
selection rule changes.

Output: head-to-head printed to stdout. No DB or parquet writes.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")

TIER1 = ["BKLN", "HYG", "JNK", "TLT"]
TIER2 = ["SPX", "SPY", "DIA", "QQQ", "IWM"]
TIER3 = ["XLU", "XLV", "IYR", "GLD", "VZ", "KO", "PG", "WMT", "EFA", "VNQ"]
COHORT = TIER1 + TIER2 + TIER3

SLIP_FRAC = 0.25
MIN_CREDIT_WIDTH = 0.50
MAX_WING_STRIKES = 6   # cap how far we extend the long leg


def third_friday(year, month):
    d = date(year, month, 1)
    return d + timedelta(days=((4 - d.weekday()) % 7) + 14)


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
    coi = c["cOi"].values
    poi = c["pOi"].values
    best_K, best_pain = None, None
    for K in strikes:
        total = (coi * np.maximum(0.0, K - strikes)).sum() + \
                (poi * np.maximum(0.0, strikes - K)).sum()
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


def build_bull_put_at_strikes(chain, short_K, long_K):
    sp_row = get_row(chain, short_K)
    lp_row = get_row(chain, long_K)
    if sp_row is None or lp_row is None:
        return None
    sp = price_sell(sp_row["pBidPx"], sp_row["pAskPx"])
    lp = price_buy(lp_row["pBidPx"], lp_row["pAskPx"])
    if sp is None or lp is None:
        return None
    credit = sp - lp
    width = short_K - long_K
    if credit <= 0 or width <= 0:
        return None
    return {"entry_credit": credit, "short_K": short_K, "long_K": long_K,
            "wing_width": width, "credit_width": credit / width}


def build_bull_put_one_strike(chain, short_K):
    long_K = nth_strike_from(chain, short_K, -1)
    if long_K is None:
        return None
    return build_bull_put_at_strikes(chain, short_K, long_K)


def build_bull_put_widened(chain, short_K, min_credit_width=MIN_CREDIT_WIDTH,
                           max_strikes=MAX_WING_STRIKES):
    """Try long = -1, -2, ... until credit/width >= floor or budget exhausted."""
    best = None
    for n in range(1, max_strikes + 1):
        long_K = nth_strike_from(chain, short_K, -n)
        if long_K is None:
            break
        spread = build_bull_put_at_strikes(chain, short_K, long_K)
        if spread is None:
            continue
        if best is None:
            best = spread  # fallback if no wing meets floor
        if spread["credit_width"] >= min_credit_width:
            return spread
    return best  # may be None or just the best we found if floor never met


def settle_bull_put(short_K, long_K, entry_credit, close):
    short_intr = max(0.0, short_K - close)
    long_intr = max(0.0, long_K - close)
    return entry_credit + (-short_intr + long_intr)


def run_ticker(ticker, opex_list):
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path, columns=[
        "trade_date", "expirDate", "strike", "stkPx", "delta",
        "cOi", "pOi", "cBidPx", "cAskPx", "pBidPx", "pAskPx",
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"])

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
        if spot < mp:
            continue  # Phase 2c entry rule
        mp_K = nth_strike_from(chain, mp, 0)
        if mp_K is None or mp_K >= spot:
            continue

        final = df[df["trade_date"] == opex]
        if final.empty:
            continue
        close = float(final["stkPx"].iloc[0])

        # Original: 1-strike-below long
        orig = build_bull_put_one_strike(chain, mp_K)
        # Widened: extend long until credit/width >= 0.50
        wide = build_bull_put_widened(chain, mp_K)

        base = {"ticker": ticker, "opex": opex, "spot_entry": spot,
                "spot_close": close, "mp_k": mp, "mp_strike": mp_K}

        if orig is not None:
            rows.append({**base, "variant": "orig",
                         "short_K": orig["short_K"], "long_K": orig["long_K"],
                         "wing": orig["wing_width"], "credit": orig["entry_credit"],
                         "cw": orig["credit_width"],
                         "pnl": settle_bull_put(orig["short_K"], orig["long_K"],
                                                orig["entry_credit"], close),
                         "passes_floor": orig["credit_width"] >= MIN_CREDIT_WIDTH})
        if wide is not None:
            rows.append({**base, "variant": "wide",
                         "short_K": wide["short_K"], "long_K": wide["long_K"],
                         "wing": wide["wing_width"], "credit": wide["entry_credit"],
                         "cw": wide["credit_width"],
                         "pnl": settle_bull_put(wide["short_K"], wide["long_K"],
                                                wide["entry_credit"], close),
                         "passes_floor": wide["credit_width"] >= MIN_CREDIT_WIDTH})

    return rows


def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers · floor cw>={MIN_CREDIT_WIDTH:.2f} · "
          f"max wing strikes={MAX_WING_STRIKES} · slip={SLIP_FRAC}")

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        n_orig = sum(1 for r in rows if r["variant"] == "orig")
        n_wide = sum(1 for r in rows if r["variant"] == "wide")
        print(f"  [{i}/{len(COHORT)}] {t}: orig={n_orig}  wide={n_wide}")

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("no rows")
        return

    print(f"\nTotal cycle-rows: {len(df):,} (orig + wide combined)\n")

    # Cohort totals per variant
    print("═══ Cohort totals (each variant standalone) ═══")
    tot = df.groupby("variant").agg(
        n=("pnl", "count"),
        mean=("pnl", "mean"),
        median=("pnl", "median"),
        win=("pnl", lambda s: (s > 0).mean()),
        total=("pnl", "sum"),
        avg_cw=("cw", "mean"),
        pct_floor_pass=("passes_floor", "mean"),
    ).round(4)
    print(tot.to_string())

    # Per-ticker means
    print("\n═══ Per-ticker mean P&L ═══")
    pv = df.pivot_table(index="ticker", columns="variant", values="pnl",
                        aggfunc="mean").round(3)
    pv["lift"] = (pv.get("wide", 0) - pv.get("orig", 0)).round(3)
    pv["n_orig"] = df[df["variant"] == "orig"].groupby("ticker").size()
    pv["n_wide"] = df[df["variant"] == "wide"].groupby("ticker").size()
    print(pv.to_string())

    # Head-to-head on overlapping cycles only
    orig = df[df["variant"] == "orig"][["ticker", "opex", "pnl", "cw", "wing"]] \
        .rename(columns={"pnl": "pnl_orig", "cw": "cw_orig", "wing": "wing_orig"})
    wide = df[df["variant"] == "wide"][["ticker", "opex", "pnl", "cw", "wing"]] \
        .rename(columns={"pnl": "pnl_wide", "cw": "cw_wide", "wing": "wing_wide"})
    m = orig.merge(wide, on=["ticker", "opex"], how="inner")
    print(f"\n═══ Head-to-head: orig vs wide on overlapping cycles (N={len(m):,}) ═══")
    print(f"  orig:  mean {m['pnl_orig'].mean():+.4f}   median {m['pnl_orig'].median():+.4f}   "
          f"win {(m['pnl_orig'] > 0).mean():.3f}   avg cw {m['cw_orig'].mean():.3f}   "
          f"avg wing ${m['wing_orig'].mean():.2f}")
    print(f"  wide:  mean {m['pnl_wide'].mean():+.4f}   median {m['pnl_wide'].median():+.4f}   "
          f"win {(m['pnl_wide'] > 0).mean():.3f}   avg cw {m['cw_wide'].mean():.3f}   "
          f"avg wing ${m['wing_wide'].mean():.2f}")
    delta = m["pnl_wide"].mean() - m["pnl_orig"].mean()
    print(f"  widening lift: {delta:+.4f}/cycle   "
          f"(orig avg cw {m['cw_orig'].mean():.2f} vs wide {m['cw_wide'].mean():.2f})")

    # Worst-case tail comparison
    print("\n═══ Tail risk (P&L 5th percentile and worst single cycle) ═══")
    print(f"  orig: 5%ile {m['pnl_orig'].quantile(0.05):+.3f}   "
          f"worst {m['pnl_orig'].min():+.3f}")
    print(f"  wide: 5%ile {m['pnl_wide'].quantile(0.05):+.3f}   "
          f"worst {m['pnl_wide'].min():+.3f}")

    # Floor-pass rate
    print(f"\n═══ Credit/width floor pass rate (>= {MIN_CREDIT_WIDTH:.2f}) ═══")
    print(f"  orig: {m['cw_orig'].ge(MIN_CREDIT_WIDTH).mean()*100:.1f}% of cycles passed natively")
    print(f"  wide: {m['cw_wide'].ge(MIN_CREDIT_WIDTH).mean()*100:.1f}% of cycles passed after widening")


if __name__ == "__main__":
    main()
