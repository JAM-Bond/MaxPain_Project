#!/usr/bin/env python3.11
"""Where does the SPY 200-DMA + IV gate (H1) fail — and would Chaikin Money Flow
have helped? (DESCRIPTIVE / exploratory — NOT a gate, NOT an edge claim.)

H1 = SPY close < 200-DMA  AND  SPY IV-rank > 0.5. It is the bear_call entry gate
(regime Stage 3). "H1 fails" here means: a bear_call we were *allowed* to open under
H1 still LOST — almost always because the market rallied (the Stage-5 recovery /
false-positive zone). Bear_call is bearish: it loses when the underlying rises.

Two questions the user asked:
  (1) PREVENTION — at ENTRY, does a Chaikin Money Flow reading separate the H1
      bear_call winners from the losers well enough to have vetoed losers without
      throwing away winners? (CMF on the name, and on SPY.) A still-positive money
      flow = "buyers in control, don't sell calls into this."
  (2) EARLY WARNING — for trades that were opened and went on to lose, did the
      underlying's CMF turn bullish (cross up) BEFORE the adverse price move
      (the underlying breaching the short call strike), giving usable lead time —
      and crucially, did it do so RELIABLY (not also firing in the winners)?

Data:
  • Trades: data/profile/price_breach_stop_results.parquet (bear_call leg),
    the universe_v1 150-name backtest, 2013–2026. Outcome = managed_pnl (our
    realized exit policy) with held_pnl reported alongside.
  • H1: SPY close + 200-DMA (yfinance) AND SPY IV-rank (atm_iv_series.parquet).
  • CMF-12 (user's working default) per name + on SPY, from yfinance OHLCV.

Discipline: walk-forward sign check (train ≤2019 / val ≥2020); precision framed
explicitly (does a filter/warning hit losers more than winners?); descriptive only.

Usage: python3.11 scripts/research/h1_chaikin_study.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

TRADES = ROOT / "data/profile/price_breach_stop_results.parquet"
ATM_IV = ROOT / "data/profile/atm_iv_series.parquet"
OUT = ROOT / "data/profile/h1_chaikin_study.parquet"

CMF_WIN = 12                     # user's working default
CMF_THR = 0.0                    # bullish-flow trigger threshold (sign); robustness at +0.05 too
TRAIN_END = pd.Timestamp("2019-12-31")


def cmf_frame(df: pd.DataFrame, win: int = CMF_WIN) -> pd.Series:
    hi, lo, cl, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (hi - lo).replace(0, np.nan)
    mfm = ((cl - lo) - (hi - cl)) / rng
    mfv = mfm.fillna(0) * vol
    return mfv.rolling(win).sum() / vol.rolling(win).sum()


def build_h1() -> pd.DataFrame:
    """Daily H1 series: SPY<200dma AND SPY iv_rank>0.5. Also SPY CMF-12."""
    import yfinance as yf
    spy = yf.download("SPY", period="max", interval="1d", auto_adjust=False, progress=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    spy = spy.rename(columns={"Adj Close": "AdjClose"}).dropna()
    spy["ma200"] = spy["Close"].rolling(200).mean()
    spy["spy_cmf"] = cmf_frame(spy)
    iv = pd.read_parquet(ATM_IV)
    iv = iv[iv["ticker"] == "SPY"][["trade_date", "iv_rank"]].copy()
    iv["trade_date"] = pd.to_datetime(iv["trade_date"])
    iv = iv.set_index("trade_date").sort_index()
    h1 = pd.DataFrame(index=spy.index)
    h1["spy_close"] = spy["Close"]
    h1["spy_ma200"] = spy["ma200"]
    h1["spy_cmf"] = spy["spy_cmf"]
    h1["spy_ivr"] = iv["iv_rank"].reindex(h1.index, method="ffill")
    h1["h1"] = (h1["spy_close"] < h1["spy_ma200"]) & (h1["spy_ivr"] > 0.5)
    return h1.dropna(subset=["spy_ma200", "spy_ivr"])


def fetch_ohlcv(tickers: list) -> dict:
    """Per-name OHLCV frames (raw) from one batch yfinance download."""
    import yfinance as yf
    raw = yf.download(tickers, period="max", interval="1d", auto_adjust=False,
                      group_by="ticker", progress=False, threads=True)
    out = {}
    for t in tickers:
        try:
            sub = raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(sub) > CMF_WIN + 5:
                sub = sub.copy()
                sub["cmf"] = cmf_frame(sub)
                out[t] = sub
        except Exception:
            continue
    return out


def asof(series: pd.Series, ts) -> float:
    """Value of a date-indexed series as of ts (ffill); NaN if before start."""
    try:
        idx = series.index.searchsorted(pd.Timestamp(ts), side="right") - 1
        return float(series.iloc[idx]) if idx >= 0 else np.nan
    except Exception:
        return np.nan


def main() -> int:
    print("H1 (SPY<200dma & IVR>0.5) failure × Chaikin Money Flow  [DESCRIPTIVE]")
    print(f"CMF window={CMF_WIN}  bullish-trigger>{CMF_THR}  split={TRAIN_END.date()}")

    tr = pd.read_parquet(TRADES)
    tr = tr[tr["structure"] == "bear_call"].copy()
    tr["entry_date"] = pd.to_datetime(tr["entry_date"])
    tr["expiration"] = pd.to_datetime(tr["expiration"])

    h1 = build_h1()
    ohlcv = fetch_ohlcv(sorted(tr["ticker"].unique().tolist()))

    # mark H1 at entry + CMF readings at entry
    tr["h1_at_entry"] = tr["entry_date"].map(lambda d: bool(asof(h1["h1"].astype(float), d) >= 0.5))
    tr["spy_cmf_entry"] = tr["entry_date"].map(lambda d: asof(h1["spy_cmf"], d))
    tr["name_cmf_entry"] = [asof(ohlcv[t]["cmf"], d) if t in ohlcv else np.nan
                            for t, d in zip(tr["ticker"], tr["entry_date"])]

    pop = tr[tr["h1_at_entry"]].dropna(subset=["managed_pnl", "name_cmf_entry"]).copy()
    pop["win"] = pop["managed_pnl"] > 0
    pop["window"] = np.where(pop["entry_date"] <= TRAIN_END, "train", "val")
    n, nl = len(pop), int((~pop["win"]).sum())
    print(f"\nH1-active bear_call cycles: {len(tr[tr['h1_at_entry']])} "
          f"(with CMF: {n}) | winners={n-nl} losers={nl} "
          f"win-rate={100*(n-nl)/n:.1f}%  mean managed_pnl={pop['managed_pnl'].mean():+.3f}")
    print(f"  (baseline ALL bear_call cycles: {len(tr)}, "
          f"win-rate={100*(tr['managed_pnl']>0).mean():.1f}% — H1 is the gate that selects the subset above)")

    # ── (1) PREVENTION: CMF-at-entry separation winners vs losers ──
    print(f"\n{'='*78}\n(1) PREVENTION — CMF at entry: winners vs losers\n{'='*78}")
    for lbl, col in [("name CMF-12", "name_cmf_entry"), ("SPY CMF-12", "spy_cmf_entry")]:
        w = pop.loc[pop["win"], col].dropna()
        l = pop.loc[~pop["win"], col].dropna()
        print(f"  {lbl}:  winners mean={w.mean():+.3f} median={w.median():+.3f} | "
              f"losers mean={l.mean():+.3f} median={l.median():+.3f}  "
              f"(Δ={w.mean()-l.mean():+.3f})")
    # candidate veto filters: skip the trade if money still flowing in at entry
    print("\n  Candidate entry vetoes (skip bear_call if flow still bullish at entry):")
    for lbl, col in [("name CMF>0", "name_cmf_entry"), ("SPY CMF>0", "spy_cmf_entry")]:
        veto = pop[col] > CMF_THR
        kept = pop[~veto]
        vlos = int((~pop.loc[veto, "win"]).sum()); vwin = int(pop.loc[veto, "win"].sum())
        if len(kept):
            print(f"    veto {lbl:<11}: vetoes {int(veto.sum())} trades "
                  f"({vlos} losers / {vwin} winners) → kept win-rate "
                  f"{100*kept['win'].mean():.1f}% (from {100*pop['win'].mean():.1f}%), "
                  f"kept mean pnl {kept['managed_pnl'].mean():+.3f} (from {pop['managed_pnl'].mean():+.3f})")
            # walk-forward: does the veto improve mean pnl in BOTH windows?
            row = []
            for win in ("train", "val"):
                sub = pop[pop.window == win]
                base = sub["managed_pnl"].mean()
                kp = sub[sub[col] <= CMF_THR]["managed_pnl"].mean()
                row.append((win, kp - base))
            stable = (not any(np.isnan(d) for _, d in row)
                      and np.sign(row[0][1]) == np.sign(row[1][1]))
            print(f"        WF Δmean(kept−all): "
                  f"{'  '.join(f'{w}={d:+.3f}' for w,d in row)}  "
                  f"{'✓ same sign' if stable else '✗ flips'}")

    # ── (2) EARLY WARNING: did name-CMF turn bullish before the short-strike breach? ──
    print(f"\n{'='*78}\n(2) EARLY WARNING — name CMF crossing up vs the adverse price move\n{'='*78}")
    print("  For each H1 bear_call trade: breach = first day spot ≥ short call strike after")
    print("  entry; warn = first day name CMF crosses > threshold after entry. Lead = breach − warn.")

    def path_events(row):
        t = row["ticker"]
        if t not in ohlcv:
            return pd.Series({"warned": np.nan, "breached": np.nan, "lead": np.nan})
        df = ohlcv[t]
        mask = (df.index > row["entry_date"]) & (df.index <= row["expiration"])
        seg = df.loc[mask]
        if seg.empty:
            return pd.Series({"warned": np.nan, "breached": np.nan, "lead": np.nan})
        # adverse breach: spot rises to/through the short call strike
        br = seg.index[seg["Close"] >= row["short_strike"]]
        breach_date = br[0] if len(br) else pd.NaT
        # warning: CMF crosses from <=thr to >thr
        c = seg["cmf"]
        cross = seg.index[(c > CMF_THR) & (c.shift(1) <= CMF_THR)]
        warn_date = cross[0] if len(cross) else pd.NaT
        lead = np.nan
        if pd.notna(breach_date) and pd.notna(warn_date):
            lead = np.busday_count(warn_date.date(), breach_date.date())
        return pd.Series({"warned": pd.notna(warn_date),
                          "breached": pd.notna(breach_date), "lead": lead})

    ev = pop.apply(path_events, axis=1)
    pop = pd.concat([pop, ev], axis=1)
    losers = pop[~pop["win"]]
    winners = pop[pop["win"]]
    brl = losers[losers["breached"] == True]
    print(f"\n  Of {len(losers)} losers: {int((losers['breached']==True).sum())} actually breached the short strike.")
    warned_before = brl[(brl["warned"] == True) & (brl["lead"] > 0)]
    print(f"  Among breached losers, CMF crossed up BEFORE the breach in "
          f"{len(warned_before)}/{len(brl)} ({100*len(warned_before)/max(len(brl),1):.0f}%); "
          f"median lead = {warned_before['lead'].median():.0f} trading days.")
    # RELIABILITY / false alarms: how often does CMF also cross up in winners?
    wr = winners["warned"] == True
    lr = losers["warned"] == True
    print(f"\n  RELIABILITY (false-alarm check): name CMF crosses up at some point during the hold in")
    print(f"    {100*lr.mean():.0f}% of losers  vs  {100*wr.mean():.0f}% of winners "
          f"→ {'little separation — unreliable as a warning' if abs(lr.mean()-wr.mean())<0.15 else 'some separation'}.")
    print(f"  (Bear_call adverse move = underlying rising; CMF rises *with* price, so a 'warning' that")
    print(f"   fires about as often in winners is coincident, not predictive.)")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pop.to_parquet(OUT)
    print(f"\nsaved → {OUT.relative_to(ROOT)}")
    print("CAVEATS: backtest (universe_v1, no live fills); managed_pnl uses our exit policy; "
          "CMF is coincident with price by construction; descriptive only — pre-register before gating.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
