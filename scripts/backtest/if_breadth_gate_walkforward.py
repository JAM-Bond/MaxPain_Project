"""
IF gate sensitivity research — walk-forward across breadth-divergence gates.

Tests 6 candidate entry gates against the validated term-inversion baseline (G0):
  G0  term_spread > 0                          (baseline, Phase C validated)
  G1  spx_pct_to_50dma > 7%                    (extension only — control)
  G2  pct_above_50dma < 55% AND spx_pct > 7%   (30-yr rule replication)
  G3  G0 OR G2                                 (sensitive)
  G4  G0 AND G2                                (strict)
  G5  nhnl_diff < 0 AND spx_pct > 7%           (new-low > new-high on extended-index day)

Walk-forward: 4 splits, 10yr train / 3yr validate, matching Phase C.
Decision rule: promote a gate if
  (a) val expectancy >= 80% of baseline G0
  (b) val frequency materially higher than G0
  (c) walk-forward stable in 3/4 or 4/4 splits

Outputs:
  data/profile/if_breadth_gate_walkforward.parquet
  data/profile/breadth_spx500_v2.parquet  (enriched with new-high/low counts)
"""

import json, time, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')

ROOT = Path("/Users/josephmorris/MaxPain_Project")
IF_DATA = ROOT / "data/backtest/results_wide_wings_universe_slip025.parquet"
SIGNAL = ROOT / "data/profile/signal_vrp_termstruct_spy.parquet"
BREADTH_OUT = ROOT / "data/profile/breadth_spx500_v2.parquet"
WALKFWD_OUT = ROOT / "data/profile/if_breadth_gate_walkforward.parquet"
SPX_LIST = ROOT / "data/spx_constituents.json"


def build_enriched_breadth() -> pd.DataFrame:
    """Pull SPX-500 closes, compute breadth + new-high/new-low counts."""
    syms = json.loads(SPX_LIST.read_text())['symbols']
    print(f"  Pulling {len(syms)} SPX names from yfinance (2013-01-01 → 2026-05-12)...")
    t0 = time.time()
    closes = {}
    for i in range(0, len(syms), 50):
        chunk = syms[i:i+50]
        try:
            df = yf.download(chunk, start='2013-01-01', end='2026-05-12',
                             progress=False, auto_adjust=False, threads=True)
            cl = df['Close'] if isinstance(df.columns, pd.MultiIndex) else df[['Close']].rename(columns={'Close': chunk[0]})
            for c in cl.columns:
                s = cl[c].dropna()
                if len(s) >= 252:
                    closes[c] = s
        except Exception:
            pass

    wide = pd.DataFrame(closes).sort_index()
    print(f"  Wide closes: {wide.shape} in {time.time()-t0:.0f}s")

    # 50-day MA and % above
    ma50 = wide.rolling(50).mean()
    pct_above_50 = (wide > ma50).sum(axis=1) / wide.notna().sum(axis=1) * 100

    # 52-week high/low counts
    hi_252 = wide.rolling(252).max()
    lo_252 = wide.rolling(252).min()
    new_highs = (wide >= hi_252).sum(axis=1)
    new_lows = (wide <= lo_252).sum(axis=1)
    nhnl_diff = new_highs - new_lows

    # SPX close + 50DMA + distance
    spy = yf.download('^GSPC', start='2013-01-01', end='2026-05-12', progress=False, auto_adjust=False)['Close']
    if isinstance(spy, pd.DataFrame):
        spy = spy.iloc[:, 0]
    spy_ma50 = spy.rolling(50).mean()
    spx_pct = (spy / spy_ma50 - 1) * 100

    out = pd.DataFrame({
        'date': pct_above_50.index,
        'pct_above_50dma': pct_above_50.values,
        'new_highs': new_highs.values,
        'new_lows': new_lows.values,
        'nhnl_diff': nhnl_diff.values,
        'n_tickers': wide.notna().sum(axis=1).values,
        'spx_close': spy.reindex(pct_above_50.index).values,
        'spx_pct_to_50dma': spx_pct.reindex(pct_above_50.index).values,
    })
    out.to_parquet(BREADTH_OUT)
    print(f"  ✓ Saved enriched breadth: {BREADTH_OUT}")
    print(f"  Last 5 days:")
    print(out.tail(5).to_string(index=False))
    return out


def stats(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) == 0:
        return {"N": 0, "mean": np.nan, "win": np.nan}
    return {"N": len(s),
            "mean": round(s.mean(), 4),
            "win": round((s > 0).mean(), 3)}


def load_if_per_cycle() -> pd.DataFrame:
    """Per-cycle pnl_50pct rows for 10% wings, dte_45."""
    df = pd.read_parquet(IF_DATA)
    df = df[(df["wing_pct"] == 0.10) & (df["entry_label"] == "dte_45")
            & (df["exit_rule"] == "50_pct")].copy()
    df = df[["ticker", "expiration", "entry_date", "pnl"]].rename(
        columns={"pnl": "pnl_50pct"})
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    return df


def add_gates(df: pd.DataFrame) -> pd.DataFrame:
    """Add G0..G5 binary entry filters."""
    BREADTH_THRESH = 55.0   # < 55% above 50DMA
    EXT_THRESH = 7.0        # SPX > 7% above own 50DMA

    df = df.copy()
    df["G0_term_inv"]       = df["term_spread"] > 0
    df["G1_ext_only"]       = df["spx_pct_to_50dma"] > EXT_THRESH
    df["G2_breadth_div"]    = (df["pct_above_50dma"] < BREADTH_THRESH) & (df["spx_pct_to_50dma"] > EXT_THRESH)
    df["G3_term_OR_breadth"]  = df["G0_term_inv"] | df["G2_breadth_div"]
    df["G4_term_AND_breadth"] = df["G0_term_inv"] & df["G2_breadth_div"]
    df["G5_nhnl_div"]         = (df["nhnl_diff"] < 0) & (df["spx_pct_to_50dma"] > EXT_THRESH)
    return df


def walkforward(df: pd.DataFrame) -> pd.DataFrame:
    """Run all 6 gates across the 4 walk-forward splits."""
    splits = [
        ("2013-01-01", "2020-12-31", "2021-01-01", "2023-12-31"),
        ("2014-01-01", "2021-12-31", "2022-01-01", "2024-12-31"),
        ("2015-01-01", "2022-12-31", "2023-01-01", "2025-12-31"),
        ("2016-01-01", "2023-12-31", "2024-01-01", "2026-04-30"),
    ]
    gates = ["G0_term_inv", "G1_ext_only", "G2_breadth_div",
             "G3_term_OR_breadth", "G4_term_AND_breadth", "G5_nhnl_div"]

    rows = []
    for train_s, train_e, val_s, val_e in splits:
        train = df[(df["entry_date"] >= train_s) & (df["entry_date"] <= train_e)]
        val = df[(df["entry_date"] >= val_s) & (df["entry_date"] <= val_e)]
        t_base = train["pnl_50pct"]
        v_base = val["pnl_50pct"]
        for g in gates:
            t_gate = train.loc[train[g], "pnl_50pct"]
            v_gate = val.loc[val[g], "pnl_50pct"]
            rows.append({
                "split": f"{val_s[:7]}..{val_e[:7]}",
                "gate": g,
                "train_N": len(t_gate),
                "train_mean": round(t_gate.mean(), 4) if len(t_gate) else np.nan,
                "train_lift_vs_base": round(t_gate.mean() - t_base.mean(), 4) if len(t_gate) else np.nan,
                "val_N": len(v_gate),
                "val_mean": round(v_gate.mean(), 4) if len(v_gate) else np.nan,
                "val_win": round((v_gate > 0).mean(), 3) if len(v_gate) else np.nan,
                "val_lift_vs_base": round(v_gate.mean() - v_base.mean(), 4) if len(v_gate) else np.nan,
                "val_freq_pct": round(len(v_gate) / max(len(v_base), 1) * 100, 1),
            })
    return pd.DataFrame(rows)


def summarize(wf: pd.DataFrame) -> pd.DataFrame:
    """Aggregate stability across the 4 splits."""
    rows = []
    for g in wf["gate"].unique():
        gw = wf[wf["gate"] == g]
        # baseline mean across splits for the same gate's val_mean - val_lift = base mean
        positive_lift_count = (gw["val_lift_vs_base"] > 0).sum()
        rows.append({
            "gate": g,
            "splits_with_lift": f"{positive_lift_count}/4",
            "mean_val_N":  int(round(gw["val_N"].mean())),
            "mean_val_lift": round(gw["val_lift_vs_base"].mean(), 4),
            "mean_val_freq_pct": round(gw["val_freq_pct"].mean(), 1),
            "mean_val_win": round(gw["val_win"].mean(), 3),
        })
    return pd.DataFrame(rows)


def main() -> None:
    # 1. Build (or load) enriched breadth
    if BREADTH_OUT.exists():
        print(f"Loading cached enriched breadth: {BREADTH_OUT}")
        breadth = pd.read_parquet(BREADTH_OUT)
    else:
        print("Building enriched breadth series...")
        breadth = build_enriched_breadth()
    breadth["date"] = pd.to_datetime(breadth["date"])

    # 2. Load IF cycles + signal, merge
    print("\nLoading IF cycles + SPY signal series...")
    cycles = load_if_per_cycle()
    sig = pd.read_parquet(SIGNAL).rename(columns={"trade_date": "entry_date"})[
        ["entry_date", "term_spread", "vrp", "iv_rank"]]
    sig["entry_date"] = pd.to_datetime(sig["entry_date"])

    df = cycles.merge(sig, on="entry_date", how="left")
    df = df.merge(breadth.rename(columns={"date": "entry_date"}), on="entry_date", how="left")
    df = df.dropna(subset=["term_spread", "pnl_50pct",
                           "pct_above_50dma", "spx_pct_to_50dma", "nhnl_diff"])
    print(f"  Merged: {len(df)} IF cycles with full feature set")
    print(f"  Cycle date range: {df['entry_date'].min().date()} → {df['entry_date'].max().date()}")

    # 3. Add gate columns
    df = add_gates(df)

    # Quick gate-firing summary
    print("\n=== Gate firing frequency (whole dataset) ===")
    for g in ["G0_term_inv", "G1_ext_only", "G2_breadth_div",
              "G3_term_OR_breadth", "G4_term_AND_breadth", "G5_nhnl_div"]:
        fire = df[g].sum()
        print(f"  {g:24s}: {fire:6d} / {len(df)}  ({fire/len(df)*100:5.1f}%)")

    # 4. Walk-forward
    print("\nRunning walk-forward across 4 splits × 6 gates...")
    wf = walkforward(df)
    wf.to_parquet(WALKFWD_OUT, index=False)
    print(f"  ✓ Saved: {WALKFWD_OUT}")

    # 5. Per-split detail
    print("\n=== Walk-forward detail (per split × gate) ===")
    print(wf.to_string(index=False))

    # 6. Stability summary
    print("\n=== Stability summary (across 4 splits) ===")
    summary = summarize(wf)
    print(summary.to_string(index=False))

    # 7. Decision pass/fail for each gate vs G0
    print("\n=== Promotion eligibility (vs G0 baseline) ===")
    g0_row = summary[summary["gate"] == "G0_term_inv"].iloc[0]
    g0_lift = g0_row["mean_val_lift"]
    g0_N = g0_row["mean_val_N"]
    for _, r in summary.iterrows():
        if r["gate"] == "G0_term_inv":
            print(f"  {r['gate']:24s}  BASELINE  (lift={g0_lift:+.4f}, mean_N={g0_N})")
            continue
        lift_ok = r["mean_val_lift"] >= 0.8 * g0_lift if g0_lift > 0 else r["mean_val_lift"] >= g0_lift
        freq_ok = r["mean_val_N"] > g0_N
        stab_ok = int(r["splits_with_lift"].split("/")[0]) >= 3
        verdict = "PROMOTE" if (lift_ok and freq_ok and stab_ok) else "REJECT"
        flags = []
        if not lift_ok: flags.append("expectancy")
        if not freq_ok: flags.append("frequency")
        if not stab_ok: flags.append("stability")
        flag_str = f" [fails: {', '.join(flags)}]" if flags else ""
        print(f"  {r['gate']:24s}  {verdict}  lift={r['mean_val_lift']:+.4f}, mean_N={r['mean_val_N']}, stable={r['splits_with_lift']}{flag_str}")


if __name__ == "__main__":
    main()
