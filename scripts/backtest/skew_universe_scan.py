"""Build the skew-rich universe for the Jade Lizard study.

For each ticker in universe_v1, compute the historical mean of
(30Δ-equivalent put IV minus 30Δ-equivalent call IV) over the ORATS sample.
Names with positive mean skew (puts more expensive than calls) qualify.

Output: data/profile/skew_universe.parquet
"""
from pathlib import Path
import logging
import pandas as pd
import numpy as np

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"
OUT = ROOT / "data/profile/skew_universe.parquet"

# Exclude vol-toy / degenerate-skew names
SKIP = {"VIX", "VXX", "UVXY", "SVXY"}

# Target deltas for skew measurement: ~30Δ on each side
PUT_DELTA_TARGET = 0.70   # call delta = 0.70 → put delta = -0.30
CALL_DELTA_TARGET = 0.30  # call delta = 0.30 directly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("skew_scan")


def measure_ticker_skew(ticker: str) -> dict | None:
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(
        path,
        columns=["trade_date", "expirDate", "strike", "stkPx", "delta",
                 "cMidIv", "pMidIv"],
    )
    if df.empty:
        return None

    df = df.dropna(subset=["delta", "cMidIv", "pMidIv"])
    if df.empty:
        return None

    # For each (trade_date, expirDate), pick the row closest to PUT_DELTA_TARGET
    # and the row closest to CALL_DELTA_TARGET. Skew per snapshot =
    # pMidIv at put-delta-target − cMidIv at call-delta-target.
    df = df.copy()
    df["put_dist"] = (df["delta"] - PUT_DELTA_TARGET).abs()
    df["call_dist"] = (df["delta"] - CALL_DELTA_TARGET).abs()

    grouped = df.groupby(["trade_date", "expirDate"], sort=False)

    skew_samples = []
    for _, sub in grouped:
        if len(sub) < 2:
            continue
        put_row = sub.loc[sub["put_dist"].idxmin()]
        call_row = sub.loc[sub["call_dist"].idxmin()]
        if abs(put_row["delta"] - PUT_DELTA_TARGET) > 0.10:
            continue
        if abs(call_row["delta"] - CALL_DELTA_TARGET) > 0.10:
            continue
        skew = float(put_row["pMidIv"] - call_row["cMidIv"])
        if not np.isfinite(skew):
            continue
        skew_samples.append(skew)

    if len(skew_samples) < 100:
        return None

    arr = np.array(skew_samples)
    return {
        "ticker": ticker,
        "N_snapshots": len(arr),
        "mean_skew": float(arr.mean()),
        "median_skew": float(np.median(arr)),
        "pct_positive": float((arr > 0).mean()),
        "p10": float(np.quantile(arr, 0.10)),
        "p90": float(np.quantile(arr, 0.90)),
    }


def main() -> None:
    universe = pd.read_parquet(UNIVERSE)["ticker"].tolist()
    target = [t for t in universe if t not in SKIP]
    log.info("Scanning %d tickers (skipping %d vol-toy)", len(target), len(SKIP))

    rows = []
    for i, t in enumerate(target, 1):
        result = measure_ticker_skew(t)
        if result is None:
            log.info("  [%d/%d] %s: no usable skew samples", i, len(target), t)
            continue
        rows.append(result)
        if i % 25 == 0:
            log.info("  [%d/%d] %s: mean_skew=%+.4f, pct_pos=%.0f%%, N=%d",
                     i, len(target), t,
                     result["mean_skew"], result["pct_positive"]*100,
                     result["N_snapshots"])

    df = pd.DataFrame(rows)
    df = df.sort_values("mean_skew", ascending=False)

    print("\n" + "=" * 80)
    print(f"TOTAL ANALYZED: {len(df)} tickers")
    print(f"POSITIVE mean skew: {(df['mean_skew'] > 0).sum()} tickers")
    print(f"NEGATIVE mean skew: {(df['mean_skew'] <= 0).sum()} tickers")
    print("=" * 80)
    print()

    print("TOP 30 — Highest Positive Skew (put IV most above call IV):")
    print(df.head(30).to_string(index=False))
    print()
    print("BOTTOM 20 — Negative or Lowest Skew (excluded from Jade Lizard universe):")
    print(df.tail(20).to_string(index=False))

    # Save the skew-rich subset for the Jade Lizard study
    skew_rich = df[df["mean_skew"] > 0].copy()
    skew_rich.to_parquet(OUT, index=False)
    log.info("Wrote %d skew-rich tickers to %s", len(skew_rich), OUT)


if __name__ == "__main__":
    main()
