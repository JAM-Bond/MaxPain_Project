"""
ZEBRA universe expansion — pre-backtest screen.

Applies hard pre-screen gates from ZEBRA_UNIVERSE_EXPANSION_PREREG.md to the
163-ticker ORATS by_ticker universe (minus the current ZEBRA Tier 1+2 cohort)
and writes a candidate parquet for user review BEFORE the backtest runs.

Data sources:
  - Spot + 1y daily OHLCV: Schwab market data API (live)
  - Strike density: ORATS by_ticker (latest snapshot)
  - Earnings: yfinance (Schwab does not expose earnings)
  - Sector: yfinance Ticker.info

Hard gates:
  1. Spot in [$20, $100]              (Schwab live quote)
  2. 20d avg share volume > 1M        (Schwab pricehistory)
  3. Spot above both 200dma + 50dma   (Schwab pricehistory)
  4. 6-month return > 0%              (Schwab pricehistory)
  5. Strike density at 75-DTE: ≥5 strikes within ±15% of spot (ORATS)
  6. No earnings within next 75 days  (yfinance via earnings_calendar.py)
  7. ORATS coverage ≥ 5 years         (by_ticker date range)

Output: data/profile/zebra_universe_expansion_candidates.parquet
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path.home() / "MaxPain_Project"
METAL_ROOT = Path.home() / "Metal_Project"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(METAL_ROOT))  # for Schwab.auth

from scripts.qualifier.earnings_calendar import upcoming_earnings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("zebra_screen")

ORATS_BY_TICKER = ROOT / "data/orats/by_ticker"
OUTPUT_PATH = ROOT / "data/profile/zebra_universe_expansion_candidates.parquet"

EXISTING_ZEBRA_COHORT = {
    # Tier 1
    "SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
    # Tier 2
    "DIA", "IWM", "GLD", "TJX", "GE", "WMT", "AMD", "PLTR",
}

SPOT_MIN = 20.0
SPOT_MAX = 100.0
MIN_VOLUME_20D = 1_000_000
MIN_COVERAGE_YEARS = 5.0
ZEBRA_DTE = 75
DTE_TOLERANCE_DAYS = 15  # accept 60-90 DTE expirations
STRIKE_BAND_PCT = 0.15
MIN_STRIKES_IN_BAND = 5
EARNINGS_HORIZON_DAYS = 75


def list_orats_tickers() -> list[str]:
    return sorted(p.stem for p in ORATS_BY_TICKER.glob("*.parquet"))


def screen_orats_layer(ticker: str, spot: float) -> dict | None:
    """Read ORATS by_ticker and apply: coverage years, strike density at 75-DTE.

    Spot comes from Schwab (live), passed in. ORATS spot is NOT used for the budget gate
    because the latest ORATS file may be 1-10 days stale.

    Returns dict of measured fields if both ORATS gates pass; None on read error.
    """
    path = ORATS_BY_TICKER / f"{ticker}.parquet"
    try:
        df = pd.read_parquet(path, columns=["trade_date", "expirDate", "yte", "strike", "stkPx"])
    except Exception as e:
        log.debug("%s: cannot read parquet (%s)", ticker, e)
        return None

    if df.empty:
        return None

    latest_date = df["trade_date"].max()
    earliest_date = df["trade_date"].min()
    coverage_years = (latest_date - earliest_date).days / 365.25

    # Coverage gate
    if coverage_years < MIN_COVERAGE_YEARS:
        return {"ticker": ticker, "fail_reason": f"coverage {coverage_years:.1f}y < {MIN_COVERAGE_YEARS}",
                "coverage_years": coverage_years, "strikes_in_band": None}

    latest_slice = df[df["trade_date"] == latest_date]
    orats_spot = float(latest_slice["stkPx"].iloc[0])

    # Strike density at 75-DTE on latest snapshot, banded around LIVE Schwab spot
    target_yte = ZEBRA_DTE / 365.0
    yte_min = (ZEBRA_DTE - DTE_TOLERANCE_DAYS) / 365.0
    yte_max = (ZEBRA_DTE + DTE_TOLERANCE_DAYS) / 365.0
    near = latest_slice[(latest_slice["yte"] >= yte_min) & (latest_slice["yte"] <= yte_max)]

    if near.empty:
        return {"ticker": ticker, "fail_reason": f"no expiration in 60-90 DTE on {latest_date.date()}",
                "coverage_years": coverage_years, "strikes_in_band": 0,
                "orats_spot": orats_spot, "latest_orats_date": str(latest_date.date())}

    near_grp = near.groupby("expirDate")["yte"].mean().reset_index()
    near_grp["diff"] = (near_grp["yte"] - target_yte).abs()
    chosen_exp = near_grp.sort_values("diff").iloc[0]["expirDate"]

    chosen = near[near["expirDate"] == chosen_exp]
    band_lo = spot * (1 - STRIKE_BAND_PCT)
    band_hi = spot * (1 + STRIKE_BAND_PCT)
    strikes_in_band = chosen[(chosen["strike"] >= band_lo) & (chosen["strike"] <= band_hi)]["strike"].nunique()

    if strikes_in_band < MIN_STRIKES_IN_BAND:
        return {"ticker": ticker, "fail_reason": f"only {strikes_in_band} strikes in ±15% band at chosen exp",
                "coverage_years": coverage_years, "strikes_in_band": strikes_in_band,
                "orats_spot": orats_spot, "latest_orats_date": str(latest_date.date())}

    return {
        "ticker": ticker,
        "fail_reason": None,
        "coverage_years": round(coverage_years, 2),
        "strikes_in_band": strikes_in_band,
        "chosen_expiration": str(chosen_exp),
        "orats_spot": round(orats_spot, 2),
        "latest_orats_date": str(latest_date.date()),
    }


def fetch_schwab_quotes(symbols: list[str]) -> dict[str, float]:
    """Bulk live quote fetch from Schwab /marketdata/v1/quotes.
    Returns {symbol: lastPrice}. Empty dict on auth failure.
    Mirrors Metal_Project/scripts/pipeline/update_close_prices.py:39.
    """
    try:
        from Schwab.auth import get_valid_token
    except Exception as e:
        log.error("Schwab auth import failed: %s", e)
        return {}
    try:
        token = get_valid_token()
    except Exception as e:
        log.error("Schwab token fetch failed: %s", e)
        return {}

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    url = "https://api.schwabapi.com/marketdata/v1/quotes"

    out = {}
    # Schwab accepts comma-joined symbol lists; chunk to be safe
    CHUNK = 50
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        try:
            resp = requests.get(url, headers=headers,
                                params={"symbols": ",".join(chunk), "fields": "quote"},
                                timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("Schwab /quotes chunk %d failed: %s", i, e)
            continue
        for sym, info in data.items():
            quote = info.get("quote", {})
            px = quote.get("lastPrice") or quote.get("regularMarketLastPrice")
            if px:
                out[sym.upper()] = float(px)
    return out


def fetch_schwab_history_1y(symbol: str) -> pd.DataFrame | None:
    """Fetch ~1y daily OHLCV from Schwab pricehistory. Returns DataFrame or None."""
    try:
        from Schwab.auth import get_valid_token
    except Exception:
        return None
    try:
        token = get_valid_token()
    except Exception:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    url = "https://api.schwabapi.com/marketdata/v1/pricehistory"
    params = {
        "symbol": symbol,
        "periodType": "year",
        "period": 1,
        "frequencyType": "daily",
        "frequency": 1,
        "needExtendedHoursData": False,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code != 200:
            return None
        candles = resp.json().get("candles", [])
    except Exception:
        return None
    if not candles:
        return None
    rows = [{
        "date": datetime.utcfromtimestamp(c["datetime"] / 1000).date(),
        "close": c["close"], "volume": c["volume"],
    } for c in candles]
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df


def screen_schwab_layer(tickers: list[str], spots: dict[str, float]) -> pd.DataFrame:
    """Per ticker: 1y history → 200dma/50dma/20d_vol/6mo_ret + spot budget gate."""
    log.info("Schwab pricehistory: %d tickers, 1y daily each", len(tickers))
    rows = []
    for i, t in enumerate(tickers, 1):
        spot = spots.get(t)
        if spot is None:
            rows.append({"ticker": t, "fail_reason": "Schwab quote missing"})
            continue

        if not (SPOT_MIN <= spot <= SPOT_MAX):
            rows.append({"ticker": t, "spot_live": round(spot, 2),
                         "fail_reason": f"spot ${spot:.2f} outside [${SPOT_MIN:.0f}, ${SPOT_MAX:.0f}]"})
            continue

        df = fetch_schwab_history_1y(t)
        if df is None or len(df) < 200:
            rows.append({"ticker": t, "spot_live": round(spot, 2),
                         "fail_reason": f"Schwab history: {len(df) if df is not None else 0} bars"})
            continue

        close = df["close"]
        volume = df["volume"]
        ma50 = float(close.iloc[-50:].mean())
        ma200 = float(close.iloc[-200:].mean())
        avg_vol_20d = float(volume.iloc[-20:].mean())
        ret_6m = float(close.iloc[-1] / close.iloc[-126] - 1) if len(close) >= 126 else None

        fail = None
        if avg_vol_20d < MIN_VOLUME_20D:
            fail = f"avg_vol_20d {avg_vol_20d:,.0f} < 1M"
        elif spot < ma200:
            fail = f"spot ${spot:.2f} < 200dma ${ma200:.2f}"
        elif spot < ma50:
            fail = f"spot ${spot:.2f} < 50dma ${ma50:.2f}"
        elif ret_6m is None or ret_6m <= 0:
            fail = f"6mo return {ret_6m:.1%}" if ret_6m is not None else "6mo return n/a"

        rows.append({
            "ticker": t,
            "spot_live": round(spot, 2),
            "ma50": round(ma50, 2),
            "ma200": round(ma200, 2),
            "avg_vol_20d": int(avg_vol_20d),
            "ret_6m": round(ret_6m, 4) if ret_6m is not None else None,
            "fail_reason": fail,
        })
        if i % 10 == 0:
            log.info("  Schwab progress: %d/%d", i, len(tickers))

    return pd.DataFrame(rows)


def screen_earnings_layer(tickers: list[str], today: date) -> dict[str, str | None]:
    """For each ticker, return None if no earnings within 75 days, else fail string."""
    log.info("yfinance earnings calendar: %d tickers, %d-day horizon", len(tickers), EARNINGS_HORIZON_DAYS)
    df = upcoming_earnings(tickers, today, window_days=EARNINGS_HORIZON_DAYS)
    out = {t: None for t in tickers}
    if df.empty:
        return out
    for sym, grp in df.groupby("ticker"):
        first = grp["earnings_date"].min()
        out[sym] = f"earnings {first} within 75 days"
    return out


def fetch_sectors(tickers: list[str]) -> dict[str, str]:
    """Look up GICS sector for surviving tickers via yfinance Ticker.info."""
    import yfinance as yf
    sectors = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            sectors[t] = info.get("sector") or "Unknown"
        except Exception:
            sectors[t] = "Unknown"
    return sectors


def main():
    today = date.today()
    log.info("ZEBRA universe expansion screen — run_date=%s", today)

    # ── Layer 0: ORATS coverage minus existing cohort ──
    all_tickers = list_orats_tickers()
    candidates = [t for t in all_tickers if t not in EXISTING_ZEBRA_COHORT]
    log.info("Layer 0: %d ORATS tickers, %d in existing cohort, %d candidates",
             len(all_tickers), len(EXISTING_ZEBRA_COHORT), len(candidates))

    # ── Schwab live quotes for all candidates (drives Layer 1 strike-band centering) ──
    log.info("Schwab quotes: bulk fetch for %d candidates", len(candidates))
    spots = fetch_schwab_quotes(candidates)
    log.info("Schwab quotes: %d/%d returned", len(spots), len(candidates))
    if not spots:
        log.error("Schwab quote fetch failed — aborting. Re-auth: `python3.11 Schwab/auth.py --force-reauth` from Metal_Project")
        return

    # ── Layer 1: ORATS gates (coverage + strike density centered on LIVE spot) ──
    log.info("Layer 1: ORATS gates (coverage, strike density at 75-DTE)")
    layer1 = []
    for t in candidates:
        spot = spots.get(t)
        if spot is None:
            layer1.append({"ticker": t, "fail_reason": "no Schwab quote",
                           "coverage_years": None, "strikes_in_band": None})
            continue
        r = screen_orats_layer(t, spot)
        if r is not None:
            layer1.append(r)
    layer1_df = pd.DataFrame(layer1)
    layer1_pass = layer1_df[layer1_df["fail_reason"].isna()]
    log.info("Layer 1: %d → %d pass", len(layer1_df), len(layer1_pass))

    if layer1_pass.empty:
        log.warning("No tickers passed layer 1; aborting.")
        layer1_df.to_parquet(OUTPUT_PATH, index=False)
        return

    # ── Layer 2: Schwab pricehistory gates (volume, MAs, 6mo return) ──
    log.info("Layer 2: Schwab pricehistory gates (volume, MAs, 6mo return)")
    survivors_l1 = layer1_pass["ticker"].tolist()
    layer2_df = screen_schwab_layer(survivors_l1, spots)
    layer2_pass = layer2_df[layer2_df["fail_reason"].isna()]
    log.info("Layer 2: %d → %d pass", len(layer2_df), len(layer2_pass))

    if layer2_pass.empty:
        log.warning("No tickers passed layer 2; writing partial output.")
        merged = layer1_df.merge(layer2_df, on="ticker", how="left", suffixes=("_l1", "_l2"))
        merged.to_parquet(OUTPUT_PATH, index=False)
        return

    # ── Layer 3: earnings horizon ──
    survivors_l2 = layer2_pass["ticker"].tolist()
    earnings_fails = screen_earnings_layer(survivors_l2, today)
    earn_df = pd.DataFrame([
        {"ticker": t, "earnings_fail": earnings_fails.get(t)} for t in survivors_l2
    ])
    earn_pass = earn_df[earn_df["earnings_fail"].isna()]
    log.info("Layer 3: %d → %d pass (no earnings in 75d)", len(earn_df), len(earn_pass))

    # ── Layer 4: sector lookup for final survivors ──
    final_tickers = earn_pass["ticker"].tolist()
    log.info("Layer 4: sector lookup for %d final survivors", len(final_tickers))
    sectors = fetch_sectors(final_tickers)
    sec_df = pd.DataFrame([{"ticker": t, "sector": sectors[t]} for t in final_tickers])

    # ── Build merged output ──
    out = (
        layer1_df.rename(columns={"fail_reason": "fail_l1"})
        .merge(layer2_df.rename(columns={"fail_reason": "fail_l2"}), on="ticker", how="left")
        .merge(earn_df, on="ticker", how="left")
        .merge(sec_df, on="ticker", how="left")
    )
    out["passed_all"] = out["ticker"].isin(final_tickers)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUTPUT_PATH, index=False)
    log.info("Wrote %s — %d rows total, %d passed all gates", OUTPUT_PATH, len(out), out["passed_all"].sum())

    # ── Print summary ──
    print("\n" + "=" * 70)
    print(f"ZEBRA UNIVERSE EXPANSION SCREEN  ({today})")
    print("=" * 70)
    print(f"Total ORATS-covered candidates (minus existing cohort): {len(candidates)}")
    print(f"Layer 1 (ORATS: spot/coverage/strikes):  {len(layer1_pass)}/{len(layer1_df)}")
    print(f"Layer 2 (yfinance: vol/MAs/6mo ret):     {len(layer2_pass)}/{len(layer2_df)}")
    print(f"Layer 3 (no earnings in next 75d):       {len(earn_pass)}/{len(earn_df)}")
    print(f"FINAL CANDIDATES: {len(final_tickers)}")
    print()

    if final_tickers:
        survivors = (
            out[out["passed_all"]]
            .sort_values(["sector", "ticker"])
            [["ticker", "sector", "spot_live", "orats_spot", "ma200", "avg_vol_20d", "ret_6m", "coverage_years", "strikes_in_band"]]
        )
        print(survivors.to_string(index=False))
        print()
        print("Sector distribution:")
        print(out[out["passed_all"]]["sector"].value_counts().to_string())
    else:
        print("No survivors. See parquet for per-ticker fail reasons.")


if __name__ == "__main__":
    main()
