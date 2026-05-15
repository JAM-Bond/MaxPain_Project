"""H2 weakness — current snapshot on 2026-05-14.

Surfaces today's names matching the W3 (multi-filter) and W4 (new 52w low
in last 20 trading days) weakness criteria, against the 326-name ORATS
universe. These are the live candidates for:
  - bull_put EXCLUSION (don't sell put credit on these names)
  - bear-side cohort if H1 ever fires
  - "puts on weak names" tail-hedge identification (Burry-style)
"""
from __future__ import annotations

import sys
from pathlib import Path
import logging

import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"


def build_close_panel():
    closes = {}
    files = sorted(BY_TICKER.glob("*.parquet"))
    for p in files:
        ticker = p.stem
        try:
            df = pd.read_parquet(p, columns=["trade_date", "stkPx"])
        except Exception:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df = df.drop_duplicates(subset=["trade_date"], keep="first")
        s = df.set_index("trade_date")["stkPx"].astype(float)
        closes[ticker] = s
    panel = pd.DataFrame(closes)
    panel.index = pd.to_datetime(panel.index)
    panel = panel.sort_index()
    return panel


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("h2snap")
    panel = build_close_panel()
    today = panel.index.max()
    log.info("Latest panel date: %s", today.date())

    # Metrics for today's row
    ret_252 = panel.pct_change(252, fill_method=None)
    ret_126 = panel.pct_change(126, fill_method=None)
    ret_60 = panel.pct_change(60, fill_method=None)
    ranks_252 = ret_252.rank(axis=1, pct=True)
    ma_200 = panel.rolling(200, min_periods=100).mean()
    ma_30w = panel.rolling(150, min_periods=80).mean()
    ma_30w_slope = ma_30w - ma_30w.shift(30)
    rolling_52w_high = panel.rolling(252, min_periods=120).max()
    rolling_52w_low = panel.rolling(252, min_periods=120).min()
    dist_from_52w_high = panel / rolling_52w_high - 1.0
    new_52w_low = panel <= rolling_52w_low + 1e-9
    new_low_recent = new_52w_low.rolling(20, min_periods=1).max().fillna(0).astype(bool)

    price = panel.loc[today]
    rs = ranks_252.loc[today]
    ma200 = ma_200.loc[today]
    r6 = ret_126.loc[today]
    r2 = ret_60.loc[today]
    r12 = ret_252.loc[today]
    dfh = dist_from_52w_high.loc[today]
    nl_recent = new_low_recent.loc[today]
    below_200 = price < ma200
    far_below_52w = dfh < -0.30

    # W3: RS bottom 10% AND below 200dma AND >30% off 52w high
    w3 = (rs <= 0.10) & below_200 & far_below_52w
    # W4: new 52w low in last 20 trading days
    w4 = nl_recent
    # Composite (also useful)
    w3_and_w4 = w3 & w4

    def show(label, mask):
        names = mask[mask].dropna().index.tolist()
        names = [n for n in names if pd.notna(price.get(n))]
        if not names:
            print(f"\n{label}: NO NAMES match today.")
            return
        rows = []
        for n in names:
            rows.append({
                "ticker": n,
                "price": float(price[n]),
                "rs_pct": float(rs[n]) if pd.notna(rs[n]) else None,
                "60d_ret": float(r2[n]) if pd.notna(r2[n]) else None,
                "6m_ret": float(r6[n]) if pd.notna(r6[n]) else None,
                "12m_ret": float(r12[n]) if pd.notna(r12[n]) else None,
                "dist_52wH": float(dfh[n]) if pd.notna(dfh[n]) else None,
                "below_200": bool(below_200.get(n, False)),
                "new_low_20d": bool(nl_recent.get(n, False)),
            })
        df = pd.DataFrame(rows).sort_values("12m_ret")
        print(f"\n{label}  (N={len(df)})")
        print(df.to_string(index=False,
                           formatters={
                               "price": "${:.2f}".format,
                               "rs_pct": "{:.2f}".format,
                               "60d_ret": "{:+.1%}".format,
                               "6m_ret": "{:+.1%}".format,
                               "12m_ret": "{:+.1%}".format,
                               "dist_52wH": "{:+.1%}".format,
                           }))

    print(f"H2 CURRENT SNAPSHOT — {today.date()}")
    print("=" * 70)
    show("W3: RS bottom 10% + below 200dma + >30% off 52w high", w3)
    show("W4: new 52w low within last 20 trading days", w4)
    show("W3 ∩ W4: strongest signal (multi-filter AND accelerating)", w3_and_w4)


if __name__ == "__main__":
    main()
