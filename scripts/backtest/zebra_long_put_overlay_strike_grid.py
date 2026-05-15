"""ZEBRA + long-put overlay — full strike-grid sweep, HOLD-to-expiry.

Phase 1 only tested ATM / 5% OTM / 10% OTM. Phase 2 M1 (T-21 close) was
rejected; HOLD-to-expiry is the validated manager. This script extends the
strike grid to also include ITM and deeper-OTM puts, so we can identify
the best-fit strike empirically.

Strike grid (signed % offset from spot — negative = ITM, positive = OTM):
  itm10  = -10%
  itm5   = -5%
  atm    =   0%
  otm5   =  +5%
  otm10  = +10%   (current Phase 1 default)
  otm15  = +15%
  otm20  = +20%

ZEBRA + each put are both held to OpEx and settled on intrinsic. Slip 0.25.
Tier-1 cohort (7 names × ~135 cycles each ≈ 934 cycles).

Output:
  data/profile/zebra_put_overlay_strike_grid_results.parquet
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before
from structures import open_zebra, intrinsic_value_at_expiry
from legs import price_long_put

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_OUT = ROOT / "data/profile/zebra_put_overlay_strike_grid_results.parquet"

ENTRY_DTE = 75
SLIP = 0.25
TIER1 = ["SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN"]

# Signed offset from spot: negative = ITM (strike above spot),
# positive = OTM (strike below spot for puts).
STRIKE_GRID = {
    "itm10": -0.10,
    "itm5":  -0.05,
    "atm":    0.00,
    "otm5":   0.05,
    "otm10":  0.10,
    "otm15":  0.15,
    "otm20":  0.20,
}


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def open_long_put_at_signed_pct(chain, spot, signed_pct, expiration):
    """signed_pct > 0 → OTM (target = spot * (1 - pct));
       signed_pct < 0 → ITM (target = spot * (1 - pct) = spot * (1 + |pct|))."""
    target_strike = spot * (1.0 - signed_pct)
    cand = chain.dropna(subset=["pBidPx", "pAskPx", "pMidIv"]).copy()
    if cand.empty:
        return None, None
    cand = cand[cand["pMidIv"] >= C.MIN_IV_FOR_PRICING]
    if cand.empty:
        return None, None
    idx = (cand["strike"] - target_strike).abs().idxmin()
    row = cand.loc[idx]
    K = float(row["strike"])
    px = price_long_put(row)
    if px is None or px <= 0:
        return None, None
    return {"strike": K, "entry_px": float(px)}, float(px)


def intrinsic_put(K, S_exp):
    return max(0.0, K - S_exp)


def simulate_cycle(slice_by_day, available_days, entry_date, expiration, ticker):
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    zpos = open_zebra(entry_chain, pd.Timestamp(entry_date), expiration)
    if zpos is None:
        return None

    spot_entry = zpos.underlying_entry

    puts = {}
    for label, pct in STRIKE_GRID.items():
        p, debit = open_long_put_at_signed_pct(entry_chain, spot_entry, pct, expiration)
        if p is not None:
            puts[label] = (p, debit)

    if not puts:
        return None

    last_chain = slice_by_day.get(expiration.date())
    if last_chain is None or last_chain.empty:
        forward_days = [d for d in available_days
                        if d > entry_date and d <= expiration.date()]
        last_d = forward_days[-1] if forward_days else None
        if last_d is None:
            return None
        last_chain = slice_by_day.get(last_d)
        if last_chain is None or last_chain.empty:
            return None

    S_exp = float(last_chain["stkPx"].iloc[0])
    pnl_zebra = float(zpos.entry_credit + intrinsic_value_at_expiry(zpos, S_exp))

    out = {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "return_pct": (S_exp / spot_entry - 1.0) * 100,
        "zebra_debit": float(zpos.notes["debit"]),
        "pnl_zebra": pnl_zebra,
    }
    for label, (p, debit) in puts.items():
        K = p["strike"]
        intrinsic = intrinsic_put(K, S_exp)
        pnl_put = intrinsic - debit
        out[f"{label}_strike"] = K
        out[f"{label}_debit"] = float(debit)
        out[f"pnl_{label}_put"] = float(pnl_put)
        out[f"pnl_{label}_combined"] = float(pnl_zebra + pnl_put)

    return out


def simulate_ticker(ticker):
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return []
    tdf = pd.read_parquet(path)
    if tdf.empty:
        return []
    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"])
    tdf["date_only"] = tdf["trade_date"].dt.date
    first_date = tdf["trade_date"].min().date()
    last_date = tdf["trade_date"].max().date()

    exp_str_to_date = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None:
            exp_str_to_date[s] = ts

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]
    opex_to_exp = {}
    for opex in opex_eligible:
        ts = pd.Timestamp(opex)
        for s, d in exp_str_to_date.items():
            if abs((d - ts).days) <= 1:
                opex_to_exp[ts] = s
                break

    exp_groups = {s: sub for s, sub in tdf.groupby("expirDate", sort=False)}
    summaries = []

    C.activate_slip(SLIP)
    for opex_ts, exp_str in opex_to_exp.items():
        exp_df = exp_groups[exp_str]
        slice_by_day = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}
        available_days = sorted(slice_by_day.keys())

        target = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target, available_days)
        if entry_date is None:
            continue
        s = simulate_cycle(slice_by_day, available_days, entry_date, opex_ts, ticker)
        if s is not None:
            summaries.append(s)
    return summaries


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("zebra_strikegrid")
    log.info("ZEBRA + put strike-grid sweep on tier-1: %s", TIER1)
    log.info("Strike grid: %s", STRIKE_GRID)

    all_results = []
    for i, t in enumerate(TIER1, 1):
        s = simulate_ticker(t)
        all_results.extend(s)
        log.info("  [%d/%d] %s: %d cycles", i, len(TIER1), t, len(s))

    if not all_results:
        log.error("No cycles produced")
        return

    df = pd.DataFrame(all_results)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d cycles to %s", len(df), RESULTS_OUT)

    n = len(df)
    print(f"\n=== ZEBRA + V_put strike-grid sweep (HOLD-to-OpEx, slip={SLIP}) ===")
    print(f"cycles: {n}\n")

    base_mean = df["pnl_zebra"].mean()
    base_win = (df["pnl_zebra"] > 0).mean()
    base_min = df["pnl_zebra"].min()
    print(f"  BASE (ZEBRA only):   mean=${base_mean:+.2f}  win={base_win:.1%}  worst=${base_min:+.2f}")
    print()

    rows = []
    for label, pct in STRIKE_GRID.items():
        col = f"pnl_{label}_combined"
        if col not in df.columns:
            continue
        m = df[col].mean()
        w = (df[col] > 0).mean()
        mn = df[col].min()
        sd = df[col].std()
        cost = -df[f"{label}_debit"].mean()
        lift = m - base_mean
        rows.append((label, pct, m, w, mn, sd, cost, lift))
        print(f"  +{label:7s} ({pct:+.0%}): mean=${m:+.2f}  win={w:.1%}  worst=${mn:+.2f}  std=${sd:.2f}  cost=${cost:.2f}  lift=${lift:+.2f}")

    # Per-ticker for the top-2 strike picks
    grid = pd.DataFrame(rows, columns=["label", "pct", "mean", "win", "worst", "std", "cost", "lift"])
    grid = grid.sort_values("mean", ascending=False)
    top2 = grid.head(2)["label"].tolist()
    print(f"\n=== Per-ticker (top 2 by cohort-mean: {top2}) ===")
    agg = {"n": ("pnl_zebra", "size"), "zebra": ("pnl_zebra", "mean")}
    for label in top2:
        agg[label] = (f"pnl_{label}_combined", "mean")
    by_t = df.groupby("ticker").agg(**agg)
    print(by_t.to_string())

    # Walk-forward across all strikes
    print("\n=== Walk-forward lift over BASE (per split × strike) ===")
    df["val_year"] = pd.to_datetime(df["expiration"]).dt.year
    splits = [
        ("2021-2023", range(2021, 2024)),
        ("2022-2024", range(2022, 2025)),
        ("2023-2025", range(2023, 2026)),
        ("2024-2026", range(2024, 2027)),
    ]
    header = "  split        " + "  ".join(f"{l:>7s}" for l in STRIKE_GRID)
    print(header)
    pos_count = {l: 0 for l in STRIKE_GRID}
    for slabel, yrs in splits:
        m = df[df["val_year"].isin(list(yrs))]
        if m.empty:
            print(f"  {slabel}: no cycles")
            continue
        base = m["pnl_zebra"].mean()
        parts = []
        for label in STRIKE_GRID:
            col = f"pnl_{label}_combined"
            if col not in m.columns:
                parts.append(f"{'   na':>7s}")
                continue
            lift = m[col].mean() - base
            if lift > 0:
                pos_count[label] += 1
            parts.append(f"{lift:+7.2f}")
        print(f"  {slabel}: " + "  ".join(parts))

    print("\n  Positive splits (out of 4):")
    for label in STRIKE_GRID:
        print(f"    {label:7s}  {pos_count[label]}/4")


if __name__ == "__main__":
    main()
