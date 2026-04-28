"""MP test suite Phase 1 — pin reliability scan across the 150-symbol universe.

Pre-registered methodology (see `project_maxpain_test_suite_plan.md`):

For each (ticker, monthly OpEx expiration):
  1. On T-1 (Thursday EOD, day before third-Friday expiration):
     - Compute max pain strike MP: argmin over all strikes K of
         sum over strikes Ks of  cOi(Ks) · max(0, K − Ks) + pOi(Ks) · max(0, Ks − K)
     - MP = the strike where total option writer payout at expiry = S=K is minimized
  2. Record actual spot at Friday close (T-0)
  3. Distance metrics:
     - abs_pct = |close − MP| / close        (primary, scale-free)
     - abs_dollars = |close − MP|
     - within_1pct = close within 1% of MP  (binary)
     - within_2pct = close within 2% of MP
     - within_1_strike = close within 1 strike-spacing of MP
  4. Strike spacing per cycle = median of adjacent-strike gaps in the ATM region

Per-ticker aggregation:
  - PRIMARY RANK: median abs_pct (lower = more reliable pinner)
  - Secondary: pct of cycles where within_1pct = True
  - Sample constraint: require ≥ 24 cycles to include in ranking (2+ years of monthly OpEx)

Output: data/profile/pin_reliability_scan.parquet (per-cycle) and per-ticker summary.
"""
from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"
OUT_DIR = ROOT / "data/profile"


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday: Monday=0, Friday=4
    offset = (4 - d.weekday()) % 7
    first_friday = d + timedelta(days=offset)
    return first_friday + timedelta(days=14)


def monthly_opex(start_year: int, end_year: int) -> list[date]:
    out = []
    for y in range(start_year, end_year + 1):
        for m in range(1, 13):
            out.append(third_friday(y, m))
    return out


def parse_exp(s: str) -> pd.Timestamp | None:
    try:
        p = s.split("/")
        return pd.Timestamp(year=int(p[2]), month=int(p[0]), day=int(p[1]))
    except Exception:
        return None


def compute_max_pain(chain: pd.DataFrame) -> float | None:
    """Given a chain snapshot (rows are strikes), return the max-pain strike."""
    c = chain.dropna(subset=["strike", "cOi", "pOi"]).copy()
    if c.empty:
        return None
    strikes = c["strike"].values
    call_oi = c["cOi"].values
    put_oi  = c["pOi"].values
    # For each candidate K (each existing strike), compute total pain
    best_K = None
    best_pain = None
    for K in strikes:
        call_pain = (call_oi * np.maximum(0.0, K - strikes)).sum()
        put_pain  = (put_oi  * np.maximum(0.0, strikes - K)).sum()
        total = call_pain + put_pain
        if best_pain is None or total < best_pain:
            best_pain = total
            best_K = float(K)
    return best_K


def strike_spacing_atm(chain: pd.DataFrame, spot: float) -> float | None:
    c = chain.dropna(subset=["strike", "stkPx"]).copy()
    if c.empty:
        return None
    c = c.sort_values("strike")
    # within 10% of spot
    near = c[(c["strike"] >= spot * 0.9) & (c["strike"] <= spot * 1.1)]
    if len(near) < 2:
        near = c
    gaps = np.diff(near["strike"].values)
    gaps = gaps[gaps > 0]
    if len(gaps) == 0:
        return None
    return float(np.median(gaps))


def scan_ticker(ticker: str, opex_dates: list[pd.Timestamp]) -> list[dict]:
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path, columns=["trade_date","expirDate","strike","stkPx","cOi","pOi"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["exp_dt"] = df["expirDate"].map(parse_exp)
    df = df.dropna(subset=["exp_dt"])

    # Find orat expirDate strings that match monthly third-Fridays (±1 day tolerance)
    exp_strs: dict[pd.Timestamp, str] = {}
    for s in df["expirDate"].unique():
        d = parse_exp(s)
        if d is None:
            continue
        # match to nearest opex date
        for opex in opex_dates:
            if abs((d - opex).days) <= 1:
                exp_strs[opex] = s
                break

    rows = []
    for opex, exp_str in exp_strs.items():
        sub = df[df["expirDate"] == exp_str]
        if sub.empty:
            continue
        # Find T-1 (trading day immediately before expiry)
        # Use the latest trade_date in sub that is strictly before the expiration
        sub_pre = sub[sub["trade_date"] < opex]
        if sub_pre.empty:
            continue
        t_minus_1 = sub_pre["trade_date"].max()
        chain_t1 = sub_pre[sub_pre["trade_date"] == t_minus_1]
        if chain_t1.empty:
            continue
        mp = compute_max_pain(chain_t1)
        if mp is None:
            continue
        spot_t1 = float(chain_t1["stkPx"].iloc[0])
        spacing = strike_spacing_atm(chain_t1, spot_t1)
        if spacing is None or spacing <= 0:
            continue

        # Close at expiry: find the trade_date == opex (or nearest ≤ opex+1) for this ticker
        # Use ticker's last-available snapshot with trade_date == opex
        final = df[df["trade_date"] == opex]
        if final.empty:
            continue
        spot_close = float(final["stkPx"].iloc[0])

        abs_dist = abs(spot_close - mp)
        abs_pct = abs_dist / spot_close
        rows.append({
            "ticker": ticker,
            "opex": opex,
            "t_minus_1": t_minus_1,
            "mp_t1": mp,
            "spot_t1": spot_t1,
            "spot_close": spot_close,
            "strike_spacing": spacing,
            "abs_dist": abs_dist,
            "abs_pct": abs_pct,
            "within_1pct": abs_pct <= 0.01,
            "within_2pct": abs_pct <= 0.02,
            "within_1_strike": abs_dist <= spacing,
            "within_2_strikes": abs_dist <= 2 * spacing,
        })
    return rows


def main() -> None:
    uni = pd.read_parquet(UNIVERSE)
    tickers = uni["ticker"].tolist()
    print(f"Scanning {len(tickers)} tickers for monthly-OpEx pin reliability")

    # Build candidate OpEx list (2013-2026)
    opex_all = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]

    all_rows = []
    for i, t in enumerate(tickers, 1):
        rows = scan_ticker(t, opex_all)
        if rows:
            all_rows.extend(rows)
        if i % 25 == 0:
            print(f"  ...{i}/{len(tickers)}  rows so far: {len(all_rows):,}")

    per_cycle = pd.DataFrame(all_rows)
    print(f"\nTotal cycles: {len(per_cycle):,} across {per_cycle['ticker'].nunique()} tickers")

    # Per-ticker summary
    g = per_cycle.groupby("ticker").agg(
        n=("opex", "count"),
        median_abs_pct=("abs_pct", "median"),
        mean_abs_pct=("abs_pct", "mean"),
        within_1pct=("within_1pct", "mean"),
        within_2pct=("within_2pct", "mean"),
        within_1_strike=("within_1_strike", "mean"),
        within_2_strikes=("within_2_strikes", "mean"),
        median_spacing=("strike_spacing", "median"),
        median_spot=("spot_close", "median"),
    ).reset_index()

    # Sample-size filter: need ≥ 24 cycles (pre-registered)
    qualified = g[g["n"] >= 24].copy()
    qualified = qualified.merge(uni[["ticker","cluster","sector"]], on="ticker", how="left")
    print(f"Qualified tickers (≥24 cycles): {len(qualified)}")

    # Ranked outputs
    print()
    print("═══ TOP 20 most reliable pinners (by median |close − MP| / spot) ═══")
    top = qualified.sort_values("median_abs_pct").head(20)
    print(top[["ticker","cluster","sector","n","median_abs_pct","mean_abs_pct","within_1pct","within_2pct","median_spot","median_spacing"]].to_string(index=False, float_format=lambda x: f"{x:.3f}" if isinstance(x, float) else str(x)))
    print()

    print("═══ TOP 20 by fraction within 1% of MP ═══")
    top_within = qualified.sort_values("within_1pct", ascending=False).head(20)
    print(top_within[["ticker","cluster","sector","n","within_1pct","within_2pct","median_abs_pct"]].to_string(index=False, float_format=lambda x: f"{x:.3f}" if isinstance(x, float) else str(x)))
    print()

    print("═══ BOTTOM 15 (unreliable — farthest from MP) ═══")
    bottom = qualified.sort_values("median_abs_pct", ascending=False).head(15)
    print(bottom[["ticker","cluster","sector","n","median_abs_pct","within_1pct","median_spot"]].to_string(index=False, float_format=lambda x: f"{x:.3f}" if isinstance(x, float) else str(x)))
    print()

    # By cluster
    print("═══ Aggregate by cluster ═══")
    by_cluster = qualified.groupby("cluster").agg(
        n_tickers=("ticker", "count"),
        median_of_medians=("median_abs_pct", "median"),
        median_within_1pct=("within_1pct", "median"),
    ).reset_index()
    print(by_cluster.to_string(index=False, float_format=lambda x: f"{x:.3f}" if isinstance(x, float) else str(x)))
    print()

    # Save
    per_cycle.to_parquet(OUT_DIR / "pin_reliability_per_cycle.parquet", index=False)
    qualified.to_parquet(OUT_DIR / "pin_reliability_by_ticker.parquet", index=False)
    print("wrote:")
    print(f"  {(OUT_DIR / 'pin_reliability_per_cycle.parquet').relative_to(ROOT)}")
    print(f"  {(OUT_DIR / 'pin_reliability_by_ticker.parquet').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
