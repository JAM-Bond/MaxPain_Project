"""Exploratory — variant (b) of the sector-ETF Stage-2 thesis: when an ETF's
Stage-2 break fires, does bear-call on the WORST top-holding outperform
bear-call on the ETF itself?

Hypothesis (user's instinct, 2026-05-18): when a sector ETF rolls over, the
weighted-average that is the ETF is hiding real losers among its largest
holdings. Trade the losers directly, not the index.

Test design:
  1. Hardcoded top-10 holdings per sector ETF (CURRENT weights, used as a
     proxy for historical weights — explicit look-ahead-bias caveat).
  2. For each constituent with bear_call cycle data, tag each cycle with:
     - parent ETF's Stage-2 active at cycle entry?
     - constituent's own Stage-2 active at entry?
     - constituent's 60-day return at entry (for "worst" ranking)
  3. Compare cells:
     (i)   all constituent cycles                            — baseline
     (ii)  constituent's own Stage-2 active                  — variant (a)
     (iii) parent ETF's Stage-2 active                       — variant (b)
     (iv)  both Stage-2 active                               — compound
     (v)   ETF Stage-2 + constituent is worst-quartile by 60d return — user's full thesis
  4. Compare against ETF-direct (already known: 91 fires, mean +$0.027/sh,
     win 82%).

Output: data/profile/sector_etf_stage2_constituent_targeting.parquet
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts/backtest"))

from sector_etf_bearcall_transition_signals import per_ticker_daily  # noqa: E402

BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_IN = ROOT / "data/profile/bear_call_moneyness_results.parquet"
OUT_PARQUET = ROOT / "data/profile/sector_etf_stage2_constituent_targeting.parquet"


# Top-10 holdings per sector ETF (approximate current weights as of 2025-2026).
# Caveat: this is point-in-2026 data used as proxy for historical 2013-2026
# weights. Real holdings drift considerably — JPM was a smaller share of XLF
# in 2015, NVDA was a much smaller share of XLK pre-2020, etc. Findings are
# directional only; a clean test would need point-in-time SPDR weights file
# (sourceable from State Street but not currently in our data pipeline).
TOP_HOLDINGS: dict[str, list[str]] = {
    "XLF": ["JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP", "BLK", "C"],
    "XLK": ["MSFT", "AAPL", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "CSCO",
             "AMD", "ACN"],
    "XLE": ["XOM", "CVX", "COP", "EOG", "SLB", "OXY", "MPC", "PSX", "VLO"],
    "XLI": ["GE", "RTX", "CAT", "HON", "UNP", "BA", "ETN", "ADP", "LMT",
             "DE"],
    "XLV": ["LLY", "UNH", "JNJ", "MRK", "ABBV", "TMO", "ABT", "PFE", "DHR",
             "AMGN"],
    "XLU": ["NEE", "SO", "DUK", "AEP", "SRE", "EXC", "XEL", "ED", "AWK",
             "WEC"],
    "IYR": ["PLD", "AMT", "EQIX", "WELL", "SPG", "PSA", "CCI", "O", "VICI",
             "DLR"],
    "SMH": ["NVDA", "TSM", "AVGO", "AMD", "ASML", "QCOM", "TXN", "AMAT",
             "INTC", "LRCX"],
}


def summarize(label: str, pnl: pd.Series) -> dict:
    n = len(pnl)
    if n == 0:
        return {"label": label, "n": 0, "mean": np.nan, "median": np.nan,
                "win_rate": np.nan, "total": 0.0}
    return {
        "label": label,
        "n": int(n),
        "mean": round(float(pnl.mean()), 4),
        "median": round(float(pnl.median()), 4),
        "win_rate": round(float((pnl > 0).mean()), 3),
        "total": round(float(pnl.sum()), 2),
    }


def main() -> int:
    if not RESULTS_IN.exists():
        print(f"ERROR: input parquet missing: {RESULTS_IN}")
        return 1
    cycles = pd.read_parquet(RESULTS_IN)
    cycles["entry_date"] = pd.to_datetime(cycles["entry_date"])

    # Build ETF Stage-2 lookup
    etf_stage2_lookup: dict[str, pd.DataFrame] = {}
    for etf in TOP_HOLDINGS:
        d = per_ticker_daily(etf)
        if d is None:
            print(f"  WARN: no daily series for {etf}; skipping")
            continue
        etf_stage2_lookup[etf] = d[["S1_STAGE2_BREAK"]].rename(
            columns={"S1_STAGE2_BREAK": "etf_stage2"})

    # Build constituent dataset
    all_rows = []
    print("\nLoading constituent cycles + tagging signals...")
    for etf, holdings in TOP_HOLDINGS.items():
        if etf not in etf_stage2_lookup:
            continue
        etf_flag = etf_stage2_lookup[etf]
        for c in holdings:
            if not (BY_TICKER / f"{c}.parquet").exists():
                print(f"  {etf}/{c}: no by_ticker data, skipping")
                continue
            c_cycles = cycles[(cycles["ticker"] == c)
                                & (cycles["moneyness"] == "OTM")].copy()
            if c_cycles.empty:
                continue
            c_daily = per_ticker_daily(c)
            if c_daily is None:
                continue
            join = c_daily[["S1_STAGE2_BREAK", "ret_60"]].rename(
                columns={"S1_STAGE2_BREAK": "self_stage2",
                         "ret_60": "self_ret_60"})
            c_cycles = c_cycles.merge(join, left_on="entry_date",
                                        right_index=True, how="left")
            c_cycles = c_cycles.merge(etf_flag, left_on="entry_date",
                                        right_index=True, how="left")
            c_cycles["self_stage2"] = c_cycles["self_stage2"].fillna(0).astype(int)
            c_cycles["etf_stage2"] = c_cycles["etf_stage2"].fillna(0).astype(int)
            c_cycles["etf"] = etf
            all_rows.append(c_cycles)
    if not all_rows:
        print("Zero constituent cycles assembled. Abort.")
        return 1
    df = pd.concat(all_rows, ignore_index=True)
    print(f"Assembled {len(df):,} constituent OTM bear_call cycles "
          f"across {df['ticker'].nunique()} distinct tickers")

    # Compute "worst quartile by self_ret_60" flag CONDITIONAL on ETF stage-2
    # being active (the user's full thesis: when basket weakens, identify the
    # losers WITHIN the basket).
    etf_stage2_cycles = df[df["etf_stage2"] == 1].copy()
    if not etf_stage2_cycles.empty:
        q25 = etf_stage2_cycles["self_ret_60"].quantile(0.25)
        df["worst_quartile_when_etf_stage2"] = (
            (df["etf_stage2"] == 1) & (df["self_ret_60"] <= q25)
        ).astype(int)
        print(f"\n60d-return 25th-percentile threshold (among ETF-stage-2 cycles): {q25:+.4f}")
    else:
        df["worst_quartile_when_etf_stage2"] = 0

    # ── Headline cells ──
    print()
    print("=" * 100)
    print("Constituent-level bear_call performance — OTM, mgd50, slip=0.50, per-share P/L")
    print("=" * 100)

    cells = [
        ("ALL constituent cycles (baseline)", df["mgd50_pnl"]),
        ("self_stage2 ON", df.loc[df["self_stage2"] == 1, "mgd50_pnl"]),
        ("etf_stage2 ON  ← variant (b)", df.loc[df["etf_stage2"] == 1, "mgd50_pnl"]),
        ("BOTH stage_2 ON", df.loc[(df["self_stage2"] == 1)
                                      & (df["etf_stage2"] == 1), "mgd50_pnl"]),
        ("etf_stage2 + worst-quartile 60d return ← user's full thesis",
         df.loc[df["worst_quartile_when_etf_stage2"] == 1, "mgd50_pnl"]),
    ]
    base_mean = cells[0][1].mean()
    rows_summary = []
    for label, pnl in cells:
        s = summarize(label, pnl)
        lift = (s["mean"] - base_mean) if (s["n"] > 0 and not np.isnan(s["mean"])) else np.nan
        lift_s = f"{lift:+.4f}" if not np.isnan(lift) else "—"
        star = "  ★" if (s["n"] >= 25 and not np.isnan(s["mean"]) and s["mean"] > 0) else ""
        print(f"  {label:60s}  N={s['n']:>5d}  mean=${s['mean']:>+.4f}/sh  "
              f"win={s['win_rate']:.3f}  total=${s['total']:>+.2f}  "
              f"lift={lift_s:>9s}{star}")
        rows_summary.append({"cell": label, **s, "lift_vs_baseline": lift})

    # Per-ETF breakdown of variant (b)
    print()
    print("=" * 100)
    print("Variant (b) — etf_stage2 ON — per-ETF and per-constituent breakdown")
    print("=" * 100)
    per_etf_rows = []
    for etf in sorted(TOP_HOLDINGS.keys()):
        sub = df[(df["etf"] == etf) & (df["etf_stage2"] == 1)]
        if sub.empty:
            print(f"  {etf}: 0 cycles (ETF never in Stage-2 at any constituent's entry)")
            continue
        s = summarize(f"{etf} variant (b)", sub["mgd50_pnl"])
        per_etf_rows.append({"etf": etf, **s})
        print(f"  {etf}: N={s['n']:>4d}  mean=${s['mean']:>+.4f}/sh  "
              f"win={s['win_rate']:.3f}  total=${s['total']:>+.2f}")
        # Top-3 best and worst constituents
        by_c = sub.groupby("ticker")["mgd50_pnl"].agg(["count","mean"]).round(4)
        by_c = by_c[by_c["count"] >= 3].sort_values("mean")
        if not by_c.empty:
            print(f"    worst 3 constituents (N≥3): "
                  + ", ".join(f"{t}: ${r['mean']:+.3f}/sh (N={int(r['count'])})"
                                for t, r in by_c.head(3).iterrows()))
            print(f"    best 3 constituents (N≥3):  "
                  + ", ".join(f"{t}: ${r['mean']:+.3f}/sh (N={int(r['count'])})"
                                for t, r in by_c.tail(3).iterrows()))

    # Compare variant (b) vs ETF-direct from earlier exploration
    print()
    print("=" * 100)
    print("Comparison: trade the CONSTITUENT vs trade the ETF when ETF Stage-2 fires")
    print("=" * 100)
    etf_direct_cycles = cycles[cycles["ticker"].isin(TOP_HOLDINGS.keys())
                                  & (cycles["moneyness"] == "OTM")].copy()
    etf_flag_dict = {etf: etf_stage2_lookup[etf] for etf in TOP_HOLDINGS
                       if etf in etf_stage2_lookup}
    etf_tagged = []
    for etf, flag in etf_flag_dict.items():
        sub = etf_direct_cycles[etf_direct_cycles["ticker"] == etf].copy()
        sub = sub.merge(flag, left_on="entry_date", right_index=True, how="left")
        sub["etf_stage2"] = sub["etf_stage2"].fillna(0).astype(int)
        etf_tagged.append(sub)
    etf_direct = pd.concat(etf_tagged, ignore_index=True)
    direct_pnl = etf_direct.loc[etf_direct["etf_stage2"] == 1, "mgd50_pnl"]
    cons_pnl = df.loc[df["etf_stage2"] == 1, "mgd50_pnl"]
    direct_s = summarize("ETF-direct (Stage-2 fires)", direct_pnl)
    cons_s = summarize("Constituent (any top-10, Stage-2 fires)", cons_pnl)
    print(f"  ETF-direct:                          "
          f"N={direct_s['n']:>5d}  mean=${direct_s['mean']:>+.4f}/sh  "
          f"win={direct_s['win_rate']:.3f}  total=${direct_s['total']:+.2f}")
    print(f"  Constituent (any top-10):            "
          f"N={cons_s['n']:>5d}  mean=${cons_s['mean']:>+.4f}/sh  "
          f"win={cons_s['win_rate']:.3f}  total=${cons_s['total']:+.2f}")

    worst_pnl = df.loc[df["worst_quartile_when_etf_stage2"] == 1, "mgd50_pnl"]
    worst_s = summarize("Constituent — worst-quartile by 60d ret", worst_pnl)
    print(f"  Constituent (worst-quartile 60d):    "
          f"N={worst_s['n']:>5d}  mean=${worst_s['mean']:>+.4f}/sh  "
          f"win={worst_s['win_rate']:.3f}  total=${worst_s['total']:+.2f}")

    # Persist
    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows_summary).to_parquet(OUT_PARQUET, index=False)
    print(f"\nWrote {OUT_PARQUET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
