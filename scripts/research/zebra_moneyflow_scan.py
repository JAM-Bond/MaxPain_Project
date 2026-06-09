"""ZEBRA pre-commit money-flow context scan (DESCRIPTIVE — not a gate, not an edge claim).

For today's committed ZEBRA candidates (GO + DOWNSIZE from cycle_qualifier_runs),
answer one question: historically, what did each name's price do over the ZEBRA
holding horizon when its money flow was negative / neutral / positive — and which
bucket is it in right now?

Money flow = Chaikin Money Flow (CMF-20). ZEBRA is bullish (~100 delta), so the
forward STOCK return over ~75 calendar days (52 trading days) is the relevant lens.
This is conditional/descriptive context to eyeball before committing; it does NOT
assert predictive edge and is deliberately not wired into the qualifier.

Usage: python3.11 scripts/research/zebra_moneyflow_scan.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH  # noqa: E402

CMF_WIN = 20          # Chaikin Money Flow lookback
FWD_TD = 52           # forward trading days ≈ 75 calendar days (ZEBRA horizon)
NEG, POS = -0.05, 0.05  # CMF bucket thresholds
MIN_N = 30            # adequacy floor per bucket before we trust the stat


def committed_zebra_symbols():
    conn = sqlite3.connect(DB_PATH)
    latest = conn.execute("SELECT MAX(run_date) FROM cycle_qualifier_runs").fetchone()[0]
    df = pd.read_sql_query(
        "SELECT symbol, structure, verdict, size FROM cycle_qualifier_runs "
        f"WHERE run_date=? AND structure LIKE 'zebra%' AND verdict IN ('GO','DOWNSIZE')",
        conn, params=(latest,))
    conn.close()
    return latest, df.sort_values(["verdict", "symbol"]).reset_index(drop=True)


def cmf(df):
    """Chaikin Money Flow on a single-ticker OHLCV frame."""
    hi, lo, cl, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (hi - lo).replace(0, np.nan)
    mfm = ((cl - lo) - (hi - cl)) / rng        # money-flow multiplier, [-1, +1]
    mfv = mfm.fillna(0) * vol                   # money-flow volume
    return mfv.rolling(CMF_WIN).sum() / vol.rolling(CMF_WIN).sum()


def bucket(series):
    return np.where(series < NEG, "negative",
                    np.where(series > POS, "positive", "neutral"))


def scan():
    run_date, cand = committed_zebra_symbols()
    syms = list(cand["symbol"].unique())
    print(f"ZEBRA money-flow scan — qualifier run {run_date} | {len(syms)} committed names")
    print(f"CMF-{CMF_WIN} | fwd {FWD_TD} trading days (~75 cal) | buckets <{NEG} / neutral / >{POS}")
    print("=" * 118)

    import yfinance as yf
    raw = yf.download(syms, period="max", interval="1d", group_by="ticker",
                      auto_adjust=False, progress=False, threads=True)

    verdict_of = dict(zip(cand["symbol"], cand["verdict"]))
    rows = []
    for s in syms:
        try:
            df = raw[s].dropna(subset=["Close", "Volume"]).copy()
        except Exception:
            rows.append({"sym": s, "err": "no data"}); continue
        if len(df) < CMF_WIN + FWD_TD + 60:
            rows.append({"sym": s, "err": f"short hist ({len(df)}d)"}); continue
        df["cmf"] = cmf(df)
        df["bkt"] = bucket(df["cmf"])
        df["fwd"] = df["Close"].shift(-FWD_TD) / df["Close"] - 1.0
        cur = df.dropna(subset=["cmf"]).iloc[-1]
        hist = df.dropna(subset=["cmf", "fwd"])

        rec = {"sym": s, "verdict": verdict_of.get(s, "?"),
               "cur_cmf": cur["cmf"], "cur_bkt": cur["bkt"], "err": None}
        for b in ("negative", "neutral", "positive"):
            g = hist[hist["bkt"] == b]["fwd"]
            rec[f"{b}_n"] = len(g)
            rec[f"{b}_win"] = (g > 0).mean() if len(g) else np.nan
            rec[f"{b}_mean"] = g.mean() if len(g) else np.nan
            rec[f"{b}_p10dn"] = (g < -0.10).mean() if len(g) else np.nan
        rows.append(rec)
    return run_date, rows


def _fmt_bucket(rec, b):
    n = rec.get(f"{b}_n", 0)
    if not n:
        return f"{'—':>20}"
    flag = "" if n >= MIN_N else "*"
    return (f"N={n:<4}{flag} win={rec[f'{b}_win']*100:>4.0f}% "
            f"avg={rec[f'{b}_mean']*100:>+5.1f}% dn={rec[f'{b}_p10dn']*100:>3.0f}%")


def report(run_date, rows):
    ok = [r for r in rows if not r["err"]]
    bad = [r for r in rows if r["err"]]

    # surface currently-negative-flow names first (yellow flags for a bullish entry)
    order = {"negative": 0, "neutral": 1, "positive": 2}
    ok.sort(key=lambda r: (order.get(r["cur_bkt"], 9), r["sym"]))

    print(f"\n{'SYM':<6}{'VERD':<9}{'CMF':>7} {'NOW':<9} | "
          f"{'forward '+str(FWD_TD)+'d return when flow was…':<0}")
    print(f"{'':<31} | {'NEGATIVE':<28} {'NEUTRAL':<28} {'POSITIVE':<28}")
    print("-" * 118)
    for r in ok:
        print(f"{r['sym']:<6}{r['verdict']:<9}{r['cur_cmf']:>+6.2f} {r['cur_bkt']:<9} | "
              f"{_fmt_bucket(r,'negative'):<28} {_fmt_bucket(r,'neutral'):<28} {_fmt_bucket(r,'positive'):<28}")

    print("-" * 118)
    print("win = P(fwd return > 0)   avg = mean fwd return   dn = P(fwd < -10%)   "
          f"* = N<{MIN_N} (thin, low confidence)")

    neg_now = [r for r in ok if r["cur_bkt"] == "negative"]
    if neg_now:
        print(f"\n⚠ Currently in NEGATIVE money flow ({len(neg_now)}): "
              f"{', '.join(r['sym'] for r in neg_now)}")
        print("  (descriptive flag only — distribution flow, not a vetting gate; "
              "compare each name's NEGATIVE column to its POSITIVE column)")
    if bad:
        print(f"\nSkipped (data): " + ", ".join(f"{r['sym']}({r['err']})" for r in bad))


def _arrow(win):
    if win is None or np.isnan(win):
        return "  —  "
    if win > 0.52:
        return "↑"
    if win < 0.48:
        return "↓"
    return "≈"


def report_behavior(run_date, rows):
    """Pure description: did price tend to be HIGHER 52td (~75 cal) later, by flow bucket?"""
    ok = [r for r in rows if not r["err"]]
    ok.sort(key=lambda r: r["sym"])

    print(f"\nDIRECTIONAL BEHAVIOR — qualifier run {run_date} | {len(ok)} committed ZEBRA names")
    print(f"'higher' = price was up {FWD_TD} trading days (~75 cal) later. win = % of windows that ended higher.")
    print("=" * 104)
    print(f"{'SYM':<6}{'now':<10}| {'NEGATIVE flow':<26} {'NEUTRAL flow':<26} {'POSITIVE flow':<26}")
    print("-" * 104)
    for r in ok:
        cells = []
        for b in ("negative", "neutral", "positive"):
            n, win, avg = r[f"{b}_n"], r[f"{b}_win"], r[f"{b}_mean"]
            if not n:
                cells.append(f"{'—':<26}"); continue
            thin = "" if n >= MIN_N else "*"
            cells.append(f"{_arrow(win)} higher {win*100:>3.0f}% | avg {avg*100:>+5.1f}% (N={n}{thin})".ljust(26))
        print(f"{r['sym']:<6}{r['cur_bkt']:<10}| {cells[0]} {cells[1]} {cells[2]}")
    print("-" * 104)

    # The explicit ask: over full history, did each name go HIGHER after NEGATIVE flow?
    print("\nQ: Over full history, did each name go HIGHER after NEGATIVE money flow? (sorted by frequency)")
    print("-" * 104)
    negs = [r for r in ok if r["negative_n"]]
    negs.sort(key=lambda r: r["negative_win"], reverse=True)
    for r in negs:
        win, avg, n = r["negative_win"], r["negative_mean"], r["negative_n"]
        verdict = "YES — usually higher" if win > 0.55 else \
                  ("NO — usually lower" if win < 0.45 else "MIXED — ~coin flip")
        thin = "  (thin sample)" if n < MIN_N else ""
        print(f"  {r['sym']:<6} higher {win*100:>3.0f}% of the time | avg {avg*100:>+5.1f}% | "
              f"N={n:<5} → {verdict}{thin}")

    up = sum(1 for r in negs if r["negative_win"] > 0.50)
    up_pos = sum(1 for r in ok if r["positive_n"] and r["positive_win"] > 0.50)
    print("-" * 104)
    print(f"Tally: after NEGATIVE flow, {up}/{len(negs)} names rose >50% of the time over {FWD_TD}td. "
          f"After POSITIVE flow, {up_pos}/{sum(1 for r in ok if r['positive_n'])} did.")


if __name__ == "__main__":
    rd, rows = scan()
    if "--behavior" in sys.argv:
        report_behavior(rd, rows)
    else:
        report(rd, rows)
