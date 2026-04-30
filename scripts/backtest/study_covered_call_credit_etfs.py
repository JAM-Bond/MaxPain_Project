#!/usr/bin/env python3.11
"""
Covered-call backtest on credit ETFs (BKLN, TLT, JNK + HYG control).

Pre-registration: docs/COVERED_CALL_CREDIT_ETFS_PREREG.md (sealed 2026-04-30).

Per cycle (monthly OpEx → next monthly OpEx):
  - Long 100 shares at S0 (close, day after prior OpEx)
  - Short 1 call at 30Δ closest, expiring at next monthly OpEx
  - Held to expiry; no managed exit
  - P&L per share = (min(S1, K) - S0) + premium - slip + dividends_in_window

Comparison baseline (per ticker): bull_put 30Δ short / 0.5%-spot wing (with
$1 floor) at slip=0.10, same cycles, held to expiry. Capital-adjusted to
make CC vs spread comparable.

Outputs:
  data/profile/covered_call_credit_etfs.parquet            (per-cycle rows)
  data/profile/covered_call_credit_etfs_scorecard.parquet  (per-ticker agg)
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.opex_calendar import monthly_opex_dates  # noqa: E402

ROOT = Path.home() / "MaxPain_Project"
TICKER_DIR = ROOT / "data/orats/by_ticker"
OUT_CYCLES = ROOT / "data/profile/covered_call_credit_etfs.parquet"
OUT_SCORE = ROOT / "data/profile/covered_call_credit_etfs_scorecard.parquet"

UNIVERSE = ["BKLN", "TLT", "JNK", "HYG"]  # HYG is control
SLIPS_CC = [0.05, 0.10]
SLIP_BP_BASELINE = 0.10
START_YEAR = 2013
END_YEAR = 2026
TARGET_DELTA_CALL = 0.30
TARGET_DELTA_PUT = -0.30
WING_PCT_OF_SPOT = 0.005  # 0.5% wing for bull_put baseline
WING_FLOOR = 1.00          # $1 minimum wing


# ─── Data loading ─────────────────────────────────────────────────────

def load_chain(ticker: str) -> pd.DataFrame:
    """Load full ORATS chain for a ticker. Returns DataFrame with parsed dates."""
    p = TICKER_DIR / f"{ticker}.parquet"
    df = pd.read_parquet(p)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["exp_dt"] = pd.to_datetime(df["expirDate"], format="%m/%d/%Y", errors="coerce")
    df = df.dropna(subset=["exp_dt"])
    df["dte"] = (df["exp_dt"] - df["trade_date"]).dt.days
    return df


def daily_close_series(chain: pd.DataFrame) -> pd.Series:
    """Daily stock close series (one row per trade_date)."""
    s = (chain.dropna(subset=["stkPx"])
              .drop_duplicates("trade_date")
              .sort_values("trade_date")
              .set_index("trade_date")["stkPx"])
    return s


def load_dividends(ticker: str) -> pd.DataFrame:
    """yfinance dividend history → DataFrame {ex_date, amount}.
    Empty DataFrame if yfinance unavailable or ticker has no divs."""
    try:
        import yfinance as yf
    except ImportError:
        return pd.DataFrame(columns=["ex_date", "amount"])
    try:
        divs = yf.Ticker(ticker).dividends
        if divs is None or divs.empty:
            return pd.DataFrame(columns=["ex_date", "amount"])
        out = pd.DataFrame({
            "ex_date": [pd.Timestamp(d).tz_localize(None).normalize() for d in divs.index],
            "amount": divs.values,
        })
        return out
    except Exception:
        return pd.DataFrame(columns=["ex_date", "amount"])


# ─── Cycle entry: 30Δ call selection + premium ───────────────────────

def find_30delta_call(chain: pd.DataFrame, entry_date: pd.Timestamp,
                      target_expiry: pd.Timestamp,
                      target_delta: float = TARGET_DELTA_CALL) -> dict | None:
    """For entry_date, find the OTM call (strike >= stkPx) whose delta is
    closest to target_delta. Returns dict with strike, mid, bid, ask, delta;
    None if no chain row matches.

    Filters to OTM-or-ATM only (strike >= stkPx) so we don't sell a deep ITM
    call against the stock — that would be economically equivalent to selling
    stock outright.
    """
    sub = chain[(chain["trade_date"] == entry_date)
                & (chain["exp_dt"] == target_expiry)].copy()
    if sub.empty:
        return None
    # Filter to OTM-or-ATM calls (strike at or above spot)
    sub = sub[sub["strike"] >= sub["stkPx"]]
    if sub.empty:
        return None
    sub["dist"] = (sub["delta"] - target_delta).abs()
    sub = sub.sort_values("dist")
    row = sub.iloc[0]
    bid = float(row["cBidPx"]) if pd.notna(row["cBidPx"]) else 0.0
    ask = float(row["cAskPx"]) if pd.notna(row["cAskPx"]) else 0.0
    if ask <= 0 or bid < 0:
        return None
    mid = (bid + ask) / 2
    return {
        "strike": float(row["strike"]),
        "delta": float(row["delta"]),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "stkPx": float(row["stkPx"]),
    }


def find_30delta_put(chain: pd.DataFrame, entry_date: pd.Timestamp,
                      target_expiry: pd.Timestamp,
                      target_put_delta: float = TARGET_DELTA_PUT) -> dict | None:
    """For entry_date, find the put with put_delta closest to target.

    ORATS schema: each row has both call and put pricing; the `delta`
    column is the call delta. Put delta = call_delta - 1, so a -0.30 put
    delta corresponds to a row where call_delta ≈ +0.70.
    """
    sub = chain[(chain["trade_date"] == entry_date)
                & (chain["exp_dt"] == target_expiry)].copy()
    if sub.empty:
        return None
    sub["put_delta"] = sub["delta"] - 1.0
    # Filter to OTM puts only (strike < stkPx)
    sub = sub[sub["strike"] < sub["stkPx"]]
    if sub.empty:
        return None
    sub["dist"] = (sub["put_delta"] - target_put_delta).abs()
    sub = sub.sort_values("dist")
    row = sub.iloc[0]
    bid = float(row["pBidPx"]) if pd.notna(row["pBidPx"]) else 0.0
    ask = float(row["pAskPx"]) if pd.notna(row["pAskPx"]) else 0.0
    if ask <= 0 or bid < 0:
        return None
    return {
        "strike": float(row["strike"]),
        "delta": float(row["put_delta"]),
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2,
        "stkPx": float(row["stkPx"]),
    }


def find_long_put(chain: pd.DataFrame, entry_date: pd.Timestamp,
                   target_expiry: pd.Timestamp, short_strike: float,
                   wing: float) -> dict | None:
    """Find the closest available put strike at short_strike - wing.
    Filters to OTM puts (strike < stkPx)."""
    target_strike = short_strike - wing
    sub = chain[(chain["trade_date"] == entry_date)
                & (chain["exp_dt"] == target_expiry)].copy()
    if sub.empty:
        return None
    sub = sub[sub["strike"] < sub["stkPx"]]
    if sub.empty:
        return None
    sub["dist"] = (sub["strike"] - target_strike).abs()
    sub = sub.sort_values("dist")
    row = sub.iloc[0]
    bid = float(row["pBidPx"]) if pd.notna(row["pBidPx"]) else 0.0
    ask = float(row["pAskPx"]) if pd.notna(row["pAskPx"]) else 0.0
    if ask <= 0:
        return None
    return {
        "strike": float(row["strike"]),
        "bid": bid,
        "ask": ask,
        "mid": (bid + ask) / 2,
    }


# ─── Core backtest loop ───────────────────────────────────────────────

def backtest_ticker(ticker: str, opex_dates: list[date]) -> list[dict]:
    """Run both CC and bull_put baseline on a ticker. One row per cycle per
    structure per slip."""
    print(f"  Loading {ticker}...")
    chain = load_chain(ticker)
    closes = daily_close_series(chain)
    divs = load_dividends(ticker)

    rows = []
    for i in range(len(opex_dates) - 1):
        prior_opex = opex_dates[i]
        next_opex = opex_dates[i + 1]

        # Entry day = first trading day strictly after prior_opex
        entry_candidates = closes.index[closes.index > pd.Timestamp(prior_opex)]
        entry_candidates = entry_candidates[entry_candidates < pd.Timestamp(next_opex)]
        if len(entry_candidates) == 0:
            continue
        entry_date = entry_candidates[0]

        if entry_date not in closes.index or pd.Timestamp(next_opex) not in closes.index:
            # Fall back: use closest-to-OpEx exit close
            try:
                exit_idx = closes.index.searchsorted(pd.Timestamp(next_opex))
                if exit_idx >= len(closes):
                    continue
                exit_date = closes.index[exit_idx]
                if exit_date > pd.Timestamp(next_opex) + pd.Timedelta(days=2):
                    continue
            except Exception:
                continue
        else:
            exit_date = pd.Timestamp(next_opex)

        S0 = float(closes.loc[entry_date])
        S1 = float(closes.loc[exit_date])

        # Dividends in [entry_date, exit_date)
        div_in_window = 0.0
        if not divs.empty:
            mask = (divs["ex_date"] >= entry_date) & (divs["ex_date"] < exit_date)
            div_in_window = float(divs.loc[mask, "amount"].sum())

        # ── COVERED CALL ───────────────────────────────────────────────
        call = find_30delta_call(chain, entry_date, pd.Timestamp(next_opex))
        if call is not None:
            K_call = call["strike"]
            premium_mid = call["mid"]
            assigned = S1 >= K_call
            stock_pnl = (min(S1, K_call) - S0)
            for slip in SLIPS_CC:
                premium_net = max(0.0, premium_mid - slip)
                pnl = stock_pnl + premium_net + div_in_window
                rows.append({
                    "ticker": ticker,
                    "structure": "covered_call",
                    "cycle_open": entry_date.date(),
                    "cycle_expiry": exit_date.date(),
                    "S0": S0, "S1": S1,
                    "K": K_call,
                    "premium_mid": premium_mid,
                    "premium_collected": premium_net,
                    "dividends": div_in_window,
                    "assigned": assigned,
                    "slip": slip,
                    "pnl_per_share": pnl,
                    "delta_target": TARGET_DELTA_CALL,
                    "wing": np.nan,
                    "max_loss_per_share": S0,  # capital outlay
                })

        # ── BULL_PUT BASELINE (slip=0.10) ─────────────────────────────
        short_put = find_30delta_put(chain, entry_date, pd.Timestamp(next_opex))
        if short_put is not None:
            K_short = short_put["strike"]
            wing = max(WING_FLOOR, S0 * WING_PCT_OF_SPOT)
            long_put = find_long_put(chain, entry_date, pd.Timestamp(next_opex),
                                      K_short, wing)
            if long_put is not None:
                actual_wing = K_short - long_put["strike"]
                if actual_wing > 0:
                    credit_mid = short_put["mid"] - long_put["mid"]
                    credit_net = credit_mid - SLIP_BP_BASELINE
                    # P&L at expiry
                    if S1 >= K_short:
                        pnl = credit_net   # full credit, both expire OTM
                    elif S1 <= long_put["strike"]:
                        pnl = credit_net - actual_wing  # max loss
                    else:
                        pnl = credit_net - (K_short - S1)
                    rows.append({
                        "ticker": ticker,
                        "structure": "bull_put",
                        "cycle_open": entry_date.date(),
                        "cycle_expiry": exit_date.date(),
                        "S0": S0, "S1": S1,
                        "K": K_short,
                        "premium_mid": credit_mid,
                        "premium_collected": credit_net,
                        "dividends": 0.0,  # spread doesn't hold stock
                        "assigned": False,
                        "slip": SLIP_BP_BASELINE,
                        "pnl_per_share": pnl,
                        "delta_target": TARGET_DELTA_PUT,
                        "wing": actual_wing,
                        "max_loss_per_share": actual_wing - credit_net,
                    })

    return rows


# ─── Aggregation ──────────────────────────────────────────────────────

def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Per-ticker per-structure per-slip aggregate scorecard."""
    rows = []
    for (ticker, structure, slip), grp in df.groupby(["ticker", "structure", "slip"]):
        pnls = grp["pnl_per_share"].dropna()
        if len(pnls) == 0:
            continue
        cap = grp["max_loss_per_share"].mean()  # average max loss per share
        mean_pnl = float(pnls.mean())
        ann_return = mean_pnl * 12 / cap if cap > 0 else np.nan
        rows.append({
            "ticker": ticker,
            "structure": structure,
            "slip": slip,
            "n_cycles": int(len(pnls)),
            "mean_pnl_per_share": mean_pnl,
            "median_pnl_per_share": float(pnls.median()),
            "win_rate": float((pnls > 0).mean()),
            "worst": float(pnls.min()),
            "best": float(pnls.max()),
            "total_pnl_per_share": float(pnls.sum()),
            "avg_capital_per_share": float(cap),
            "annualized_return_on_capital": float(ann_return),
            "n_assigned": int(grp["assigned"].sum()),
            "avg_dividends_per_cycle": float(grp["dividends"].mean()),
        })
    return pd.DataFrame(rows).sort_values(["ticker", "structure", "slip"])


def walk_forward(df: pd.DataFrame) -> pd.DataFrame:
    """4-year sub-window stability check."""
    df = df.copy()
    df["year"] = pd.to_datetime(df["cycle_open"]).dt.year
    windows = [(2013, 2016), (2017, 2020), (2021, 2024), (2025, 2026)]
    rows = []
    for lo, hi in windows:
        sub = df[(df["year"] >= lo) & (df["year"] <= hi)]
        for (ticker, structure, slip), g in sub.groupby(["ticker", "structure", "slip"]):
            pnls = g["pnl_per_share"].dropna()
            if len(pnls) < 5:
                continue
            cap = g["max_loss_per_share"].mean()
            rows.append({
                "window": f"{lo}-{hi}",
                "ticker": ticker,
                "structure": structure,
                "slip": slip,
                "n": int(len(pnls)),
                "mean_pnl": float(pnls.mean()),
                "win_rate": float((pnls > 0).mean()),
                "ann_return": float(pnls.mean() * 12 / cap) if cap > 0 else np.nan,
            })
    return pd.DataFrame(rows)


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    print(f"Universe: {UNIVERSE}")
    print(f"Period: {START_YEAR}-{END_YEAR}")
    opex_dates = monthly_opex_dates(START_YEAR, END_YEAR)
    print(f"Monthly OpEx dates: {len(opex_dates)}")

    all_rows = []
    for ticker in UNIVERSE:
        try:
            rows = backtest_ticker(ticker, opex_dates)
            print(f"    {ticker}: {len(rows)} rows")
            all_rows.extend(rows)
        except Exception as e:
            print(f"    {ticker}: ERR {e}")

    df = pd.DataFrame(all_rows)
    df.to_parquet(OUT_CYCLES, index=False)
    print(f"\n  ✓ {len(df)} cycle rows → {OUT_CYCLES}")

    score = aggregate(df)
    score.to_parquet(OUT_SCORE, index=False)

    print("\n" + "=" * 100)
    print("AGGREGATE SCORECARD (per-ticker per-structure per-slip)")
    print("=" * 100)
    print(score.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    print("\n" + "=" * 100)
    print("WALK-FORWARD by 4-year windows")
    print("=" * 100)
    wf = walk_forward(df)
    print(wf.to_string(index=False, float_format=lambda x: f"{x:.4f}"))


if __name__ == "__main__":
    main()
