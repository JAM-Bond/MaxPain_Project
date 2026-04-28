"""Analyze the new-tickers inverted_fly backtest vs existing universe baseline.

For each new ticker, produce 50%-only exit P&L stats at 10% and 15% wings.
Compare to existing universe findings: universe-wide baseline +$0.191/cycle at 10%.
Also: term-inversion filter gate per ticker.
"""
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path("/Users/josephmorris/MaxPain_Project")
NEW_DATA = ROOT / "data/backtest/results_if_new_tickers_slip025.parquet"
SIGNAL = ROOT / "data/profile/signal_vrp_termstruct_spy.parquet"


def main() -> None:
    df = pd.read_parquet(NEW_DATA)
    df = df[df["exit_rule"].isin(["50_pct", "dte_21"])]
    # Dedup per (ticker, expiration, entry_date, wing_pct) taking 50_pct only for headline
    df50 = df[df["exit_rule"] == "50_pct"].copy()
    df50["entry_date"] = pd.to_datetime(df50["entry_date"])

    sig = pd.read_parquet(SIGNAL).rename(columns={"trade_date": "entry_date"})[
        ["entry_date", "term_spread", "vrp", "iv_rank"]]
    sig["entry_date"] = pd.to_datetime(sig["entry_date"])
    df50 = df50.merge(sig, on="entry_date", how="left").dropna(
        subset=["term_spread", "vrp"])

    print("=" * 80)
    print("New-ticker inverted_fly: 50%-only exit, 10% and 15% wings, slip=0.25")
    print("=" * 80)

    for wing in [0.10, 0.15]:
        print(f"\n--- Wing {wing:.0%} of spot ---")
        sub = df50[df50["wing_pct"] == wing]
        # Per-ticker summary
        rows = []
        for tkr, g in sub.groupby("ticker"):
            pnl = g["pnl"]
            gate = g.loc[g["term_spread"] > 0, "pnl"]
            rows.append({
                "ticker": tkr,
                "N": len(pnl),
                "mean": round(pnl.mean(), 4),
                "median": round(pnl.median(), 4),
                "win": round((pnl > 0).mean(), 3),
                "worst": round(pnl.min(), 2),
                "best": round(pnl.max(), 2),
                "N_gate": len(gate),
                "mean_gate": round(gate.mean(), 4) if len(gate) else np.nan,
                "gate_lift": round(gate.mean() - pnl.mean(), 4) if len(gate) else np.nan,
                "win_gate": round((gate > 0).mean(), 3) if len(gate) else np.nan,
            })
        per_tkr = pd.DataFrame(rows).sort_values("mean", ascending=False)
        print(per_tkr.to_string(index=False))
        # Group summary
        print(f"\n  Group mean (all new tickers at {wing:.0%} wings): "
              f"{sub['pnl'].mean():+.4f} per cycle, "
              f"win {(sub['pnl']>0).mean():.3f}, N={len(sub)}")

    # Annual stability for the new tickers at 10% wings
    print("\n" + "=" * 80)
    print("Per-ticker annual mean P&L at 10% wings (check 2026 behavior)")
    print("=" * 80)
    sub10 = df50[df50["wing_pct"] == 0.10].copy()
    sub10["year"] = sub10["entry_date"].dt.year
    piv = sub10.pivot_table(index="ticker", columns="year", values="pnl", aggfunc="mean")
    print(piv.round(3).to_string(float_format=lambda x: f"{x:+.2f}" if pd.notna(x) else "   - "))

    # Walk-forward on new tickers
    print("\n" + "=" * 80)
    print("Walk-forward (train 2013-2022, val 2023-2026) on new tickers at 10% wings")
    print("=" * 80)
    train_end = "2022-12-31"
    wf_rows = []
    for tkr, g in sub10.groupby("ticker"):
        train = g[g["entry_date"] <= train_end]["pnl"]
        val = g[g["entry_date"] > train_end]["pnl"]
        if len(train) == 0 or len(val) == 0:
            continue
        wf_rows.append({
            "ticker": tkr,
            "N_train": len(train),
            "mean_train": round(train.mean(), 4),
            "win_train": round((train > 0).mean(), 3),
            "N_val": len(val),
            "mean_val": round(val.mean(), 4),
            "win_val": round((val > 0).mean(), 3),
            "lift": round(val.mean() - train.mean(), 4),
        })
    wf = pd.DataFrame(wf_rows).sort_values("mean_val", ascending=False)
    print(wf.to_string(index=False))

    # Save
    out_dir = ROOT / "data/profile"
    per_tkr.to_parquet(out_dir / "if_new_tickers_per_ticker.parquet", index=False)
    wf.to_parquet(out_dir / "if_new_tickers_walkforward.parquet", index=False)


if __name__ == "__main__":
    main()
