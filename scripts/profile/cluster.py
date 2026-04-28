#!/usr/bin/env python3.11
"""Cluster the symbol profile into behavioral archetypes.

Loads profile_v1.parquet, filters to clustering-eligible tickers, standardizes features,
fits KMeans for several k values, reports silhouette scores, and for a chosen k writes
per-ticker cluster assignments plus a centroid summary for naming archetypes.

Usage:
    python3.11 cluster.py --ks 6 8 10 12              # compare, don't save
    python3.11 cluster.py --ks 10 --save              # fit k=10, save assignments
    python3.11 cluster.py --ks 10 --save --topn 30    # dump 30 members per cluster
"""
import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).parent))
import config as C

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cluster")


CLUSTER_FEATURES = [
    # Liquidity / structure — log-scaled because of heavy right tails
    "log_median_total_oi",
    "log_median_total_volume",
    "log_median_n_contracts",
    "median_n_expirations",
    "has_weekly_frac",
    "log_median_stk_px",
    # Volatility personality
    "median_atm_iv",
    "iv_regime_range",
    "median_iv_skew_10d",
    "realized_vol_annualized",
    # Engineered — the VRP proxy
    "vrp_static",
]


def prepare_features(profile: pd.DataFrame, min_history: int = 1000) -> pd.DataFrame:
    df = profile.copy()
    df["log_median_total_oi"] = np.log1p(df["median_total_oi"])
    df["log_median_total_volume"] = np.log1p(df["median_total_volume"])
    df["log_median_n_contracts"] = np.log1p(df["median_n_contracts"])
    df["log_median_stk_px"] = np.log1p(df["median_stk_px"])
    df["vrp_static"] = df["median_atm_iv"] - df["realized_vol_annualized"]

    eligible = df[df["history_days"] >= min_history].copy()
    before = len(eligible)
    eligible = eligible.dropna(subset=CLUSTER_FEATURES)
    log.info("Candidates: %d history-qualified, %d after non-NaN feature filter",
             before, len(eligible))
    return eligible


def fit_and_score(X: np.ndarray, k: int, seed: int = 42) -> tuple[KMeans, float]:
    km = KMeans(n_clusters=k, n_init=10, random_state=seed)
    labels = km.fit_predict(X)
    sample = min(5000, len(X))  # silhouette on all points is expensive
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(X), size=sample, replace=False) if len(X) > sample else np.arange(len(X))
    sil = silhouette_score(X[idx], labels[idx])
    return km, sil


def centroid_table(km: KMeans, scaler: StandardScaler, feature_names: list[str]) -> pd.DataFrame:
    centers_original = scaler.inverse_transform(km.cluster_centers_)
    return pd.DataFrame(centers_original, columns=feature_names)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ks", type=int, nargs="+", default=[8, 10, 12],
                        help="Candidate k values for KMeans")
    parser.add_argument("--save", action="store_true",
                        help="If set and --ks has length 1, save cluster assignments")
    parser.add_argument("--topn", type=int, default=20,
                        help="Top N members (by OI) to list per cluster")
    parser.add_argument("--min-history", type=int, default=1000,
                        help="Minimum history_days to be eligible for clustering")
    args = parser.parse_args()

    if not C.PROFILE_PATH.exists():
        raise SystemExit(f"Profile not found at {C.PROFILE_PATH} — run build.py first")
    profile = pd.read_parquet(C.PROFILE_PATH)
    log.info("Loaded profile: %d tickers", len(profile))

    eligible = prepare_features(profile, min_history=args.min_history)
    X_raw = eligible[CLUSTER_FEATURES].to_numpy()
    scaler = StandardScaler()
    X = scaler.fit_transform(X_raw)
    log.info("Feature matrix: %s", X.shape)

    results = []
    fitted = {}
    for k in args.ks:
        km, sil = fit_and_score(X, k)
        results.append((k, sil))
        fitted[k] = km
        sizes = pd.Series(km.labels_).value_counts().sort_index().tolist()
        log.info("k=%d  silhouette=%.4f  sizes=%s", k, sil, sizes)

    if args.save and len(args.ks) == 1:
        k = args.ks[0]
        km = fitted[k]
        eligible = eligible.copy()
        eligible["cluster"] = km.labels_
        out_path = C.PROFILE_ROOT / f"clusters_k{k}.parquet"
        eligible.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)
        log.info("Wrote cluster assignments: %s", out_path)

        log.info("\nCentroid summary (feature means per cluster, original units):")
        centroids = centroid_table(km, scaler, CLUSTER_FEATURES)
        centroids.insert(0, "size", pd.Series(km.labels_).value_counts().sort_index().values)
        centroids.index.name = "cluster"
        log.info("\n%s", centroids.round(3).to_string())

        log.info("\nTop %d members per cluster (by median_total_oi):", args.topn)
        for c in sorted(eligible["cluster"].unique()):
            members = eligible[eligible["cluster"] == c].nlargest(args.topn, "median_total_oi")
            tickers = members["ticker"].tolist()
            log.info("  cluster %d (size %d): %s", c, (eligible['cluster']==c).sum(), ", ".join(tickers))


if __name__ == "__main__":
    main()
