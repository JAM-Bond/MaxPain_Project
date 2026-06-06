"""SSGA Select Sector SPDR flow data — free daily shares-outstanding + NAV + AUM.

State Street publishes a daily "NAV history" XLSX per Select Sector SPDR that is
actually a time series of Date | NAV | Shares Outstanding | Total Net Assets,
back to 2003-12, 100% filled. ETF net flow is reconstructed as
    net_flow($) = Δ(shares outstanding) × NAV
since creation/redemption IS the change in shares outstanding.

Free, authoritative, no API key. Source + URL pattern documented in the
reference_ssga_sector_flow_data memory. Companion files at the same path:
holdings-daily-us-en-{t}.xlsx, pdhist-us-en-{t}.xlsx (premium/discount history).

NB: GLD (SPDR Gold, different site) and SLV (iShares) are NOT Select Sector
SPDRs and are not covered here — the 11 GICS sectors are.

Usage:
    from lib.ssga_flows import build_all, resample_flows
    daily, failed = build_all()
    monthly = resample_flows(daily, "ME")
"""
from __future__ import annotations

import io

import pandas as pd
import requests

SECTOR_SPDRS = ["XLE", "XLF", "XLK", "XLV", "XLI", "XLP", "XLY",
                "XLU", "XLB", "XLRE", "XLC"]

NAVHIST_URL = ("https://www.sectorspdrs.com/library-content/products/"
               "fund-data/etfs/us/navhist-us-en-{t}.xlsx")

_HEADERS = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                           "AppleWebKit/537.36")}


def fetch_navhist(ticker: str, timeout: int = 30) -> pd.DataFrame | None:
    """Daily [date, ticker, nav, shares_out, aum] for one Select Sector SPDR,
    ascending by date. Returns None on fetch/parse failure.

    The XLSX has Fund-Name/Ticker metadata in rows 0-2 and the column header in
    row 3; the numeric columns arrive as strings and are coerced.
    """
    url = NAVHIST_URL.format(t=ticker.lower())
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
    except Exception:
        return None
    if not r.ok or len(r.content) < 500:
        return None
    try:
        df = pd.read_excel(io.BytesIO(r.content), header=3)
    except Exception:
        return None
    if "Date" not in df.columns:
        return None
    df = df[df["Date"].notna()].copy()
    df["date"] = pd.to_datetime(df["Date"], format="%d-%b-%Y", errors="coerce")
    for src, dst in [("NAV", "nav"), ("Shares Outstanding", "shares_out"),
                     ("Total Net Assets", "aum")]:
        df[dst] = pd.to_numeric(df.get(src), errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    df["ticker"] = ticker
    return df[["date", "ticker", "nav", "shares_out", "aum"]]


def _split_factors(shares: pd.Series, nav: pd.Series) -> pd.Series:
    """Per-day share-split factor (1.0 except on split days).

    A split multiplies shares and divides NAV by the same clean factor, so AUM
    (= shares × nav) passes through CONTINUOUSLY while shares jump by ~2/3/4×.
    A genuine creation/redemption, by contrast, MOVES aum. So a day is a split
    iff shares jumped by a clean multiple AND aum was ~continuous through it.
    """
    sr = shares / shares.shift(1)
    aum_r = (shares * nav) / (shares * nav).shift(1)
    f = pd.Series(1.0, index=shares.index)
    is_split = ((sr >= 1.4) | (sr <= 0.71)) & (aum_r.sub(1.0).abs() < 0.15)
    snapped = sr.where(sr >= 1.4, other=1.0 / sr).round()      # nearest integer ≥1
    fwd = sr >= 1.4
    f.loc[is_split & fwd] = snapped[is_split & fwd]            # forward split: ×N
    f.loc[is_split & ~fwd] = 1.0 / snapped[is_split & ~fwd]    # reverse split: ÷N
    return f


def reconstruct_flows(df: pd.DataFrame) -> pd.DataFrame:
    """Add daily net flow ($) = Δ(split-adjusted shares) × split-adjusted nav.

    Splits are detected and removed first: the raw `Δshares × nav` reconstruction
    manufactures a phantom flow on every split day (e.g. a 2:1 split doubles
    shares and halves NAV → a fake +100%-of-AUM inflow). Shares are back-adjusted
    to TODAY's units (latest day unscaled) so Δ reflects only real
    creation/redemption; nav is scaled inversely so shares×nav = aum is preserved.
    The first row's flow is NaN (no prior day)."""
    df = df.sort_values("date").reset_index(drop=True).copy()
    f = _split_factors(df["shares_out"], df["nav"]).fillna(1.0)
    import numpy as np
    logf = np.log(f.to_numpy())
    csum = np.cumsum(logf)
    cumfactor_after = np.exp(csum[-1] - csum)                 # ∏ of splits AFTER day d
    adj_shares = df["shares_out"] * cumfactor_after
    adj_nav = df["nav"] / cumfactor_after
    df["flow"] = adj_shares.diff() * adj_nav
    return df


def build_all(tickers: list[str] | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Long-format daily flow store across the sector SPDRs.
    Returns (DataFrame[date,ticker,nav,shares_out,aum,flow], failed_tickers)."""
    tickers = tickers or SECTOR_SPDRS
    frames, failed = [], []
    for t in tickers:
        d = fetch_navhist(t)
        if d is None or d.empty:
            failed.append(t)
            continue
        frames.append(reconstruct_flows(d))
    if not frames:
        return pd.DataFrame(), failed
    return (pd.concat(frames, ignore_index=True)
            .sort_values(["ticker", "date"]).reset_index(drop=True), failed)


def resample_flows(daily: pd.DataFrame, freq: str = "ME") -> pd.DataFrame:
    """Resample to `freq` (default month-end): flow SUMMED, nav/shares_out/aum LAST."""
    out = []
    for t, g in daily.groupby("ticker"):
        g = g.set_index("date").sort_index()
        r = pd.DataFrame({
            "flow": g["flow"].resample(freq).sum(min_count=1),
            "nav": g["nav"].resample(freq).last(),
            "shares_out": g["shares_out"].resample(freq).last(),
            "aum": g["aum"].resample(freq).last(),
        })
        r["ticker"] = t
        out.append(r.reset_index())
    return pd.concat(out, ignore_index=True).sort_values(["ticker", "date"]).reset_index(drop=True)
