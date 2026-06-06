"""Study B (option-volume arm) — does declining volume on EXTENDED names predict
a larger forward move / fatter downside tail than the stable-volume control?

Per LONGDATED_IF_VOLUME_SIGNAL_PREREG.md (sealed 2026-06-03). Burry's tell:
volume fades as a name gets extended → precondition for a top/drop. This arm
uses OPTION volume (ORATS cVolu+pVolu, available now); the equity-volume arm
needs a yfinance ingest (separate). Both scored independently — neither
promotes on the other's strength.

Now the LINCHPIN of the thread: Study A showed long-dated IF is not capital-
efficient vs 45-DTE, so the prize is a SIGNAL-GATED 45-DTE IF — iff a timing
signal like this one has real, incremental predictive power.

Design (sealed):
  - Extension (two defs, separate): E1 = close >= 8% above 200-DMA;
    E2 = within 5% of 52-week high.
  - Volume-decline: 20d avg opt-vol / 60d avg <= 0.85. Control = ratio >= 1.00.
  - Targets, compared: (mag) P(|fwd ret| > 5%) + mean|ret|;
                       (down) P(fwd ret < -10%) + mean fwd drawdown.
  - Windows: 25 and 60 trading days. 4-split walk-forward. Universe = IF set.
  - Gate 5: declining cohort must beat the PLAIN-extension baseline (incremental).

Reads ORATS by_ticker; writes data/profile/volume_signal_results.parquet.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data" / "orats" / "by_ticker"
OUT = ROOT / "data" / "profile" / "volume_signal_results.parquet"

IF_UNIVERSE = sorted(set([
    "SPX", "SPY", "QQQ", "GLD", "EFA", "WMT", "NEM", "XOM", "PG", "WFC", "GE",
    "INTC", "BABA", "TSLA", "AMD", "NVDA", "CAR", "AMZN", "GOOGL", "SCCO",
    "GOLD", "CLF", "ISRG", "XLK", "PEP", "STX", "LRCX", "MCD", "JNJ", "PDD",
    "AG", "DELL", "AFRM", "PLTR", "AVGO",
]))

DECLINE_BAR = 0.85
STABLE_BAR = 1.00
EXT_MA = 0.08          # E1: >=8% above 200-DMA
EXT_HIGH = -0.05       # E2: within 5% of 52wk high (close/high - 1 >= -0.05)
WINDOWS = [25, 60]
MOVE_THRESH = 0.05     # |ret| > 5%
DOWN_THRESH = -0.10    # ret < -10%
MAG_LIFT_PP = 0.05     # Gate 1: +5pp
DOWN_LIFT_PP = 0.03    # Gate 2: +3pp
MIN_N_SPLIT = 100      # adequacy

SPLITS = [("2021-01-01", "2023-12-31"), ("2022-01-01", "2024-12-31"),
          ("2023-01-01", "2025-12-31"), ("2024-01-01", "2026-04-30")]

COLS = ["trade_date", "stkPx", "cVolu", "pVolu"]


def _build_daily(sym):
    df = pd.read_parquet(BY_TICKER / f"{sym}.parquet", columns=COLS)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    g = df.groupby("trade_date")
    daily = pd.DataFrame({
        "close": g["stkPx"].first(),
        "optvol": (g["cVolu"].sum() + g["pVolu"].sum()),
    }).sort_index()
    c = daily["close"]
    daily["ma200"] = c.rolling(200, min_periods=100).mean()
    daily["hi52"] = c.rolling(252, min_periods=120).max()
    daily["pct_ma"] = c / daily["ma200"] - 1
    daily["pct_hi"] = c / daily["hi52"] - 1
    v = daily["optvol"]
    daily["volratio"] = v.rolling(20).mean() / v.rolling(60).mean()
    for w in WINDOWS:
        daily[f"fwd{w}"] = c.shift(-w) / c - 1
        # forward min over (t+1 .. t+w): rolling-min then shift back
        daily[f"fdraw{w}"] = c.rolling(w).min().shift(-w) / c - 1
    daily["ticker"] = sym
    return daily.reset_index().rename(columns={"index": "trade_date"})


def _cohort_stats(d, w):
    ret = d[f"fwd{w}"]
    draw = d[f"fdraw{w}"]
    return {
        "n": len(d),
        "p_move": (ret.abs() > MOVE_THRESH).mean(),
        "mean_absret": ret.abs().mean(),
        "p_down": (ret < DOWN_THRESH).mean(),
        "mean_draw": draw.mean(),
    }


def run():
    avail = {p.stem for p in BY_TICKER.glob("*.parquet")}
    universe = [s for s in IF_UNIVERSE if s in avail]
    print(f"  universe {len(universe)}; OPTION-volume arm; decline<= {DECLINE_BAR}, "
          f"control>= {STABLE_BAR}\n", flush=True)
    frames = []
    for i, sym in enumerate(universe, 1):
        frames.append(_build_daily(sym))
        if i % 10 == 0 or i == len(universe):
            print(f"  built {i}/{len(universe)}", flush=True)
    alld = pd.concat(frames, ignore_index=True)
    alld.to_parquet(OUT, index=False)
    print(f"\n  wrote {len(alld):,} name-days -> {OUT}\n", flush=True)
    _report(alld)


def _report(alld):
    ext_defs = [("E1 >=8% over 200DMA", alld["pct_ma"] >= EXT_MA),
                ("E2 within 5% of 52wk high", alld["pct_hi"] >= EXT_HIGH)]
    print("=" * 96, flush=True)
    print("  STUDY B — OPTION-VOLUME DECLINE on EXTENDED names predicting forward move", flush=True)
    print("  declining = 20d/60d opt-vol <= 0.85 | control = >= 1.00 | baseline = all extended", flush=True)
    print("=" * 96, flush=True)
    for ext_name, ext_mask in ext_defs:
        base = alld[ext_mask].dropna(subset=["volratio"])
        decl = base[base["volratio"] <= DECLINE_BAR]
        ctrl = base[base["volratio"] >= STABLE_BAR]
        print(f"\n  ── {ext_name} ──  (extended name-days: {len(base):,}; "
              f"declining {len(decl):,} / control {len(ctrl):,})", flush=True)
        for w in WINDOWS:
            dd = decl.dropna(subset=[f"fwd{w}", f"fdraw{w}"])
            cc = ctrl.dropna(subset=[f"fwd{w}", f"fdraw{w}"])
            bb = base.dropna(subset=[f"fwd{w}", f"fdraw{w}"])
            ds, cs, bs = _cohort_stats(dd, w), _cohort_stats(cc, w), _cohort_stats(bb, w)
            print(f"\n   [{w}d fwd]            N      P(|ret|>5%)  mean|ret|   P(ret<-10%)  mean draw", flush=True)
            for lbl, st in [("declining-vol", ds), ("control(stable)", cs), ("baseline(all ext)", bs)]:
                print(f"     {lbl:18s} {st['n']:>7} {st['p_move']*100:>9.1f}% "
                      f"{st['mean_absret']*100:>9.1f}% {st['p_down']*100:>10.1f}% "
                      f"{st['mean_draw']*100:>9.1f}%", flush=True)
            # Gates
            mag_lift = ds["p_move"] - cs["p_move"]
            down_lift = ds["p_down"] - cs["p_down"]
            g1 = (mag_lift >= MAG_LIFT_PP) and (ds["mean_absret"] > cs["mean_absret"])
            g2 = (down_lift >= DOWN_LIFT_PP) and (ds["mean_draw"] < cs["mean_draw"])
            # Gate 5: beat plain-extension baseline (incremental)
            g5_mag = ds["p_move"] - bs["p_move"]
            g5_down = ds["p_down"] - bs["p_down"]
            # Walk-forward: lift direction holds with adequacy
            def _wf(metric, control_metric, thresh):
                hits = ok = 0
                for a, b in SPLITS:
                    dsub = dd[(dd.trade_date >= a) & (dd.trade_date <= b)]
                    csub = cc[(cc.trade_date >= a) & (cc.trade_date <= b)]
                    if len(dsub) < MIN_N_SPLIT or len(csub) < MIN_N_SPLIT:
                        continue
                    ok += 1
                    if metric(dsub) - control_metric(csub) >= thresh:
                        hits += 1
                return hits, ok
            wf_mag = _wf(lambda x: (x[f"fwd{w}"].abs() > MOVE_THRESH).mean(),
                         lambda x: (x[f"fwd{w}"].abs() > MOVE_THRESH).mean(), MAG_LIFT_PP)
            wf_down = _wf(lambda x: (x[f"fwd{w}"] < DOWN_THRESH).mean(),
                          lambda x: (x[f"fwd{w}"] < DOWN_THRESH).mean(), DOWN_LIFT_PP)
            print(f"     -> mag lift {mag_lift*100:+.1f}pp (gate1 {'PASS' if g1 else 'fail'}, "
                  f"WF {wf_mag[0]}/{wf_mag[1]}); "
                  f"down lift {down_lift*100:+.1f}pp (gate2 {'PASS' if g2 else 'fail'}, "
                  f"WF {wf_down[0]}/{wf_down[1]})", flush=True)
            print(f"        vs baseline (gate5): mag {g5_mag*100:+.1f}pp, "
                  f"down {g5_down*100:+.1f}pp  ({'incremental' if (g5_mag>0 or g5_down>0) else 'NO incremental lift'})", flush=True)
    print("\n" + "=" * 96, flush=True)
    print("  Directionality verdict per (ext,window): gate1 only=magnitude-only; "
          "gate1+gate2=downside-tilt; neither=REJECTED.", flush=True)
    print("  NOTE: overlapping forward windows autocorrelate absolute levels; the LIFT "
          "(decl-control) is\n  the gated quantity and both cohorts share the overlap. "
          "Equity-volume arm pending ingest.", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    run()
