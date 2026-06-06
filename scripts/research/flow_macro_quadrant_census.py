#!/usr/bin/env python3.11
"""
Flow × Macro quadrant census — DESCRIPTIVE ONLY (no pre-reg, no prediction).

For each of the 11 Select Sector SPDRs, each month, classify a 2x2 state:
  macro stance M = (trailing macro-beta fingerprint) · (recent 3m macro move)
  flow  stance F = FLOW3 (clean 3m organic flow %, split-adjusted + hygiene)
    ① M>0,F>0 confirmed-bull   ② M>0,F<0 HOLLOW-THESIS (the cell of interest)
    ③ M<0,F>0 fighting-tape    ④ M<0,F<0 confirmed-bear

Answers the two go/no-go questions before any sealed test:
  Trap 1 — is M just a proxy for recent price? → report corr(M, trailing 3m excess).
  Trap 2 — is cell ② even populated? → quadrant counts per sector + overall.
Then, purely descriptively, what forward 3m excess / absolute return / max
drawdown trails each cell. XLF/rates worked example printed at the end.

Usage: python3.11 -m scripts.research.flow_macro_quadrant_census
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
from scripts.backtest.sector_flow_rotation_study import flow_signals, SECTORS  # noqa: E402

WIN = 60          # trailing months for the macro-beta fingerprint
MINW = 36         # minimum months to estimate a fingerprint
FRED_DEEP = ROOT / "data/macro/fred_daily_deep.parquet"   # 2002+ (vs live 13y store)


def prices_monthly() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Month-end absolute monthly returns and excess-vs-SPY for the 11 sectors."""
    daily = yf.download(SECTORS + ["SPY"], start="2003-06-01", end="2026-06-05",
                        interval="1d", auto_adjust=True, progress=False)["Close"]
    m = daily.resample("ME").last()
    ret = m.pct_change()
    excess = ret[SECTORS].sub(ret["SPY"], axis=0)
    return ret[SECTORS], excess


def macro_moves() -> pd.DataFrame:
    """Standardized 3-month macro-move panel (expanding z, no lookahead).
    Built straight from the deep 2002+ FRED store, not the live 13y store."""
    f = pd.read_parquet(FRED_DEEP)
    f["date"] = pd.to_datetime(f["date"])
    w = (f.pivot_table(index="date", columns="series_id", values="value")
         .sort_index().ffill())
    m = w.resample("ME").last()
    mv = pd.DataFrame(index=m.index)
    mv["rates"] = m["DGS10"].diff(3)
    mv["curve"] = m["T10Y2Y"].diff(3)
    mv["inflexp"] = m["T10YIE"].diff(3)
    mv["credit"] = (m["DBAA"] - m["DAAA"]).diff(3)
    mv["oil"] = m["DCOILWTICO"].pct_change(3)
    mv["dollar"] = m["DTWEXBGS"].diff(3)
    mv["vix"] = m["VIXCLS"].diff(3)
    # expanding standardization (uses only data through each date)
    z = (mv - mv.expanding(min_periods=MINW).mean()) / mv.expanding(min_periods=MINW).std()
    return z.dropna(how="all")


def macro_stance(excess: pd.DataFrame, moves: pd.DataFrame) -> pd.DataFrame:
    """M_s,t = trailing fingerprint (fit on excess_s ~ moves over [t-WIN, t-1]) · moves_t."""
    idx = excess.index.intersection(moves.index)
    excess, moves = excess.loc[idx], moves.loc[idx]
    cols = list(moves.columns)
    M = pd.DataFrame(index=idx, columns=SECTORS, dtype=float)
    for s in SECTORS:
        y = excess[s]
        for i, t in enumerate(idx):
            lo = max(0, i - WIN)
            win = idx[lo:i]                      # strictly before t
            yy = y.loc[win].dropna()
            if len(yy) < MINW:
                continue
            w = yy.index
            X = np.column_stack([np.ones(len(w))] + [moves.loc[w, c].values for c in cols])
            if np.isnan(X).any():
                continue
            beta, *_ = np.linalg.lstsq(X, yy.values, rcond=None)
            xt = moves.loc[t, cols].values
            if np.isnan(xt).any():
                continue
            M.at[t, s] = float(beta[0] + xt @ beta[1:])
    return M


def fwd_stats(ret_abs: pd.DataFrame, excess: pd.DataFrame):
    """Forward 3m excess, forward 3m absolute return, forward 3m max drawdown (trough)."""
    fwd_ex3, fwd_abs3, fwd_dd3 = {}, {}, {}
    for s in SECTORS:
        ra, ex = ret_abs[s], excess[s]
        fe, fa, fd = {}, {}, {}
        for i in range(len(ra) - 3):
            t = ra.index[i]
            nxt_abs = ra.iloc[i + 1:i + 4]
            nxt_ex = ex.iloc[i + 1:i + 4]
            if nxt_abs.isna().any() or nxt_ex.isna().any():
                continue
            cum = (1 + nxt_abs).cumprod()
            fa[t] = float(cum.iloc[-1] - 1)            # 3m absolute total return
            fe[t] = float(nxt_ex.sum())                # 3m cumulative excess
            fd[t] = float(cum.min() - 1)               # deepest trough vs entry
        fwd_abs3[s], fwd_ex3[s], fwd_dd3[s] = pd.Series(fa), pd.Series(fe), pd.Series(fd)
    return (pd.DataFrame(fwd_ex3), pd.DataFrame(fwd_abs3), pd.DataFrame(fwd_dd3))


def main() -> int:
    ret_abs, excess = prices_monthly()
    flow3, _ = flow_signals()
    moves = macro_moves()
    M = macro_stance(excess, moves)
    F = flow3
    R3 = excess.rolling(3, min_periods=3).sum()          # trailing 3m excess (price proxy)
    fwd_ex3, fwd_abs3, fwd_dd3 = fwd_stats(ret_abs, excess)

    # align everything to a long tidy frame
    idx = M.index.intersection(F.index).intersection(R3.index)
    idx = [t for t in idx if t >= pd.Timestamp("2009-12-31")]   # after fingerprint warmup
    rows = []
    for t in idx:
        for s in SECTORS:
            m, f, r = M.at[t, s], F.at[t, s], R3.at[t, s]
            if pd.isna(m) or pd.isna(f):
                continue
            rows.append({
                "date": t, "sector": s, "M": m, "F": f, "R3": r,
                "fwd_ex3": fwd_ex3[s].get(t, np.nan),
                "fwd_abs3": fwd_abs3[s].get(t, np.nan),
                "fwd_dd3": fwd_dd3[s].get(t, np.nan),
            })
    df = pd.DataFrame(rows)
    df["cell"] = np.where(df.M > 0,
                          np.where(df.F > 0, "1_confirmed_bull", "2_hollow_thesis"),
                          np.where(df.F > 0, "3_fighting_tape", "4_confirmed_bear"))

    print("FLOW × MACRO QUADRANT CENSUS — descriptive only")
    print(f"  {len(df):,} sector-months, {df.date.min().date()}→{df.date.max().date()}, "
          f"{df.sector.nunique()} sectors")
    print("=" * 78)

    # Trap 1 — is macro stance just recent price?
    c_all = df[["M", "R3"]].corr().iloc[0, 1]
    print(f"\nTRAP 1 — corr(macro stance M, trailing 3m excess R3) = {c_all:+.2f}")
    print("  per-sector:", {s: round(float(g[['M','R3']].corr().iloc[0,1]), 2)
                            for s, g in df.groupby('sector')})
    print("  (≈0 → M carries info beyond price; ≈1 → redundant with the nulled H2)")

    # Trap 2 — quadrant populations
    print("\nTRAP 2 — quadrant populations (share of all sector-months):")
    vc = df.cell.value_counts().sort_index()
    for k, v in vc.items():
        print(f"  {k:18} {v:5,}  ({v/len(df):5.1%})")

    # Forward outcomes by cell (descriptive)
    print("\nFORWARD 3-MONTH OUTCOMES BY CELL (mean; dd=deepest trough vs entry):")
    print(f"  {'cell':18} {'n':>5} {'fwd_excess':>11} {'fwd_abs':>9} "
          f"{'fwd_maxDD':>10} {'%abs<0':>7}")
    for k, g in df.groupby("cell"):
        gg = g.dropna(subset=["fwd_abs3"])
        print(f"  {k:18} {len(gg):>5} {gg.fwd_ex3.mean()*100:>+10.2f}% "
              f"{gg.fwd_abs3.mean()*100:>+8.2f}% {gg.fwd_dd3.mean()*100:>+9.2f}% "
              f"{(gg.fwd_abs3<0).mean():>7.0%}")

    # Cell ② strong subset: top-tercile macro tailwind AND bottom-tercile flow
    m_hi = df.M >= df.M.quantile(0.67)
    f_lo = df.F <= df.F.quantile(0.33)
    strong2 = df[m_hi & f_lo].dropna(subset=["fwd_abs3"])
    base = df.dropna(subset=["fwd_abs3"])
    print(f"\nCELL ② STRONG (macro top-tercile tailwind ∧ flow bottom-tercile), "
          f"n={len(strong2)}:")
    print(f"  fwd excess {strong2.fwd_ex3.mean()*100:+.2f}% vs all {base.fwd_ex3.mean()*100:+.2f}%  |  "
          f"fwd maxDD {strong2.fwd_dd3.mean()*100:+.2f}% vs all {base.fwd_dd3.mean()*100:+.2f}%  |  "
          f"%abs<0 {(strong2.fwd_abs3<0).mean():.0%} vs {(base.fwd_abs3<0).mean():.0%}")

    # XLF / rates worked example — notable cell-② episodes
    print("\nXLF worked example — cell ② episodes (macro tailwind, money leaving):")
    xlf = df[(df.sector == "XLF") & (df.cell == "2_hollow_thesis")].copy()
    xlf = xlf.dropna(subset=["fwd_abs3"]).sort_values("M", ascending=False).head(8)
    print(f"  {'date':12} {'M':>6} {'F(flow3)':>9} {'fwd_abs3':>9} {'fwd_maxDD':>10}")
    for _, r in xlf.iterrows():
        print(f"  {str(r.date.date()):12} {r.M:>+6.3f} {r.F*100:>+8.1f}% "
              f"{r.fwd_abs3*100:>+8.1f}% {r.fwd_dd3*100:>+9.1f}%")

    OUT = ROOT / "data/profile/flow_macro_quadrant_census.parquet"
    df.to_parquet(OUT, index=False)
    print(f"\nWrote {OUT.relative_to(ROOT)}  ({len(df):,} sector-months)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
