#!/usr/bin/env python3.11
"""Spotlight: does each current COHORT_BEAR_CALL member's bear-call edge persist?

For every name in gate_config.COHORT_BEAR_CALL, split its OTM managed-50% cycles
into EARLY (entry < 2024-01-01, ~"what the backtest saw") vs RECENT (2024-01-01+,
~out-of-sample / what it's done lately). A FOSSIL = strong early, negative recent.
Flags the user's live shorts and index/ETF members (regime-gated, different game).
P&L is substrate-native (gross); net of slip the positive bar is ~+$0.10/sh.
"""
from __future__ import annotations
import sys, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from scripts.qualifier import gate_config as G  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"
LIVE = {"UNH", "STZ", "ZTS"}
INDEX = {"SPX", "SPY", "QQQ", "DIA", "IWM", "XLP", "IEF", "TMF"}  # regime-gated, not name-picks
CUT = pd.Timestamp("2024-01-01")
NET_BAR = 0.10   # rough gross mean needed to plausibly clear slip net


def main():
    o = pd.read_parquet(ROOT / "data/profile/bear_call_moneyness_results.parquet")
    o = o[o.moneyness == "OTM"].copy(); o["entry_date"] = pd.to_datetime(o["entry_date"])
    rows = []
    for t in sorted(set(G.COHORT_BEAR_CALL)):
        g = o[o.ticker == t]
        early = g[g.entry_date < CUT]["mgd50_pnl"]; recent = g[g.entry_date >= CUT]["mgd50_pnl"]
        me = early.mean() if len(early) else np.nan
        mr = recent.mean() if len(recent) else np.nan
        if len(recent) < 6:
            cls = "THIN"
        elif mr >= NET_BAR:
            cls = "PERSISTS+"      # plausibly net-positive lately
        elif mr > 0:
            cls = "marginal"       # positive gross but likely <0 net of slip
        elif (me == me and me > 0):
            cls = "FOSSIL"         # was positive, now negative
        else:
            cls = "chronic-neg"
        rows.append(dict(t=t, n_all=len(g), m_all=g["mgd50_pnl"].mean() if len(g) else np.nan,
                         n_rec=len(recent), m_rec=mr, m_early=me, cls=cls))
    df = pd.DataFrame(rows)
    order = {"PERSISTS+": 0, "marginal": 1, "FOSSIL": 2, "chronic-neg": 3, "THIN": 4}
    df["o"] = df.cls.map(order); df = df.sort_values(["o", "m_rec"], ascending=[True, False])

    print("=" * 90)
    print("  BEAR-CALL COHORT SPOTLIGHT — does each member's edge persist out-of-sample?")
    print(f"  EARLY = pre-2024 (backtest era) | RECENT = 2024-01-01+ (OOS) | gross; net bar ~+${NET_BAR}/sh")
    print("=" * 90)
    print(f"  {'name':6}{'kind':7}{'n_all':>6}{'mean_all':>9}{'n_rec':>6}{'mean_rec':>9}{'mean_early':>11}  class")
    for _, r in df.iterrows():
        kind = "LIVE" if r.t in LIVE else ("index" if r.t in INDEX else "name")
        me = f"{r.m_early:+.3f}" if r.m_early == r.m_early else "   -"
        mr = f"{r.m_rec:+.3f}" if r.m_rec == r.m_rec else "   -"
        star = " ◀ YOUR SHORT" if r.t in LIVE else ""
        print(f"  {r.t:6}{kind:7}{r.n_all:>6}{r.m_all:>+9.3f}{r.n_rec:>6}{mr:>9}{me:>11}  {r.cls}{star}")

    print("-" * 90)
    nm = df[~df.t.isin(INDEX)]
    print(f"  Single-name members: {len(nm)} | PERSISTS+ {sum(nm.cls=='PERSISTS+')} | "
          f"marginal {sum(nm.cls=='marginal')} | FOSSIL {sum(nm.cls=='FOSSIL')} | "
          f"chronic-neg {sum(nm.cls=='chronic-neg')} | THIN {sum(nm.cls=='THIN')}")
    print(f"  Single-name RECENT mean (pooled): ${nm['m_rec'].mean():+.3f}/sh "
          f"(plausibly net-positive members: {sorted(nm[nm.cls=='PERSISTS+'].t)})")
    for t in sorted(LIVE):
        r = df[df.t == t].iloc[0]
        print(f"  YOUR SHORT {t}: recent ${r.m_rec:+.3f}/sh (n={r.n_rec}) vs early ${r.m_early:+.3f} -> {r.cls}")
    print("=" * 90)
    df.drop(columns="o").to_parquet(ROOT / "data/profile/bearcall_cohort_spotlight.parquet", index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
