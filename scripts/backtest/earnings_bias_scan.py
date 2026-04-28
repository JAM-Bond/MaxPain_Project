"""Earnings bias scan — does each ticker historically move up or down on earnings?

For each universe ticker + new additions, pull historical earnings dates from yfinance,
then cross-reference with ORATS stock prices to compute T-1 -> T+1 returns per event.
Aggregate per ticker: mean return, % positive, N events, std.

Returns a ranked list of "usually up" vs "usually down" names.
"""
from pathlib import Path
import logging
import time
import pandas as pd
import numpy as np
import yfinance as yf

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"
OUT = ROOT / "data/profile/earnings_bias_per_ticker.parquet"

# Skip ETFs (yfinance returns no earnings for ETFs)
SKIP = {"SPY", "SPX", "QQQ", "DIA", "IWM", "EEM", "EFA", "VEU", "AGG", "BND",
        "HYG", "JNK", "BKLN", "TLT", "GLD", "SLV", "XLU", "XLV", "XLE", "XLF",
        "XLP", "XLK", "XLI", "XLC", "XLRE", "XLB", "XLY", "IYR", "VNQ", "XOP",
        "XBI", "GDX", "VXX", "UVXY", "KRE", "SMH", "KBE", "ARKK"}

# Include new expansion tickers
EXTRA = ["AMZN", "GOOGL", "NVDA", "GOLD", "AU", "KGC", "PAAS", "SLB", "BKR",
         "SCCO", "TECK", "NUE", "STLD", "CLF"]


logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("earnings_bias")


def build_price_series(ticker: str) -> pd.Series:
    """Daily stock close from ORATS by_ticker parquet. One value per trade_date."""
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return pd.Series(dtype=float)
    df = pd.read_parquet(path, columns=["trade_date", "stkPx"])
    # One stkPx per trade_date (same across strikes per ORATS convention)
    daily = df.drop_duplicates("trade_date").set_index("trade_date")["stkPx"]
    return daily.sort_index()


def earnings_return(prices: pd.Series, earnings_dt) -> float:
    """T-1 close -> T+1 close return. earnings_dt may be any time of day."""
    if prices.empty:
        return np.nan
    event_date = pd.Timestamp(earnings_dt).tz_localize(None).normalize() \
        if pd.Timestamp(earnings_dt).tz is not None \
        else pd.Timestamp(earnings_dt).normalize()
    # Find prev trading day (on-or-before event_date - 1 day)
    target_prev = event_date - pd.Timedelta(days=1)
    target_next = event_date + pd.Timedelta(days=1)
    prev_ix = prices.index[prices.index <= target_prev]
    next_ix = prices.index[prices.index >= target_next]
    if len(prev_ix) == 0 or len(next_ix) == 0:
        return np.nan
    prev_date = prev_ix[-1]
    next_date = next_ix[0]
    # Sanity: reject if bracketing prices are more than 5 calendar days from event
    # (guards against ORATS data gaps producing spurious returns)
    if (event_date - prev_date).days > 5 or (next_date - event_date).days > 5:
        return np.nan
    prev_price = prices.loc[prev_date]
    next_price = prices.loc[next_date]
    if prev_price <= 0 or np.isnan(prev_price) or np.isnan(next_price):
        return np.nan
    ret = float(next_price / prev_price - 1)
    # Additional guard: reject absurd returns (likely data artifact)
    if abs(ret) > 0.60:  # 60% in a T-1->T+1 window is the biggest plausible real move
        return np.nan
    return ret


def fetch_earnings_dates(ticker: str) -> list:
    """Pull yfinance earnings_dates. Returns a list of datetimes."""
    try:
        t = yf.Ticker(ticker)
        ed = t.earnings_dates
        if ed is None or ed.empty:
            return []
        # Only past dates with reported EPS (filter out forward/est-only)
        past = ed[ed["Reported EPS"].notna()]
        return list(past.index)
    except Exception as e:
        log.warning("%s earnings fetch err: %s", ticker, e)
        return []


def main() -> None:
    universe = pd.read_parquet(UNIVERSE)["ticker"].tolist()
    target = [t for t in universe if t not in SKIP]
    target = list(dict.fromkeys(target + EXTRA))
    log.info("Scanning %d tickers (skipping ETFs: %d)", len(target), len(SKIP))

    per_ticker = []
    for i, tkr in enumerate(target, 1):
        prices = build_price_series(tkr)
        if prices.empty:
            log.info("  [%d/%d] %s: no price data, skip", i, len(target), tkr)
            continue
        earnings = fetch_earnings_dates(tkr)
        if not earnings:
            log.info("  [%d/%d] %s: no earnings dates, skip", i, len(target), tkr)
            continue
        returns = [earnings_return(prices, e) for e in earnings]
        returns = [r for r in returns if pd.notna(r)]
        if len(returns) < 5:
            log.info("  [%d/%d] %s: only %d earnings returns, skip",
                     i, len(target), tkr, len(returns))
            continue
        arr = np.array(returns)
        per_ticker.append({
            "ticker": tkr,
            "N": len(arr),
            "mean_ret": round(arr.mean(), 4),
            "median_ret": round(np.median(arr), 4),
            "std_ret": round(arr.std(), 4),
            "pct_positive": round((arr > 0).mean(), 3),
            "worst": round(arr.min(), 4),
            "best": round(arr.max(), 4),
        })
        if i % 20 == 0:
            log.info("  [%d/%d] done %s (N=%d, mean=%+.3f, win=%.0f%%)",
                     i, len(target), tkr, len(arr), arr.mean(), (arr>0).mean()*100)
        time.sleep(0.1)  # polite delay to avoid rate limits

    df = pd.DataFrame(per_ticker)
    # Require minimum N for ranking
    df = df[df["N"] >= 10].copy()
    df = df.sort_values("pct_positive", ascending=False).reset_index(drop=True)

    print("\n" + "=" * 80)
    print("TOP 20 — usually goes UP on earnings (by % positive)")
    print("=" * 80)
    print(df.head(20).to_string(index=False))

    print("\n" + "=" * 80)
    print("BOTTOM 20 — usually goes DOWN on earnings (by % positive ascending)")
    print("=" * 80)
    print(df.tail(20).iloc[::-1].to_string(index=False))

    print("\n" + "=" * 80)
    print("STRONGEST MEAN-UP BIAS (mean T-1→T+1 return, min N=15)")
    print("=" * 80)
    df_strong = df[df["N"] >= 15].sort_values("mean_ret", ascending=False)
    print(df_strong.head(20).to_string(index=False))

    print("\n" + "=" * 80)
    print("STRONGEST MEAN-DOWN BIAS")
    print("=" * 80)
    print(df_strong.tail(20).iloc[::-1].to_string(index=False))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    log.info("Wrote %d tickers to %s", len(df), OUT)


if __name__ == "__main__":
    main()
