"""ZEBRA overlay strike-grid — N=1 bear-window stability test.

The strike-grid finding's headline (ITM puts dominate when the bear gate
is open) rests on a single regime sample: 2022-2024. This script
partitions that window into sub-periods to test whether the ITM lift is
consistent intra-bear or driven by a single sub-period.

Three partitions of the 2022-2024 window by expiration year:
  2022 only
  2023 only
  2024 only

And a finer partition by half-year (H1/H2 of each year) to see whether
the lift is a single-event artifact or a regime-wide property.

Reads: data/profile/zebra_put_overlay_strike_grid_results.parquet
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
GRID_PATH = ROOT / "data/profile/zebra_put_overlay_strike_grid_results.parquet"

STRIKE_LABELS = ["itm10", "itm5", "atm", "otm5", "otm10", "otm15", "otm20"]


def main():
    df = pd.read_parquet(GRID_PATH)
    df["expiration"] = pd.to_datetime(df["expiration"])
    df["exp_year"] = df["expiration"].dt.year
    df["exp_half"] = df["expiration"].dt.month.apply(lambda m: "H1" if m <= 6 else "H2")
    df["yh"] = df["exp_year"].astype(str) + df["exp_half"]

    bear_mask = df["exp_year"].isin([2022, 2023, 2024])
    bear = df[bear_mask].copy()
    print(f"Bear-window (2022-2024 expirations): {len(bear)} cycles")
    print(f"Full sample: {len(df)} cycles\n")

    # Per-year lift over BASE (ZEBRA only) for each strike
    print("=== Per-year lift over BARE (in $/cycle) ===")
    print("  year   n   " + "  ".join(f"{l:>7s}" for l in STRIKE_LABELS))
    for yr in [2022, 2023, 2024]:
        sub = bear[bear["exp_year"] == yr]
        if sub.empty:
            print(f"  {yr}: no cycles")
            continue
        base = sub["pnl_zebra"].mean()
        parts = []
        for l in STRIKE_LABELS:
            col = f"pnl_{l}_combined"
            lift = sub[col].mean() - base
            parts.append(f"{lift:+7.2f}")
        print(f"  {yr}  {len(sub):3d}  " + "  ".join(parts))

    print("\n=== Per-year ITM vs OTM showdown ===")
    print("  year   n   ITM5 lift  OTM10 lift  ITM5-OTM10 advantage")
    for yr in [2022, 2023, 2024]:
        sub = bear[bear["exp_year"] == yr]
        if sub.empty:
            continue
        base = sub["pnl_zebra"].mean()
        itm5_lift = sub["pnl_itm5_combined"].mean() - base
        otm10_lift = sub["pnl_otm10_combined"].mean() - base
        adv = itm5_lift - otm10_lift
        print(f"  {yr}  {len(sub):3d}  ${itm5_lift:+7.2f}    ${otm10_lift:+7.2f}    ${adv:+7.2f}")

    # By half-year
    print("\n=== Per half-year (2022H1..2024H2) — ITM5 advantage over OTM10 ===")
    for yh in sorted(bear["yh"].unique()):
        sub = bear[bear["yh"] == yh]
        if sub.empty:
            continue
        base = sub["pnl_zebra"].mean()
        itm5_lift = sub["pnl_itm5_combined"].mean() - base
        otm10_lift = sub["pnl_otm10_combined"].mean() - base
        adv = itm5_lift - otm10_lift
        marker = "  ✓" if adv > 0 else "  ✗"
        print(f"  {yh}  N={len(sub):3d}  ITM5=${itm5_lift:+7.2f}  OTM10=${otm10_lift:+7.2f}  ITM5_adv=${adv:+7.2f}{marker}")

    # Per-ticker within the bear window: is the lift broad or concentrated?
    print("\n=== Per-ticker ITM5 advantage over OTM10 within 2022-2024 ===")
    by_t = bear.groupby("ticker").agg(
        n=("pnl_zebra", "size"),
        zebra=("pnl_zebra", "mean"),
        itm5=("pnl_itm5_combined", "mean"),
        otm10=("pnl_otm10_combined", "mean"),
    )
    by_t["itm5_adv"] = by_t["itm5"] - by_t["otm10"]
    by_t = by_t.sort_values("itm5_adv", ascending=False)
    print(by_t.to_string())

    # ITM5 dominance signal across the per-half-year sample
    halves = bear["yh"].unique()
    advs = []
    for yh in halves:
        sub = bear[bear["yh"] == yh]
        if len(sub) < 3:
            continue
        base = sub["pnl_zebra"].mean()
        itm5_lift = sub["pnl_itm5_combined"].mean() - base
        otm10_lift = sub["pnl_otm10_combined"].mean() - base
        advs.append(itm5_lift - otm10_lift)
    if advs:
        pos = sum(1 for a in advs if a > 0)
        print(f"\n  Half-years with ITM5 advantage > 0: {pos}/{len(advs)}")
        print(f"  Median advantage: ${pd.Series(advs).median():+.2f}/cyc")
        print(f"  Mean advantage:   ${pd.Series(advs).mean():+.2f}/cyc")

    # Per-ticker dominance signal
    pos_t = (by_t["itm5_adv"] > 0).sum()
    print(f"\n  Per-ticker: ITM5 > OTM10 in {pos_t}/{len(by_t)} names within the bear")


if __name__ == "__main__":
    main()
