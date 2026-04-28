"""MP Phase 2h — Aggressive bear_call_mp variant (spot extended above MP).

Phase 2c tested bear_call_mp with spot <= MP (passive) and found no MP-anchor
lift over 30-delta. This script tests the AGGRESSIVE variant:
  - Enter bear_call_mp when spot is meaningfully ABOVE MP (mean-reversion bet)
  - Short call at strike nearest MP (ITM, since spot > MP)
  - Long call one strike higher

Tests multiple extension thresholds: any, 2%, 5%, 10%. Reports per-ticker and
per-regime. Uses slip=0.25, T-5 entry, held to expiry. Cohort expanded beyond
the 19 pinners to include validated two-sided mean-reverters.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
OUT_DIR = ROOT / "data/profile"

# Phase 2c pinner cohort (19 names)
TIER1 = ["BKLN", "HYG", "JNK", "TLT"]
TIER2 = ["SPX", "SPY", "DIA", "QQQ", "IWM"]
TIER3 = ["XLU", "XLV", "IYR", "GLD", "VZ", "KO", "PG", "WMT", "EFA", "VNQ"]
PINNERS = TIER1 + TIER2 + TIER3

# Mean-reverters from project_mean_reversion_universe.md
MEAN_REVERTERS = ["CNC", "TJX", "LBTYA", "NEE", "T", "GILD", "K", "STM", "PFE", "MSFT", "RIO", "NRG", "GLNG"]
# (GLD and VZ already in pinners; excluded from extension list to avoid duplication)

COHORT = sorted(set(PINNERS + MEAN_REVERTERS))

SLIP_FRAC = 0.25

# Regime windows (from project_regime_window_findings.md)
REGIMES = {
    "COVID 2020":    ("2020-02-15", "2020-04-30"),
    "2022 bear":     ("2022-01-01", "2022-10-15"),
    "Dec 2018":      ("2018-10-01", "2018-12-24"),
    "Aug 2015":      ("2015-08-01", "2015-10-01"),
    "volmageddon":   ("2018-01-20", "2018-02-20"),
}


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


def build_bear_call_mp(chain, mp):
    """Short call at strike nearest MP, long one strike higher."""
    mp_K = nth_strike_from(chain, mp, 0)
    if mp_K is None:
        return None
    long_K = nth_strike_from(chain, mp_K, +1)
    if long_K is None:
        return None
    sc_row = get_row(chain, mp_K)
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
        "short_K": mp_K, "long_K": long_K,
        "wing_width": long_K - mp_K,
    }


def settle_pnl(structure, close):
    # short call: credit +intrinsic_short = credit - max(0, close - short_K)
    # long call:         -max(0, close - long_K) for the debit cost (included in credit)
    # Net: credit - max(0, close - short_K) + max(0, close - long_K)
    sK = structure["short_K"]
    lK = structure["long_K"]
    short_intrinsic = max(0.0, close - sK)
    long_intrinsic = max(0.0, close - lK)
    return structure["entry_credit"] - short_intrinsic + long_intrinsic


def run_ticker(ticker, opex_list):
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path, columns=[
        "trade_date", "expirDate", "strike", "stkPx",
        "cOi", "pOi", "cBidPx", "cAskPx",
    ])
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # Map each monthly OpEx to its chain's expiration string
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

        # AGGRESSIVE VARIANT: only enter when spot > MP
        if spot <= mp:
            continue
        ext_pct = (spot - mp) / mp * 100.0

        final = df[df["trade_date"] == opex]
        if final.empty:
            continue
        close = float(final["stkPx"].iloc[0])

        bcm = build_bear_call_mp(chain, mp)
        if bcm is None:
            continue

        results.append({
            "ticker": ticker, "opex": opex, "t_entry": t_entry,
            "spot_entry": spot, "spot_close": close,
            "mp_k": mp, "ext_pct": ext_pct,
            "entry_credit": bcm["entry_credit"],
            "short_K": bcm["short_K"], "long_K": bcm["long_K"], "wing_width": bcm["wing_width"],
            "pnl": settle_pnl(bcm, close),
            "in_mean_reverter_set": ticker in MEAN_REVERTERS,
            "in_pinner_set": ticker in PINNERS,
        })

    return results


def regime_label(opex):
    for name, (start, end) in REGIMES.items():
        if pd.Timestamp(start) <= opex <= pd.Timestamp(end):
            return name
    return "baseline"


def summarize(df, label):
    if len(df) == 0:
        print(f"  {label}: EMPTY")
        return
    m = df["pnl"].mean()
    med = df["pnl"].median()
    win = (df["pnl"] > 0).mean() * 100
    total = df["pnl"].sum()
    worst = df["pnl"].min()
    print(f"  {label:40s}  N={len(df):4d}  mean={m:+.4f}  median={med:+.4f}  win={win:5.1f}%  worst={worst:+.3f}  total={total:+7.2f}")


def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers")
    print(f"  Pinners ({len(PINNERS)}): {sorted(PINNERS)}")
    print(f"  Mean-reverters added ({len(MEAN_REVERTERS)}): {sorted(MEAN_REVERTERS)}")
    print()

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        print(f"  [{i:2d}/{len(COHORT)}] {t:6s}: {len(rows)} cycles entered (spot > MP)")

    df = pd.DataFrame(all_rows)
    print(f"\n═══ Total cycles entered (aggressive bear_call_mp, spot > MP): {len(df):,} ═══\n")
    if df.empty:
        print("No cycles met entry criterion. Exiting.")
        return

    df["regime"] = df["opex"].apply(regime_label)

    # By extension threshold
    print("═══ By extension threshold (cohort-wide) ═══")
    summarize(df, "ALL (spot > MP, any extension)")
    summarize(df[df["ext_pct"] >= 2],  "ext >= 2%")
    summarize(df[df["ext_pct"] >= 5],  "ext >= 5%")
    summarize(df[df["ext_pct"] >= 10], "ext >= 10%")
    print()

    # By extension bucket
    print("═══ By extension bucket ═══")
    summarize(df[(df["ext_pct"] >= 0) & (df["ext_pct"] < 2)],  "0-2% above MP")
    summarize(df[(df["ext_pct"] >= 2) & (df["ext_pct"] < 5)],  "2-5% above MP")
    summarize(df[(df["ext_pct"] >= 5) & (df["ext_pct"] < 10)], "5-10% above MP")
    summarize(df[df["ext_pct"] >= 10],                         ">= 10% above MP")
    print()

    # Pinner vs mean-reverter cohort
    print("═══ Pinner cohort vs mean-reverter cohort (all extensions) ═══")
    summarize(df[df["in_pinner_set"]],             "pinners only (any ext)")
    summarize(df[df["in_mean_reverter_set"]],      "mean-reverters only (any ext)")
    summarize(df[df["in_pinner_set"] & (df["ext_pct"] >= 2)], "pinners + ext >= 2%")
    summarize(df[df["in_mean_reverter_set"] & (df["ext_pct"] >= 2)], "mean-reverters + ext >= 2%")
    summarize(df[df["in_pinner_set"] & (df["ext_pct"] >= 5)], "pinners + ext >= 5%")
    summarize(df[df["in_mean_reverter_set"] & (df["ext_pct"] >= 5)], "mean-reverters + ext >= 5%")
    print()

    # Regime cut
    print("═══ Regime stratification (all extensions) ═══")
    for regime in ["baseline"] + list(REGIMES.keys()):
        sub = df[df["regime"] == regime]
        summarize(sub, regime)
    print()

    # Per-ticker breakdown (any extension, N >= 10)
    print("═══ Per-ticker breakdown (any extension, N>=10) ═══")
    pt = df.groupby("ticker").agg(
        N=("pnl", "count"),
        mean=("pnl", "mean"),
        median=("pnl", "median"),
        win=("pnl", lambda x: (x > 0).mean() * 100),
        total=("pnl", "sum"),
    ).round(3)
    pt = pt[pt["N"] >= 10].sort_values("mean", ascending=False)
    print(pt.to_string())
    print()

    # SPX dominance check (total dollars)
    print("═══ Dollar concentration check (any extension) ═══")
    top5 = df.groupby("ticker")["pnl"].sum().sort_values(ascending=False).head(5)
    bot5 = df.groupby("ticker")["pnl"].sum().sort_values(ascending=True).head(5)
    print(f"  Top 5 contributors:    {top5.to_dict()}")
    print(f"  Bottom 5 contributors: {bot5.to_dict()}")
    spx_total = df[df["ticker"] == "SPX"]["pnl"].sum() if "SPX" in df["ticker"].values else 0
    print(f"  SPX share of total:    {spx_total:+.2f} of {df['pnl'].sum():+.2f}  ({100*spx_total/df['pnl'].sum() if df['pnl'].sum() != 0 else float('nan'):.1f}%)")
    print()
    print("═══ Cohort-wide excluding SPX ═══")
    summarize(df[df["ticker"] != "SPX"],                          "no-SPX any ext")
    summarize(df[(df["ticker"] != "SPX") & (df["ext_pct"] >= 2)], "no-SPX ext >= 2%")
    summarize(df[(df["ticker"] != "SPX") & (df["ext_pct"] >= 5)], "no-SPX ext >= 5%")
    print()

    # Write results
    df.to_parquet(OUT_DIR / "mp_phase2h_bear_call_aggressive.parquet", index=False)
    print(f"wrote: {OUT_DIR / 'mp_phase2h_bear_call_aggressive.parquet'}")


if __name__ == "__main__":
    main()
