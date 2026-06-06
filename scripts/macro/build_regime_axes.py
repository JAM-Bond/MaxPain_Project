#!/usr/bin/env python3.11
"""
Macro regime axes — cross-factor PCA add-on to the macro-sensitivity profile.

Grafted onto the May-2026 rolling-beta pipeline (2026-06-04). The rolling betas
already give per-factor sensitivities; this adds the ORTHOGONAL cross-factor
"regime axes" so the diversification cap can catch names that are macro-
correlated across different GICS sectors (which per-factor tiers can miss).

Method: cross-factor PCA over the standardized daily factor *changes* in
macro_join_13y.parquet → regime axes (PC1/PC2/PC3); then per-name MARKET-RESIDUAL
loadings (regress each ticker's daily return on SPY + the regime scores). Each
name also gets a `regime_primary` label (its dominant axis, signed) — a macro
bucket usable exactly like the GICS sector cap.

Inputs : data/macro/macro_join_13y.parquet
Outputs: data/macro/regime_axes.parquet      (axis definitions / loadings)
         data/macro/regime_loadings.parquet  (per-ticker loadings + regime_primary)
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
JOIN = ROOT / "data/macro/macro_join_13y.parquet"
AXES_OUT = ROOT / "data/macro/regime_axes.parquet"
LOAD_OUT = ROOT / "data/macro/regime_loadings.parquet"

# Cross-factor set (raw factor daily changes already in the join). credit_d1 is
# the Baa-Aaa quality spread change, computed here to match build_betas_rolling.
FACTORS = ["DGS10_d1", "T10Y2Y_d1", "T10YIE_d1", "DTWEXBGS_d1",
           "VIXCLS_d1", "DCOILWTICO_d1", "credit_d1"]
FSHORT = ["level", "slope", "infl", "dollar", "vix", "oil", "credit"]
MIN_OBS = 252
PRIMARY_MIN_BP = 5.0   # |loading| below this (bp/SD) → no dominant regime


def _label(loadings):
    order = np.argsort(-np.abs(loadings))
    return " ".join(f"{'+' if loadings[i] >= 0 else '-'}{FSHORT[i]}" for i in order[:2])


def main():
    from sklearn.preprocessing import StandardScaler
    from sklearn.decomposition import PCA

    j = pd.read_parquet(JOIN)
    j["date"] = pd.to_datetime(j["date"])
    if "credit_d1" not in j.columns:
        j["credit_d1"] = j["DBAA_d1"] - j["DAAA_d1"]

    # one factor row per date (factor changes are repeated across tickers)
    fac = (j[["date"] + FACTORS].drop_duplicates("date")
           .set_index("date").sort_index().dropna(how="any"))
    print(f"  factor matrix: {fac.shape} {fac.index.min().date()}..{fac.index.max().date()}", flush=True)

    Z = StandardScaler().fit_transform(fac.values)
    pca = PCA(n_components=3).fit(Z)
    scores = pd.DataFrame(pca.transform(Z), index=fac.index,
                          columns=["PC1", "PC2", "PC3"])
    ev = pca.explained_variance_ratio_

    ax = []
    for i in range(3):
        ax.append({"axis": f"PC{i+1}", "ev_pct": round(ev[i] * 100, 1),
                   "label": _label(pca.components_[i]),
                   **{f"load_{FSHORT[k]}": round(float(pca.components_[i][k]), 2)
                      for k in range(len(FSHORT))}})
    axes = pd.DataFrame(ax)
    axes.to_parquet(AXES_OUT, index=False)
    print("  REGIME AXES:", flush=True)
    for r in axes.itertuples():
        print(f"    {r.axis} {r.ev_pct:>5}%  {r.label}", flush=True)

    # market factor = SPY daily return by date
    spy = (j[j["ticker"] == "SPY"][["date", "log_ret_1d"]]
           .drop_duplicates("date").set_index("date")["log_ret_1d"])

    # per-name market-residual loadings on the regime axes
    rows = []
    for tk, g in j.groupby("ticker"):
        g = g[["date", "log_ret_1d"]].dropna().set_index("date").sort_index()
        d = g.join(scores, how="inner").join(spy.rename("mkt"), how="inner").dropna()
        if len(d) < MIN_OBS:
            continue
        X = np.column_stack([np.ones(len(d)), d["mkt"].values,
                             d[["PC1", "PC2", "PC3"]].values])
        beta, _, _, _ = np.linalg.lstsq(X, d["log_ret_1d"].values, rcond=None)
        L = beta[2:] * 1e4   # bp per SD of axis
        rec = {"ticker": tk, "L_PC1": round(float(L[0]), 1),
               "L_PC2": round(float(L[1]), 1), "L_PC3": round(float(L[2]), 1)}
        # primary regime bucket = dominant axis, signed (NEUTRAL if all small)
        k = int(np.argmax(np.abs(L)))
        rec["regime_primary"] = (f"PC{k+1}{'+' if L[k] >= 0 else '-'}"
                                 if abs(L[k]) >= PRIMARY_MIN_BP else "NEUTRAL")
        rows.append(rec)
    load = pd.DataFrame(rows).sort_values("ticker")
    load.to_parquet(LOAD_OUT, index=False)
    print(f"\n  per-name loadings: {len(load)} tickers -> {LOAD_OUT}", flush=True)
    print("  regime_primary distribution:",
          load["regime_primary"].value_counts().to_dict(), flush=True)


if __name__ == "__main__":
    main()
