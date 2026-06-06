"""Study B (EQUITY-volume arm) — Burry's literal tell: does declining SHARE
volume on extended names predict a larger forward move / fatter downside tail?

Per LONGDATED_IF_VOLUME_SIGNAL_PREREG.md. Identical design to the option-volume
arm (volume_signal_study.py) — same extension defs, windows, cohorts, gates —
but the volume ratio uses EQUITY share volume (data/profile/equity_volume.parquet,
from ingest_equity_volume.py) instead of ORATS option volume. Close / 200-DMA /
52wk-high / forward returns still come from ORATS so the two arms are
apples-to-apples. Scored INDEPENDENTLY; neither promotes on the other.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
BY_TICKER = ROOT / "data" / "orats" / "by_ticker"
EQVOL = ROOT / "data" / "profile" / "equity_volume.parquet"
OUT = ROOT / "data" / "profile" / "volume_signal_equity_results.parquet"

DECLINE_BAR = 0.85
STABLE_BAR = 1.00
EXT_MA = 0.08
EXT_HIGH = -0.05
WINDOWS = [25, 60]
MOVE_THRESH = 0.05
DOWN_THRESH = -0.10
MAG_LIFT_PP = 0.05
DOWN_LIFT_PP = 0.03
MIN_N_SPLIT = 100

SPLITS = [("2021-01-01", "2023-12-31"), ("2022-01-01", "2024-12-31"),
          ("2023-01-01", "2025-12-31"), ("2024-01-01", "2026-04-30")]


def _build_daily(sym, eqvol):
    df = pd.read_parquet(BY_TICKER / f"{sym}.parquet", columns=["trade_date", "stkPx"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    daily = df.groupby("trade_date")["stkPx"].first().rename("close").to_frame().sort_index()
    c = daily["close"]
    daily["ma200"] = c.rolling(200, min_periods=100).mean()
    daily["hi52"] = c.rolling(252, min_periods=120).max()
    daily["pct_ma"] = c / daily["ma200"] - 1
    daily["pct_hi"] = c / daily["hi52"] - 1
    for w in WINDOWS:
        daily[f"fwd{w}"] = c.shift(-w) / c - 1
        daily[f"fdraw{w}"] = c.rolling(w).min().shift(-w) / c - 1
    daily = daily.reset_index().rename(columns={"index": "trade_date"})
    # merge EQUITY volume by date, compute ratio from share volume
    ev = eqvol[eqvol["ticker"] == sym][["date", "eq_volume"]].rename(columns={"date": "trade_date"})
    daily = daily.merge(ev, on="trade_date", how="left").sort_values("trade_date")
    v = daily["eq_volume"]
    daily["volratio"] = v.rolling(20).mean() / v.rolling(60).mean()
    daily["ticker"] = sym
    return daily


def _stats(d, w):
    ret, draw = d[f"fwd{w}"], d[f"fdraw{w}"]
    return {"n": len(d), "p_move": (ret.abs() > MOVE_THRESH).mean(),
            "mean_absret": ret.abs().mean(), "p_down": (ret < DOWN_THRESH).mean(),
            "mean_draw": draw.mean()}


def run():
    eqvol = pd.read_parquet(EQVOL)
    eqvol["date"] = pd.to_datetime(eqvol["date"])
    universe = sorted(eqvol["ticker"].unique())
    universe = [s for s in universe if (BY_TICKER / f"{s}.parquet").exists()]
    print(f"  EQUITY-volume arm; universe {len(universe)} (SPX excluded: no share vol)\n", flush=True)
    frames = []
    for i, sym in enumerate(universe, 1):
        frames.append(_build_daily(sym, eqvol))
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
    print("  STUDY B — EQUITY (SHARE) VOLUME DECLINE on EXTENDED names (Burry's literal tell)", flush=True)
    print("  declining = 20d/60d share-vol <= 0.85 | control >= 1.00 | baseline = all extended", flush=True)
    print("=" * 96, flush=True)
    for ext_name, ext_mask in ext_defs:
        base = alld[ext_mask].dropna(subset=["volratio"])
        decl = base[base["volratio"] <= DECLINE_BAR]
        ctrl = base[base["volratio"] >= STABLE_BAR]
        print(f"\n  ── {ext_name} ──  (extended: {len(base):,}; decl {len(decl):,} / ctrl {len(ctrl):,})", flush=True)
        for w in WINDOWS:
            dd = decl.dropna(subset=[f"fwd{w}", f"fdraw{w}"])
            cc = ctrl.dropna(subset=[f"fwd{w}", f"fdraw{w}"])
            bb = base.dropna(subset=[f"fwd{w}", f"fdraw{w}"])
            ds, cs, bs = _stats(dd, w), _stats(cc, w), _stats(bb, w)
            print(f"\n   [{w}d fwd]            N      P(|ret|>5%)  mean|ret|   P(ret<-10%)  mean draw", flush=True)
            for lbl, st in [("declining-vol", ds), ("control(stable)", cs), ("baseline(all ext)", bs)]:
                print(f"     {lbl:18s} {st['n']:>7} {st['p_move']*100:>9.1f}% "
                      f"{st['mean_absret']*100:>9.1f}% {st['p_down']*100:>10.1f}% "
                      f"{st['mean_draw']*100:>9.1f}%", flush=True)
            mag_lift = ds["p_move"] - cs["p_move"]
            down_lift = ds["p_down"] - cs["p_down"]
            g1 = (mag_lift >= MAG_LIFT_PP) and (ds["mean_absret"] > cs["mean_absret"])
            g2 = (down_lift >= DOWN_LIFT_PP) and (ds["mean_draw"] < cs["mean_draw"])

            def _wf(col, op):
                hits = ok = 0
                for a, b in SPLITS:
                    dsub = dd[(dd.trade_date >= a) & (dd.trade_date <= b)]
                    csub = cc[(cc.trade_date >= a) & (cc.trade_date <= b)]
                    if len(dsub) < MIN_N_SPLIT or len(csub) < MIN_N_SPLIT:
                        continue
                    ok += 1
                    dm = op(dsub); cm = op(csub)
                    if (dm - cm) >= (MAG_LIFT_PP if col == "mag" else DOWN_LIFT_PP):
                        hits += 1
                return hits, ok
            wf_mag = _wf("mag", lambda x: (x[f"fwd{w}"].abs() > MOVE_THRESH).mean())
            wf_down = _wf("down", lambda x: (x[f"fwd{w}"] < DOWN_THRESH).mean())
            print(f"     -> mag lift {mag_lift*100:+.1f}pp (gate1 {'PASS' if g1 else 'fail'}, "
                  f"WF {wf_mag[0]}/{wf_mag[1]}); down lift {down_lift*100:+.1f}pp "
                  f"(gate2 {'PASS' if g2 else 'fail'}, WF {wf_down[0]}/{wf_down[1]})", flush=True)
            print(f"        vs baseline (gate5): mag {(ds['p_move']-bs['p_move'])*100:+.1f}pp, "
                  f"down {(ds['p_down']-bs['p_down'])*100:+.1f}pp", flush=True)
    print("\n" + "=" * 96, flush=True)
    print("  Verdict: gate1 only=magnitude-only; gate1+gate2=downside-tilt; neither=REJECTED.", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    run()
