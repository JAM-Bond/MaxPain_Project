#!/usr/bin/env python3.11
"""
Macro-sensitivity report generator — living report off the CANONICAL pipeline.

Reads data/macro/ (the canonical rolling-beta + regime-axis pipeline, the same
artifacts lib/macro_profile.py and the qualifier/daily-alert read) and writes a
plain-English report to docs/MACRO_SENSITIVITY_REPORT.md.

This REPLACES the deleted scripts/research/generate_macro_report.py, which read
the now-removed data/profile/macro_* research duplicate. That older report is
stale: it used a 6-factor set with a curve PCA (Level/Slope/Curvature) and
regime EVs 43/18/15. The canonical pipeline uses 7 raw factor *changes* (no
curve sub-PCA) and regime EVs ≈27/18/14 — so this is a rewrite, not a re-point.

Everything quantitative (regime axes, loadings, archetype buckets, stability
counts, diversification pairs, as-of date) is computed from the parquets at run
time, so re-running after a daily_refresh.sh keeps the report current.

Usage:
    python3.11 scripts/macro/generate_macro_report.py
    python3.11 scripts/macro/generate_macro_report.py --out /tmp/report.md
"""
from __future__ import annotations

import argparse
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from lib.sector_map import get_sector, is_cap_exempt  # noqa: E402

MACRO = ROOT / "data/macro"
AXES = MACRO / "regime_axes.parquet"
LOAD = MACRO / "regime_loadings.parquet"
PROFILE = MACRO / "macro_profile.parquet"
TAGS = MACRO / "beta_stability_tags.parquet"
OUT_DEFAULT = ROOT / "docs/MACRO_SENSITIVITY_REPORT.md"

# Plain-English label per regime bucket (signed dominant axis). Driven by the
# regime_axes labels but spelled out for the archetype section.
REGIME_PLAIN = {
    "PC1+": "reflation / rates-up (energy, banks, cyclicals)",
    "PC1-": "anti-reflation / long-duration (mega-cap growth, defensives, gold-ish)",
    "PC2+": "strong-dollar / risk-off",
    "PC2-": "weak-dollar / risk-on (commodities, metals, EM)",
    "PC3+": "credit-stress",
    "PC3-": "credit-easing / pro-oil",
    "NEUTRAL": "low orthogonal macro tilt (≈market-only)",
}

# Factor → plain description for the factor table. The rate block is RAW
# (DGS10 level + T10Y2Y slope), not a curve PCA — that is the canonical method.
FACTOR_ROWS = [
    ("Level", "DGS10", "10-yr Treasury yield (overall rate level)",
     "rises when long rates rise"),
    ("Slope", "T10Y2Y", "10yr−2yr curve slope (steepness)",
     "rises when the curve steepens"),
    ("Inflation", "T10YIE", "10-yr breakeven inflation",
     "rises when inflation expectations rise"),
    ("Dollar", "DTWEXBGS", "broad trade-weighted USD",
     "rises when the dollar strengthens"),
    ("VIX", "VIXCLS", "equity-vol index (risk-off gauge)",
     "rises when volatility spikes"),
    ("Oil", "DCOILWTICO", "WTI crude",
     "rises when oil rises"),
    ("Credit", "DBAA−DAAA", "Moody's Baa−Aaa quality spread",
     "rises when credit spreads widen (risk-off)"),
]


def _fmt_list(names: list[str], per_line: int = 0) -> str:
    names = sorted(names)
    return ", ".join(names) if names else "—"


def _top_loaders(load: pd.DataFrame, col: str, n: int = 6) -> tuple[list[str], list[str]]:
    """Cohort names with the most positive / most negative loading on an axis."""
    s = load.dropna(subset=[col]).sort_values(col, ascending=False)
    pos = s.head(n)["ticker"].tolist()
    neg = s.tail(n)["ticker"].tolist()[::-1]
    return pos, neg


def section_regimes(axes: pd.DataFrame, load: pd.DataFrame) -> str:
    out = ["## 3. The three macro regimes", "",
           "A principal-component analysis *across* the seven standardized "
           "factor changes finds the uncorrelated \"regimes\" the macro world "
           "actually moves in. Two names exposed to the same regime are "
           "correlated even if they sit in different GICS sectors — this is the "
           "axis the macro-concentration cap diversifies across.", ""]
    fshort = ["level", "slope", "infl", "dollar", "vix", "oil", "credit"]
    for r in axes.itertuples():
        loads = ", ".join(f"{f} {getattr(r, 'load_' + f):+.2f}" for f in fshort)
        pc = r.axis
        pos, neg = _top_loaders(load, f"L_{pc}")
        out.append(f"- **{pc} ({r.ev_pct}% of factor variation) — {r.label}**  ")
        out.append(f"  factor loadings: {loads}  ")
        out.append(f"  cohort names that rise with it: {_fmt_list(pos)}  ")
        out.append(f"  cohort names that fall with it: {_fmt_list(neg)}")
    total_ev = axes["ev_pct"].sum()
    out.append("")
    out.append(f"_Top three axes capture {total_ev:.0f}% of cross-factor "
               f"variation. Each name's `regime_primary` is its single dominant "
               f"axis (signed); names with all-small loadings are NEUTRAL._")
    return "\n".join(out)


def section_trust(tags: pd.DataFrame) -> str:
    """Per-factor reliability from the stability tags (STABLE / MAGNITUDE_DEPENDENT
    / SIGN_FLIP), focused on MATERIAL betas (small betas are noise we don't use)."""
    name = {"mkt_d1": "Market", "DGS10_d1": "Level", "T10Y2Y_d1": "Slope",
            "T10YIE_d1": "Inflation", "DTWEXBGS_d1": "Dollar",
            "VIXCLS_d1": "VIX", "DCOILWTICO_d1": "Oil", "credit_d1": "Credit"}
    order = ["mkt_d1", "DGS10_d1", "T10YIE_d1", "credit_d1", "T10Y2Y_d1",
             "DTWEXBGS_d1", "VIXCLS_d1", "DCOILWTICO_d1"]
    rows = ["## 4. Which sensitivities to trust", "",
            "A beta is only useful if it holds its sign across regimes. "
            "Re-estimating within five macro eras tags each (name, factor) beta "
            "**STABLE** (sign + magnitude hold), **MAGNITUDE_DEPENDENT** (sign "
            "holds, size varies — still directionally usable), or **SIGN_FLIP** "
            "(flips — do not trust). Small (immaterial) betas are excluded; we "
            "don't size off them anyway.", "",
            "| Factor | Material betas | Sign holds (STABLE+MAG_DEP) | Sign flips |",
            "|---|---|---|---|"]
    for f in order:
        sub = tags[(tags["factor"] == f) & (tags["material"])]
        n = len(sub)
        if n == 0:
            rows.append(f"| {name[f]} | 0 (all betas immaterial) | — | — |")
            continue
        holds = int((sub["tag"] != "SIGN_FLIP").sum())
        flips = int((sub["tag"] == "SIGN_FLIP").sum())
        rows.append(f"| {name[f]} | {n} | {holds} ({100*holds/n:.0f}%) | "
                    f"{flips} ({100*flips/n:.0f}%) |")
    rows += ["",
             "Plain reading: the **market beta** is the one robustly stable, "
             "always-material input (used for sizing). The macro-factor betas "
             "tell a humbler story — even when material, **Level, Inflation, "
             "Slope and especially Credit flip sign for the majority of names "
             "across eras**, so no single macro-factor beta is a trustworthy "
             "standalone rule. **Dollar / VIX / Oil** betas are too small to be "
             "material for the cohort at all. This is exactly why the system "
             "diversifies across the *combined* orthogonal regime tilt "
             "(`regime_primary`) and treats the macro profile as a "
             "risk/diversification descriptor — **not** a selection or sizing "
             "edge (consistent with the rejected regime-conditioning backtest). "
             "The handful of bedrock large-beta names — leveraged-Treasury "
             "ETFs on rates, gold/metal miners on the dollar — are the "
             "exceptions whose sign never moves."]
    return "\n".join(rows)


def section_archetypes(profile: pd.DataFrame) -> str:
    out = ["## 5. Cohort macro archetypes", "",
           "Cohort names grouped by `regime_primary` — their dominant orthogonal "
           "macro bucket. This is exactly the dimension the qualifier's "
           "macro-concentration cap diversifies across (soft-downsize beyond 3 "
           "per bucket per OpEx).", ""]
    bucket_order = ["PC1+", "PC1-", "PC2-", "PC2+", "PC3+", "PC3-", "NEUTRAL"]
    counts = profile["regime_primary"].value_counts().to_dict()
    for b in bucket_order:
        if b not in counts:
            continue
        names = profile[profile["regime_primary"] == b]["ticker"].tolist()
        plain = REGIME_PLAIN.get(b, "")
        out.append(f"- **{b} — {plain}** ({len(names)}): {_fmt_list(names)}")
    return "\n".join(out)


def section_diversification(profile: pd.DataFrame) -> str:
    """Cross-sector cohort pairs with near-identical regime-loading vectors —
    the pairs the GICS sector cap treats as diversified but that carry the same
    macro DNA. Cosine on the (L_PC1, L_PC2, L_PC3) vectors."""
    df = profile.dropna(subset=["L_PC1", "L_PC2", "L_PC3"]).copy()
    df = df[df["regime_primary"] != "NEUTRAL"]            # need a real tilt
    df = df[~df["ticker"].apply(is_cap_exempt)]           # single names only
    df["sector"] = df["ticker"].apply(get_sector)
    df = df[df["sector"].astype(str).str.len() > 0]
    vecs = {r.ticker: np.array([r.L_PC1, r.L_PC2, r.L_PC3], float)
            for r in df.itertuples()}
    sect = dict(zip(df["ticker"], df["sector"]))
    pairs = []
    for a, b in combinations(sorted(vecs), 2):
        if sect[a] == sect[b]:
            continue                                      # cross-sector only
        va, vb = vecs[a], vecs[b]
        na, nb = np.linalg.norm(va), np.linalg.norm(vb)
        if na == 0 or nb == 0:
            continue
        cos = float(va @ vb / (na * nb))
        pairs.append((cos, a, sect[a], b, sect[b]))
    pairs.sort(reverse=True)
    out = ["## 6. Diversification gaps (the actionable part)", "",
           "Cross-sector cohort pairs whose regime-loading vectors point the "
           "same way — **the sector cap treats them as diversified, but they "
           "carry the same macro DNA.** These are the correlations the "
           "macro-concentration cap exists to catch.", ""]
    for cos, a, sa, b, sb in pairs[:10]:
        out.append(f"- `{cos:+.2f}`  **{a}** ({sa}) ≈ **{b}** ({sb})")
    out.append("")
    out.append("_Cosine on the top-3 regime axes (direction). The live cap uses "
               "the discrete `regime_primary` bucket; this table is the "
               "continuous view of the same structure._")
    return "\n".join(out)


def build_report(out_path: Path) -> Path:
    axes = pd.read_parquet(AXES)
    load = pd.read_parquet(LOAD)
    profile = pd.read_parquet(PROFILE)
    tags = pd.read_parquet(TAGS)

    as_of = str(profile["as_of_date"].iloc[0])[:10]
    regime = profile["regime"].iloc[0]
    n_cohort = len(profile)
    n_fit = load["ticker"].nunique()

    factor_tbl = "\n".join(
        f"| **{lab}** | {src} | {desc} | the stock {pos} |"
        for lab, src, desc, pos in FACTOR_ROWS)

    parts = [
        "# MaxPain Macro-Sensitivity Profile — Report",
        "",
        f"_Generated from the canonical `data/macro/` pipeline · betas as of "
        f"{as_of} · current-regime label `{regime}` · {n_fit} names fit, "
        f"{n_cohort} in trading cohorts._",
        "",
        "## 1. What this is",
        "",
        "A measured, per-name profile of how each cohort stock responds to the "
        "macro environment — interest rates, curve slope, inflation "
        "expectations, the dollar, equity vol, oil, and credit. It replaces "
        "intuition (\"banks like higher rates\") with rolling sensitivities from "
        "13 years of daily data, so cohort selection, sizing, and "
        "diversification can account for shared macro risk, not just sector "
        "labels. This is the live pipeline behind `lib/macro_profile.py`, the "
        "daily alert's MACRO CONCENTRATION block, and the qualifier's "
        "macro-concentration cap.",
        "",
        "## 2. The seven macro factors",
        "",
        "Each is a standardized daily *change*, so a beta reads as \"daily "
        "return response, in basis points, per +1 standard-deviation move\" — "
        "comparable across factors and market-controlled (sensitivity beyond "
        "plain market beta). The rate block is raw (Level = DGS10, Slope = "
        "T10Y2Y); there is no curve sub-PCA.",
        "",
        "| Factor | Source series | Plain meaning | A **positive** beta means… |",
        "|---|---|---|---|",
        factor_tbl,
        "",
        section_regimes(axes, load),
        "",
        section_trust(tags),
        "",
        section_archetypes(profile),
        "",
        section_diversification(profile),
        "",
        "## 7. How to use it",
        "",
        "1. **Macro-concentration cap (LIVE):** the qualifier soft-downsizes "
        "names beyond 3 per `regime_primary` bucket per OpEx — diversifying "
        "across macro regimes on top of the GICS sector cap. Two PC1+ names in "
        "different sectors are still one reflation bet.",
        "2. **Daily alert:** the MACRO CONCENTRATION block surfaces bucket "
        "clustering across open positions + candidates before each entry window.",
        "3. **Sizing context:** market beta is the trustworthy sizing input; "
        "use the macro-factor betas directionally (via the regime bucket), not "
        "as hard coefficients.",
        "4. **Post-mortem substrate:** when a position stops, read its bucket — "
        "\"PC1-: a long-duration growth name hit as rates backed up\" beats "
        "\"tech rotated.\" (`report_macro_attribution` in the post-mortem.)",
        "",
        "## 8. Methodology & caveats",
        "",
        "- Returns are **split-adjusted** (raw ORATS stkPx is split-unadjusted, "
        "which would corrupt any 252-day window spanning a split); per-name "
        "obs with |daily log return| > 0.80 dropped as data artifacts.",
        "- Betas are **rolling 252-day** multivariate OLS with SPY as a market "
        "control (so they are *partial* — incremental to market beta). Factors "
        "standardized; betas in bp per 1-SD move.",
        "- Regime axes are a cross-factor PCA over the standardized factor "
        "changes; per-name loadings are **market-residual** (SPY regressed out "
        "first), so broad-market ETFs land NEUTRAL while sector ETFs keep their "
        "factor tilt.",
        "- **Credit** = Moody's Baa−Aaa quality spread (ICE's HY-OAS series is "
        "licensing-truncated on FRED to 2023+).",
        "- **Survivorship bias:** today's universe with full history. **Stress "
        "betas** (what matters most for risk) rest on rare regimes → smaller "
        "effective sample.",
        "- **Correlation ≠ causation**, especially in cyclicals where a third "
        "factor may drive both the stock and the rate.",
        "- A one-time FOMC event-study cross-validation (corr +0.45 with the "
        "daily Level betas, 31 decisions) was run during research and is "
        "preserved in the project memory; it is **not** part of this daily "
        "pipeline (only 11 cut events, several COVID-emergency → crash-inflated).",
        "",
        f"_Data: `data/macro/{{regime_axes,regime_loadings,macro_profile,"
        f"beta_stability_tags,beta_regime_summary,beta_rolling_252d}}.parquet`. "
        f"Refresh with `scripts/macro/daily_refresh.sh`, then regenerate this "
        f"report with `python3.11 scripts/macro/generate_macro_report.py`._",
        "",
    ]
    report = "\n".join(parts)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()
    p = build_report(Path(args.out))
    print(f"Wrote macro-sensitivity report → {p}")
    print(f"  {len(p.read_text().splitlines())} lines, "
          f"{p.stat().st_size/1024:.1f} KB")


if __name__ == "__main__":
    main()
