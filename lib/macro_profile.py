"""Per-name macro-sensitivity profile reader + cohort helpers.

Reads the parquet built by scripts/macro/build_macro_profile.py and exposes
a small functional surface for the qualifier, daily alert, and post-mortem.

Source-of-truth: data/macro/macro_profile.parquet. Rebuild with
    python3.11 scripts/macro/build_macro_profile.py
which can run daily after build_betas_rolling.py (which depends on the
macro_join_13y.parquet that build_macro_join.py refreshes).

Key concepts (from Phase 3 stability validation):
  - β_mkt is the one quantitatively reliable input (94% of cohort STABLE)
  - β_dgs10 / β_credit / β_t10yie are regime-dependent for 35-50% of names;
    use only when the `beta_*_use` flag is True
  - dollar/oil/vol exposure are reported as tiers only (magnitudes too small
    for quantitative sizing, but directionally stable for diversification)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
PROFILE_PATH = ROOT / "data/macro/macro_profile.parquet"
THEMATIC_PATH = ROOT / "data/macro/thematic_beta.parquet"


@lru_cache(maxsize=1)
def load_thematic() -> pd.DataFrame:
    """Thematic-beta overlay (SOXX/QQQ, market-controlled) from
    scripts/macro/build_thematic_beta.py. Empty frame if not built yet."""
    if not THEMATIC_PATH.exists():
        return pd.DataFrame(columns=["ticker", "soxx_tier", "qqq_tier"])
    return pd.read_parquet(THEMATIC_PATH)


@lru_cache(maxsize=1)
def load_profile() -> pd.DataFrame:
    """Full macro profile table (cached for process lifetime)."""
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(
            f"{PROFILE_PATH} not found. Build it with:\n"
            f"  python3.11 scripts/macro/build_macro_profile.py"
        )
    return pd.read_parquet(PROFILE_PATH)


def get(ticker: str) -> dict | None:
    """One ticker's profile as a dict, or None if not in cohort."""
    df = load_profile()
    row = df[df["ticker"] == ticker]
    if row.empty:
        return None
    return row.iloc[0].to_dict()


def cohort_macro_concentration(tickers: list[str]) -> dict:
    """Count tier duplicates across a candidate list.

    Use this to surface macro-band concentration (analogous to sector cap):
    if 3 of 4 candidate trades are POS_HIGH on β_dgs10, they're all the same
    rate bet under one ticker symbol diversification.

    The primary dimension is `regime_primary` — the orthogonal cross-factor
    regime bucket (build_regime_axes.py). It catches names that are macro-
    correlated ACROSS sectors (e.g. a cruise line and a chipmaker that are both
    pure reflation plays load PC1+), which the per-factor tiers can miss. The
    raw-factor tiers are still reported as secondary context.

    Returns:
        {dimension: {bucket_label: [ticker, ...]}}
        e.g., {'regime_primary': {'PC1+': ['SCHW','APA','STX']},
               'beta_dgs10_tier': {'POS_HIGH': ['BAC','JPM','WFC']}}
        Only buckets with ≥2 names are returned. regime_primary first.
    """
    df = load_profile()
    sub = df[df["ticker"].isin(tickers)]
    out: dict = {}
    cols = ["beta_mkt_tier", "beta_dgs10_tier", "beta_credit_tier",
            "beta_t10yie_tier", "dollar_tier", "oil_tier", "vol_tier"]
    if "regime_primary" in sub.columns:        # orthogonal axis — the primary cap
        cols = ["regime_primary"] + cols
    for col in cols:
        groups = sub.groupby(col)["ticker"].apply(list).to_dict()
        dupes = {t: tk for t, tk in groups.items() if len(tk) >= 2 and t not in ("NEUTRAL", "NA")}
        if dupes:
            out[col] = dupes
    return out


# Plain-English gloss for the orthogonal macro archetypes (regime_primary).
# Tied to data/macro/regime_axes.parquet: PC1=+level+infl, PC2=+dollar+vix,
# PC3=+credit−oil (build_regime_axes.py). Keep in sync if the axes are rebuilt.
PC_GLOSS = {
    "PC1+": "reflation / cyclical — rallies on rising rates, inflation, oil",
    "PC1-": "long-duration growth — rallies on falling rates/inflation",
    "PC2+": "risk-off defensive — strong dollar, high vol",
    "PC2-": "risk-on cyclical / commodity — weak dollar, low vol",
    "PC3+": "credit-spread sensitive",
    "PC3-": "credit-tightening / oil-up sensitive",
}

_FACTOR_LABEL = {
    "beta_dgs10_tier": "rate β",
    "beta_credit_tier": "credit β",
    "beta_t10yie_tier": "inflation-exp β",
    "dollar_tier": "dollar",
    "oil_tier": "oil",
    "vol_tier": "vol",
}


def format_macro_concentration(tickers: list[str],
                               max_factor_lines: int = 4) -> tuple[list[str], int]:
    """Human-readable macro-concentration advisory for a candidate/promoted set.

    Wraps cohort_macro_concentration() and translates the orthogonal
    `regime_primary` archetype buckets into plain English. The headline is the
    archetype — names that are the SAME macro bet across different sectors /
    structures (the hidden concentration a per-sector cap misses); per-factor
    tiers follow as secondary context, capped to keep the advisory readable.

    Returns (lines, n_archetype_flags). Empty lines ⇒ no concentration found.
    """
    dupes = cohort_macro_concentration(tickers)
    th = load_thematic()
    themes = {r.ticker: (r.soxx_tier, r.qqq_tier)
              for r in th[th["ticker"].isin(tickers)].itertuples()}

    def _ai_exposed(tk: str) -> bool:
        s, q = themes.get(tk, ("NA", "NA"))
        return s == "HIGH" or q == "HIGH"

    lines: list[str] = []
    arche = dupes.get("regime_primary", {})
    for bucket, names in sorted(arche.items(), key=lambda kv: -len(kv[1])):
        gloss = PC_GLOSS.get(bucket, "")
        tail = f" — {gloss}" if gloss else ""
        lines.append(f"⚠ {len(names)} share archetype {bucket}{tail}: "
                     f"{', '.join(sorted(names))}  → one macro bet; size/diversify as one")
        # Thematic split WITHIN the macro group — the "AAPL is the diversifier" line
        exposed = sorted(n for n in names if _ai_exposed(n))
        plain = sorted(n for n in names if n in themes and not _ai_exposed(n))
        if len(exposed) >= 2 and plain:
            lines.append(f"   ↳ {len(exposed)} of these are AI/growth-exposed "
                         f"(high SOXX/QQQ-β): {', '.join(exposed)};  "
                         f"{', '.join(plain)} = the diversifier(s)")

    # Standalone theme clusters across the WHOLE set — catches AI names the macro
    # archetype splits apart (e.g. NVDA PC1- + PLTR PC3+ both ride AI-growth).
    for idx, proxy, label, bet in [(0, "SOXX", "AI/semiconductor", "chip"),
                                   (1, "QQQ", "megacap-growth / AI-software", "growth")]:
        hi = sorted(tk for tk, tiers in themes.items() if tiers[idx] == "HIGH")
        if len(hi) >= 2:
            lines.append(f"⚠ {len(hi)} share HIGH {label}-β ({proxy}): "
                         f"{', '.join(hi)}  → one {bet} bet")

    factor_lines = []
    for col, label in _FACTOR_LABEL.items():
        for tier, names in dupes.get(col, {}).items():
            factor_lines.append(
                f"· {len(names)} share {label} {tier}: {', '.join(sorted(names))}")
    lines.extend(factor_lines[:max_factor_lines])
    return lines, len(arche)


def rate_stress_warning(tickers: list[str], direction: str) -> list[dict]:
    """Names whose current rate β faces drag from a directional rate move.

    Parameters
    ----------
    tickers : open positions or candidates
    direction : 'UP' (yields rising) or 'DOWN' (yields falling)

    Returns
    -------
    List of {ticker, beta_dgs10, tier, drag_severity} for names with material
    rate exposure pointing the wrong way for `direction`. Names with
    beta_dgs10_use=False are skipped (Phase 3 said don't trust the β).
    """
    df = load_profile()
    sub = df[df["ticker"].isin(tickers) & (df["beta_dgs10_use"] == True)].copy()
    if direction == "UP":
        sub = sub[sub["beta_dgs10"] < -0.02]  # falls when yields rise
    elif direction == "DOWN":
        sub = sub[sub["beta_dgs10"] > 0.02]   # falls when yields fall
    else:
        raise ValueError(f"direction must be 'UP' or 'DOWN', got {direction!r}")

    sub = sub.copy()
    sub["drag_severity"] = sub["beta_dgs10"].abs()
    sub = sub.sort_values("drag_severity", ascending=False)
    return sub[["ticker", "beta_dgs10", "beta_dgs10_tier", "drag_severity"]].to_dict("records")


def cohort_by_tier(col: str, tier_value: str) -> list[str]:
    """All tickers matching a particular tier (e.g., 'POS_HIGH' rate β).

    Useful for cohort-construction queries:
        cohort_by_tier('beta_dgs10_tier', 'NEG_HIGH')  → defensive-rate names
        cohort_by_tier('dollar_tier', 'USD_INV_STRONG') → gold/metal names
    """
    df = load_profile()
    return df[df[col] == tier_value]["ticker"].tolist()
