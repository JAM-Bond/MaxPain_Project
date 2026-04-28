"""Regime-window cut of existing backtest parquets.

Filters entries by `entry_date` falling inside pre-registered bear/crash
windows and aggregates per structure x cluster, comparing to the full-sample
baseline. No engine re-run required.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
RESULTS_SLIP025 = ROOT / "data/backtest/results_slip025.parquet"
RESULTS_V2 = ROOT / "data/backtest/results_v2.parquet"
UNIVERSE = ROOT / "data/profile/universe_v1.parquet"
OUT_DIR = ROOT / "data/profile"

REGIMES = {
    "covid_2020":       ("2020-02-15", "2020-04-30"),
    "bear_2022":        ("2022-01-01", "2022-10-15"),
    "dec_2018":         ("2018-10-01", "2018-12-24"),
    "aug_2015":         ("2015-08-01", "2015-10-01"),
    "volmageddon_2018": ("2018-01-20", "2018-02-20"),
}


def summarize(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    g = df.groupby(group_cols, observed=True)
    out = g["pnl"].agg(
        n="count",
        mean_pnl="mean",
        median_pnl="median",
        win_rate=lambda s: (s > 0).mean(),
        total_pnl="sum",
    ).reset_index()
    return out


def regime_cut(df: pd.DataFrame, universe: pd.DataFrame) -> dict[str, pd.DataFrame]:
    df = df.merge(universe[["ticker", "cluster"]], on="ticker", how="left")

    tables = {}

    baseline = summarize(df, ["structure", "cluster"]).assign(regime="baseline")
    tables["baseline_struct_cluster"] = baseline

    pieces_struct_cluster = [baseline]
    pieces_struct_only = [summarize(df, ["structure"]).assign(regime="baseline")]

    for name, (start, end) in REGIMES.items():
        mask = (df["entry_date"] >= start) & (df["entry_date"] <= end)
        sub = df[mask]
        if len(sub) == 0:
            print(f"[{name}] no cycles in window {start}..{end}")
            continue
        pieces_struct_cluster.append(
            summarize(sub, ["structure", "cluster"]).assign(regime=name)
        )
        pieces_struct_only.append(
            summarize(sub, ["structure"]).assign(regime=name)
        )
        # per-ticker for the most interesting cells
        tbl = summarize(sub, ["structure", "ticker"]).assign(regime=name)
        tables[f"by_ticker_{name}"] = tbl

    tables["by_regime_struct_cluster"] = pd.concat(pieces_struct_cluster, ignore_index=True)
    tables["by_regime_struct"] = pd.concat(pieces_struct_only, ignore_index=True)
    return tables


def print_pivot(tbl: pd.DataFrame, value: str, title: str) -> None:
    # keep structures all, regimes ordered with baseline first
    regimes_order = ["baseline"] + list(REGIMES.keys())
    present = [r for r in regimes_order if r in tbl["regime"].unique()]
    piv = tbl.pivot_table(index="structure", columns="regime", values=value, aggfunc="first")
    piv = piv[present]
    print(f"\n=== {title} ({value}) ===")
    fmt = "{:8.3f}" if value != "n" else "{:8.0f}"
    print(piv.to_string(float_format=lambda x: fmt.format(x) if pd.notna(x) else "    -   "))


def print_cluster_pivot(tbl: pd.DataFrame, value: str, title: str) -> None:
    regimes_order = ["baseline"] + list(REGIMES.keys())
    present = [r for r in regimes_order if r in tbl["regime"].unique()]
    piv = tbl.pivot_table(
        index=["cluster", "structure"], columns="regime", values=value, aggfunc="first"
    )
    piv = piv[present]
    print(f"\n=== {title} ({value}) ===")
    fmt = "{:8.3f}" if value != "n" else "{:8.0f}"
    print(piv.to_string(float_format=lambda x: fmt.format(x) if pd.notna(x) else "    -   "))


def main() -> None:
    universe = pd.read_parquet(UNIVERSE)
    for label, path in [("slip025 (realistic friction)", RESULTS_SLIP025), ("v2 (mid pricing)", RESULTS_V2)]:
        print("#" * 80)
        print(f"# {label}: {path.name}")
        print("#" * 80)
        df = pd.read_parquet(path)
        df["entry_date"] = pd.to_datetime(df["entry_date"])
        tables = regime_cut(df, universe)

        print_pivot(tables["by_regime_struct"], "mean_pnl", "Per-structure mean P&L")
        print_pivot(tables["by_regime_struct"], "win_rate", "Per-structure win rate")
        print_pivot(tables["by_regime_struct"], "n", "Per-structure cycle count")
        print_cluster_pivot(
            tables["by_regime_struct_cluster"], "mean_pnl",
            "Per-structure x cluster mean P&L",
        )
        print_cluster_pivot(
            tables["by_regime_struct_cluster"], "n",
            "Per-structure x cluster cycle count",
        )

        if "slip025" in path.name:
            tag = "slip025"
        else:
            tag = "v2"
        out_struct = OUT_DIR / f"regime_by_struct_{tag}.parquet"
        out_struct_cluster = OUT_DIR / f"regime_by_struct_cluster_{tag}.parquet"
        tables["by_regime_struct"].to_parquet(out_struct, index=False)
        tables["by_regime_struct_cluster"].to_parquet(out_struct_cluster, index=False)
        print(f"\nwrote {out_struct.relative_to(ROOT)}")
        print(f"wrote {out_struct_cluster.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
