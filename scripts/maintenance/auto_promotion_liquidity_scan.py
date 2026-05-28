"""Auto-promotion pipeline Stage 1 — daily liquidity scan.

Reads the latest raw ORATS daily parquet at
`data/orats/parquet/year=YYYY/month=MM/YYYY-MM-DD.parquet`. Filters per-ticker
to the sealed liquidity gates (front-month OI ≥ 10K, vol ≥ 1K, ATM bid-ask
≤ 10%, spot ∈ [$5, $1000]). Writes one snapshot per day to
`data/profile/auto_promotion/liquidity_snapshot_YYYY-MM-DD.parquet`.

Pre-reg: docs/AUTO_PROMOTION_PIPELINE_PREREG.md §2 Stage 1.
Cron: 22:30 ET weekdays (before the nightly walk-forward driver at 22:35).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.auto_promotion import (  # noqa: E402
    AUTO_PROMOTION_DIR,
    LIQ_FRONT_MONTH_OI_MIN,
    LIQ_AVG_DAILY_VOL_MIN,
    LIQ_ATM_BIDASK_PCT_MAX,
    LIQ_SPOT_MIN,
    LIQ_SPOT_MAX,
)

RAW = ROOT / "data/orats/parquet"


def find_latest_daily_parquet() -> Path | None:
    """Return the most recent daily ORATS parquet."""
    files = sorted(RAW.rglob("*.parquet"))
    return files[-1] if files else None


def compute_liquidity_scores(df: pd.DataFrame, data_date: date | None = None) -> pd.DataFrame:
    """Compute per-ticker liquidity metrics + pass/fail for each gate.

    Measures liquidity in the 30-90 DTE monthly-cycle window — matches
    our actual trade horizons (45-DTE entries through 75-DTE ZEBRAs)
    rather than the nearest weekly contract (often wider bid-ask).

    df must have columns: ticker, expirDate, strike, stkPx, delta,
                          cBidPx, cAskPx, cOi, cVolu,
                          pBidPx, pAskPx, pOi, pVolu.
    data_date: the date the parquet represents (NOT today, since the
               nightly cron reads yesterday's parquet). If None, uses
               max(expirDate) - 365 days as a fallback estimate.

    Returns dataframe with one row per ticker:
      ticker, spot, front_month_oi, avg_volume, atm_bidask_pct, passes
    """
    df = df.copy()
    df["expirDate"] = pd.to_datetime(df["expirDate"], format="%m/%d/%Y", errors="coerce")
    if data_date is None:
        # Fallback: assume max expirDate ~ 1 year out
        data_date = (df["expirDate"].max() - pd.Timedelta(days=365)).date()
    data_ts = pd.Timestamp(data_date)

    # 30-90 DTE window relative to the data date
    df["dte"] = (df["expirDate"] - data_ts).dt.days
    df = df[(df["dte"] >= 30) & (df["dte"] <= 90)]
    if df.empty:
        return pd.DataFrame(columns=[
            "ticker", "spot", "front_month_oi", "avg_volume",
            "atm_bidask_pct", "gate_oi", "gate_vol", "gate_bidask",
            "gate_spot", "passes",
        ])

    # Per-ticker aggregates across all expirations in window
    agg_rows = []
    for ticker, grp in df.groupby("ticker"):
        spot = float(grp["stkPx"].iloc[0]) if not grp.empty else 0.0
        # Total OI + total volume across all 30-90 DTE strikes
        total_oi = float(grp["cOi"].fillna(0).sum() + grp["pOi"].fillna(0).sum())
        total_vol = float(grp["cVolu"].fillna(0).sum() + grp["pVolu"].fillna(0).sum())
        # ATM bid-ask: per-expiration nearest-to-spot strike, then take the
        # MIN spread across expirations (best-available liquidity in the window).
        # Taking the min rather than averaging avoids a single illiquid weekly
        # dragging the metric in a multi-weekly window.
        atm_pcts = []
        for exp, exp_grp in grp.groupby("expirDate"):
            exp_grp2 = exp_grp.copy()
            exp_grp2["dist_to_spot"] = (exp_grp2["strike"] - spot).abs()
            atm_row = exp_grp2.sort_values("dist_to_spot").iloc[0]
            c_mid = ((atm_row["cBidPx"] or 0) + (atm_row["cAskPx"] or 0)) / 2.0
            c_spread = (atm_row["cAskPx"] or 0) - (atm_row["cBidPx"] or 0)
            p_mid = ((atm_row["pBidPx"] or 0) + (atm_row["pAskPx"] or 0)) / 2.0
            p_spread = (atm_row["pAskPx"] or 0) - (atm_row["pBidPx"] or 0)
            atm_pct = max(
                c_spread / c_mid if c_mid > 0 else 999.0,
                p_spread / p_mid if p_mid > 0 else 999.0,
            )
            atm_pcts.append(atm_pct)
        best_atm = min(atm_pcts) if atm_pcts else 999.0
        agg_rows.append({
            "ticker": ticker,
            "spot": spot,
            "front_month_oi": total_oi,
            "avg_volume": total_vol,
            "atm_bidask_pct": best_atm,
        })
    out = pd.DataFrame(agg_rows)
    if out.empty:
        out["gate_oi"] = out["gate_vol"] = out["gate_bidask"] = out["gate_spot"] = out["passes"] = []
        return out

    out["gate_oi"] = out["front_month_oi"] >= LIQ_FRONT_MONTH_OI_MIN
    out["gate_vol"] = out["avg_volume"] >= LIQ_AVG_DAILY_VOL_MIN
    out["gate_bidask"] = out["atm_bidask_pct"] <= LIQ_ATM_BIDASK_PCT_MAX
    out["gate_spot"] = (out["spot"] >= LIQ_SPOT_MIN) & (out["spot"] <= LIQ_SPOT_MAX)
    out["passes"] = out["gate_oi"] & out["gate_vol"] & out["gate_bidask"] & out["gate_spot"]
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("liq_scan")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None,
                    help="ISO date for the ORATS file (default: latest available)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print summary without writing snapshot parquet")
    args = ap.parse_args()

    if args.date:
        target = pd.Timestamp(args.date)
        candidate = (RAW / f"year={target.year}" / f"month={target.month:02d}"
                      / f"{args.date}.parquet")
        if not candidate.exists():
            log.error("No ORATS parquet at %s", candidate)
            sys.exit(1)
        parquet_path = candidate
    else:
        parquet_path = find_latest_daily_parquet()
        if not parquet_path:
            log.error("No ORATS parquets found in %s", RAW)
            sys.exit(1)
    log.info("Reading: %s", parquet_path)

    KEEP = ["ticker", "expirDate", "strike", "stkPx", "delta",
            "cBidPx", "cAskPx", "cOi", "cVolu",
            "pBidPx", "pAskPx", "pOi", "pVolu"]
    df = pd.read_parquet(parquet_path, columns=KEEP)
    log.info("Read %d rows / %d distinct tickers",
             len(df), df["ticker"].nunique())

    log.info("Computing liquidity scores...")
    # Use the date encoded in the parquet filename (YYYY-MM-DD.parquet)
    data_date_iso = parquet_path.stem
    try:
        data_date = datetime.strptime(data_date_iso, "%Y-%m-%d").date()
    except ValueError:
        data_date = None
    scores = compute_liquidity_scores(df, data_date=data_date)
    log.info("Per-ticker scores: %d", len(scores))

    n_pass = int(scores["passes"].sum())
    n_fail = len(scores) - n_pass
    log.info("Liquidity-passing: %d  |  failing: %d", n_pass, n_fail)
    log.info("  Gate-by-gate pass counts:")
    for g in ["gate_oi", "gate_vol", "gate_bidask", "gate_spot"]:
        log.info("    %s: %d (%.1f%%)", g, int(scores[g].sum()),
                 100 * scores[g].mean())

    if args.dry_run:
        log.info("DRY RUN — not writing snapshot")
        # Show top 5 passing by OI score
        top = scores[scores["passes"]].sort_values("front_month_oi", ascending=False).head(5)
        log.info("Top 5 passing by front-month OI:\n%s", top[["ticker", "spot", "front_month_oi"]])
    else:
        snap_date = parquet_path.stem  # YYYY-MM-DD
        AUTO_PROMOTION_DIR.mkdir(parents=True, exist_ok=True)
        out_path = AUTO_PROMOTION_DIR / f"liquidity_snapshot_{snap_date}.parquet"
        scores.to_parquet(out_path, index=False)
        log.info("Wrote snapshot to %s", out_path)


if __name__ == "__main__":
    main()
