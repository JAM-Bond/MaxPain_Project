#!/usr/bin/env python3.11
"""TOS 'VelocityAndAcceleration' on SPY/RSP — does price velocity/acceleration
predict the forward trend or flag inflections early? (DESCRIPTIVE / exploratory.)

Faithful to the ThinkOrSwim study:
  velocity_raw(t) = (1/n) Σ_{i=1..n} (p_t − p_{t−i})  ==  p_t − SMA_n(prior n prices)
  velocity        = EMA_{a}(velocity_raw)                 (smoothed)
  accel_raw(t)    = (1/n) Σ_{i=1..n} (v_t − v_{t−i})  ==  v_t − SMA_n(prior n velocities)
  acceleration    = accel_raw  (histogram; 4 states by sign × direction)

KEY FRAMING: this is the velocity of PRICE, not money — it contains no volume /
positioning, so it is maximally price-coupled (the opposite of the OI-velocity
test). The 'velocity' leg ≈ distance-from-moving-average momentum, which our
RSP/200-DMA work already found mean-reverting at 1–3 months. The genuinely
untested piece is ACCELERATION (2nd derivative) + the 4-state inflection read
(pos&up / pos&down / neg&up / neg&down) as an EARLY-WARNING-of-turn signal.

Tests (a-priori; walk-forward train ≤2019 / val ≥2020):
  • IC of velocity and acceleration vs forward return at 5/10/21/42/63 td.
  • Does acceleration add over velocity (2nd derivative beyond 1st)?
  • 4-state histogram → forward returns: does 'positive-and-down' (uptrend
    decelerating) precede weakness, and 'negative-and-up' precede recoveries?

Params a-priori: length n=10, avg length a=3, EMA smoothing, close price.
Honest caveats printed with results.

Usage: python3.11 scripts/research/velocity_acceleration_study.py [n] [a]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data/profile/velocity_acceleration_study.parquet"

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10     # length
A = int(sys.argv[2]) if len(sys.argv) > 2 else 3      # average length (smoothing)
HORIZONS = (5, 10, 21, 42, 63)
TRAIN_END = pd.Timestamp("2019-12-31")
SYMBOLS = ("SPY", "RSP")


def vel_accel(px: pd.Series) -> pd.DataFrame:
    """Faithful TOS velocity & acceleration on a price series."""
    sma_n = px.rolling(N).mean()
    vel_raw = px - sma_n.shift(1)                 # p_t − SMA_n(prior n)
    vel = vel_raw.ewm(span=A, adjust=False).mean()
    accel_raw = vel - vel.rolling(N).mean().shift(1)
    accel = accel_raw                              # the histogram
    out = pd.DataFrame({"vel": vel, "accel": accel}, index=px.index)
    # normalize by price so SPY/RSP are comparable and stationary-ish
    out["vel"] = out["vel"] / px
    out["accel"] = out["accel"] / px
    return out


def _ic(d, a, b):
    dd = d[[a, b]].dropna()
    return dd.corr(method="spearman").iloc[0, 1] if len(dd) > 30 else np.nan


def study_symbol(sym: str) -> pd.DataFrame:
    import yfinance as yf
    spy = yf.download(sym, period="max", interval="1d", auto_adjust=True, progress=False)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = spy.columns.get_level_values(0)
    px = spy["Close"].dropna()
    va = vel_accel(px)
    panel = va.copy()
    panel["ret_tw"] = px / px.shift(N) - 1.0          # trailing N-day return (for coupling check)
    for h in HORIZONS:
        panel[f"fwd_{h}"] = px.shift(-h) / px - 1.0
    panel["window"] = np.where(panel.index <= TRAIN_END, "train", "val")
    # 4-state acceleration histogram
    panel["accel_pos"] = panel["accel"] > 0
    panel["accel_up"] = panel["accel"] > panel["accel"].shift(1)
    panel["state"] = np.select(
        [panel.accel_pos & panel.accel_up, panel.accel_pos & ~panel.accel_up,
         ~panel.accel_pos & panel.accel_up, ~panel.accel_pos & ~panel.accel_up],
        ["pos&up", "pos&down", "neg&up", "neg&down"], default="na")
    return panel.dropna(subset=["vel", "accel"])


def report(sym: str, panel: pd.DataFrame):
    print(f"\n{'='*82}\n{sym}: velocity/acceleration vs forward return  "
          f"(n={len(panel)}, {panel.index.min().date()}→{panel.index.max().date()})\n{'='*82}")
    # price-coupling note (velocity is momentum → expect it to track the trailing return)
    print(f"  price-coupling: corr(vel, trailing-{N}d return)="
          f"{_ic(panel, 'vel', 'ret_tw'):+.3f} (vel is momentum, expect coupling)")
    print(f"  {'horizon':>7} | {'IC(velocity)':>13} {'IC(accel)':>11}")
    for h in HORIZONS:
        print(f"  {h:>7} | {_ic(panel,'vel',f'fwd_{h}'):>+13.3f} {_ic(panel,'accel',f'fwd_{h}'):>+11.3f}")

    print(f"\n  4-STATE acceleration histogram → forward returns:")
    for h in (10, 21):
        print(f"   horizon {h}d:")
        for st in ("pos&up", "pos&down", "neg&up", "neg&down"):
            sub = panel[panel.state == st]
            m = sub[f"fwd_{h}"].mean(); md = sub[f"fwd_{h}"].median()
            # walk-forward mean by window
            tr = sub[sub.window == "train"][f"fwd_{h}"].mean()
            vl = sub[sub.window == "val"][f"fwd_{h}"].mean()
            wf = "✓" if (not np.isnan(tr) and not np.isnan(vl) and np.sign(tr) == np.sign(vl)) else "✗"
            print(f"     {st:<9} n={len(sub):>4}  mean={m*100:+5.2f}%  median={md*100:+5.2f}%  "
                  f"[train {tr*100:+.2f}% / val {vl*100:+.2f}% {wf}]")


def main() -> int:
    print(f"TOS VelocityAndAcceleration study  [DESCRIPTIVE / exploratory]")
    print(f"length n={N}  avg length a={A} (EMA)  horizons={HORIZONS}  split={TRAIN_END.date()}")
    print("NOTE: velocity = price − trailing SMA (pure price momentum); acceleration = its 2nd derivative.")
    frames = {}
    for sym in SYMBOLS:
        p = study_symbol(sym)
        frames[sym] = p
        report(sym, p)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.concat({k: v for k, v in frames.items()}, names=["symbol"]).to_parquet(OUT)
    print(f"\nsaved → {OUT.relative_to(ROOT)}")
    print("CAVEATS: pure price-derived (not money velocity); overlapping fwd windows; "
          "params a-priori (n=10,a=3) not optimized; descriptive only — pre-register before gating.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
