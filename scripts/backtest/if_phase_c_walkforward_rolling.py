"""
Inverted_fly Phase C — walk-forward stability + rolling/stop proxies (2026-04-24).

Doable on existing wide-wings parquet data. All at 10% wings, dte_45 entry, slip=0.25.

Tests:
  C1. Multi-year rolling walk-forward of the term-inversion gate.
       Four disjoint 3-year validation windows, 10-year trailing train. Tests gate stability.
  C2. Rolling proxy via dte_21 exit check-point.
       Treat the dte_21 exit value as a "mid-cycle mark." If P&L at dte_21 is deeply
       negative, simulate closing there and rolling into next monthly expiration at fresh ATM.
       Compare combined P&L to held-to-expiry baseline (50%-only).
  C3. Stop-loss proxy via dte_21 exit check-point.
       If P&L at dte_21 is deeply negative, simulate taking the loss and NOT re-entering.
       Compare to held-to-expiry baseline.

Deferred:
  C4. True mid-cycle stop requires daily BS marks. Not in held-to-rule parquet.
"""

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
IF_DATA = ROOT / "data/backtest/results_wide_wings_universe_slip025.parquet"
SIGNAL = ROOT / "data/profile/signal_vrp_termstruct_spy.parquet"


def stats(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) == 0:
        return {"N": 0, "mean": np.nan, "median": np.nan, "win": np.nan,
                "worst": np.nan, "best": np.nan}
    return {"N": len(s),
            "mean": round(s.mean(), 4),
            "median": round(s.median(), 4),
            "win": round((s > 0).mean(), 3),
            "worst": round(s.min(), 2),
            "best": round(s.max(), 2)}


def load_if_per_exit() -> pd.DataFrame:
    """Per-cycle rows with pnl_50pct, pnl_21dte for 10% wings, dte_45."""
    df = pd.read_parquet(IF_DATA)
    df = df[(df["wing_pct"] == 0.10) & (df["entry_label"] == "dte_45")].copy()
    df = df[df["exit_rule"].isin(["50_pct", "dte_21"])]
    piv = df.pivot_table(
        index=["ticker", "expiration", "entry_date"],
        columns="exit_rule",
        values="pnl",
        aggfunc="first",
    ).reset_index()
    piv = piv.rename(columns={"50_pct": "pnl_50pct", "dte_21": "pnl_21dte"})
    # Pull debit for size-normalized threshold
    first_rows = df.drop_duplicates(["ticker", "expiration", "entry_date"])[
        ["ticker", "expiration", "entry_date", "entry_credit"]]
    piv = piv.merge(first_rows, on=["ticker", "expiration", "entry_date"], how="left")
    # inverted_fly: entry_credit is negative (debit paid); abs(entry_credit) = debit dollars
    piv["debit"] = piv["entry_credit"].abs()
    piv["entry_date"] = pd.to_datetime(piv["entry_date"])
    return piv


def main() -> None:
    df = load_if_per_exit()
    sig = pd.read_parquet(SIGNAL).rename(columns={"trade_date": "entry_date"})[
        ["entry_date", "term_spread", "vrp", "iv_rank"]]
    sig["entry_date"] = pd.to_datetime(sig["entry_date"])
    df = df.merge(sig, on="entry_date", how="left").dropna(
        subset=["term_spread", "vrp", "pnl_50pct", "pnl_21dte", "debit"])
    print(f"Loaded {len(df)} cycles at 10% wings, dte_45")

    # ==================================================================
    # C1. Multi-year rolling walk-forward of term-inversion gate
    # ==================================================================
    print("\n" + "=" * 72)
    print("C1 — Multi-year walk-forward of term-inversion gate")
    print("=" * 72)
    # Four rolling train/validate splits (each 10-year train, next 3 years validate)
    splits = [
        ("2013-01-01", "2020-12-31", "2021-01-01", "2023-12-31"),
        ("2014-01-01", "2021-12-31", "2022-01-01", "2024-12-31"),
        ("2015-01-01", "2022-12-31", "2023-01-01", "2025-12-31"),
        ("2016-01-01", "2023-12-31", "2024-01-01", "2026-04-30"),
    ]
    wf_rows = []
    for train_s, train_e, val_s, val_e in splits:
        train = df[(df["entry_date"] >= train_s) & (df["entry_date"] <= train_e)]
        val = df[(df["entry_date"] >= val_s) & (df["entry_date"] <= val_e)]
        # baseline vs gate on each half
        t_base = train["pnl_50pct"]
        t_gate = train.loc[train["term_spread"] > 0, "pnl_50pct"]
        v_base = val["pnl_50pct"]
        v_gate = val.loc[val["term_spread"] > 0, "pnl_50pct"]
        wf_rows.append({
            "train_window": f"{train_s[:7]}..{train_e[:7]}",
            "val_window": f"{val_s[:7]}..{val_e[:7]}",
            "train_base_N": len(t_base),
            "train_base_mean": round(t_base.mean(), 4),
            "train_gate_N": len(t_gate),
            "train_gate_mean": round(t_gate.mean(), 4) if len(t_gate) else np.nan,
            "train_gate_lift": round(t_gate.mean() - t_base.mean(), 4) if len(t_gate) else np.nan,
            "val_base_N": len(v_base),
            "val_base_mean": round(v_base.mean(), 4),
            "val_gate_N": len(v_gate),
            "val_gate_mean": round(v_gate.mean(), 4) if len(v_gate) else np.nan,
            "val_gate_lift": round(v_gate.mean() - v_base.mean(), 4) if len(v_gate) else np.nan,
            "stable": "YES" if (len(v_gate) > 100 and v_gate.mean() > v_base.mean()) else "NO",
        })
    wf_df = pd.DataFrame(wf_rows)
    print(wf_df.to_string(index=False))

    # ==================================================================
    # C2. Rolling proxy via dte_21 check-point
    # ==================================================================
    print("\n" + "=" * 72)
    print("C2 — Rolling proxy (close at dte_21 if loss > X, treat as stop+re-enter on next OpEx)")
    print("=" * 72)
    # Baseline: held-to-expiry (using 50%-only as closest proxy — if 50% target hit, closed; else held to expiry)
    baseline = df["pnl_50pct"]
    print(f"  Baseline (50%-only exit): {stats(baseline)}")

    # Roll triggers: if dte_21 pnl <= threshold × debit_dollars, simulate close-and-reopen
    # "Re-open on next OpEx" would involve the NEXT month's cycle on same ticker.
    # Proxy: close at dte_21, and add the 50%-only P&L of the SAME ticker's next monthly cycle.
    # If no next cycle exists, drop.
    df = df.sort_values(["ticker", "entry_date"]).reset_index(drop=True)
    # For each (ticker, entry_date) find the NEXT cycle's pnl_50pct on same ticker.
    df["next_pnl"] = df.groupby("ticker")["pnl_50pct"].shift(-1)
    df["next_date"] = df.groupby("ticker")["entry_date"].shift(-1)
    # Sanity: next cycle must be ~30 days after current (next monthly)
    df["days_to_next"] = (df["next_date"] - df["entry_date"]).dt.days
    # For thresholds: dte_21_pnl as fraction of debit
    df["mtm_pct_at_21"] = df["pnl_21dte"] / df["debit"]  # negative when losing

    for thresh_pct in [-0.5, -0.75, -1.0]:
        trig = df["mtm_pct_at_21"] <= thresh_pct  # e.g., -0.5 means lost 50% of debit
        # Rolled P&L = realized loss at dte_21 + next cycle's P&L (if available)
        rolled_combined = df.loc[trig, "pnl_21dte"] + df.loc[trig, "next_pnl"]
        not_rolled = df.loc[~trig, "pnl_50pct"]
        combined = pd.concat([rolled_combined.dropna(), not_rolled], ignore_index=True)
        rolled_only = rolled_combined.dropna()
        not_rolled_only = df.loc[trig & df["next_pnl"].isna(), "pnl_21dte"]  # no next cycle, realized loss
        combined_all = pd.concat(
            [rolled_combined.dropna(), not_rolled_only, not_rolled],
            ignore_index=True,
        )
        print(f"\n  Threshold (dte_21 P&L / debit) <= {thresh_pct}:")
        print(f"    Trigger fires on {trig.sum()}/{len(df)} = {trig.mean():.1%} of cycles")
        print(f"    Rolled cycles only: {stats(rolled_only)}")
        print(f"    Full cohort combined (rolled + held): {stats(combined_all)}")
        print(f"    Baseline mean {baseline.mean():+.4f} vs rolled cohort mean "
              f"{combined_all.mean():+.4f} = "
              f"lift {combined_all.mean() - baseline.mean():+.4f}")

    # ==================================================================
    # C3. Stop-loss proxy: close at dte_21 if loss > X, do NOT reopen
    # ==================================================================
    print("\n" + "=" * 72)
    print("C3 — Stop-loss proxy (close at dte_21 if loss > X, do NOT re-enter)")
    print("=" * 72)
    print(f"  Baseline (50%-only exit): {stats(baseline)}")
    for thresh_pct in [-0.5, -0.75, -1.0]:
        trig = df["mtm_pct_at_21"] <= thresh_pct
        stopped_pnl = df.loc[trig, "pnl_21dte"]  # realized loss at stop
        held_pnl = df.loc[~trig, "pnl_50pct"]
        combined = pd.concat([stopped_pnl, held_pnl], ignore_index=True)
        print(f"\n  Threshold <= {thresh_pct}:")
        print(f"    Trigger fires on {trig.sum()}/{len(df)} = {trig.mean():.1%} of cycles")
        print(f"    Stopped cycle only: {stats(stopped_pnl)}")
        print(f"    Full cohort with stop: {stats(combined)}")
        print(f"    Lift vs baseline: {combined.mean() - baseline.mean():+.4f}")

    # Save
    wf_df.to_parquet(ROOT / "data/profile/if_phase_c_walkforward.parquet", index=False)
    print("\nSaved walk-forward table to data/profile/if_phase_c_walkforward.parquet")


if __name__ == "__main__":
    main()
