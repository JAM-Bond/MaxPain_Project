"""Sector-drift / rotation detector for the daily alert (DESCRIPTIVE, strict).

Detects when the day's qualifier candidate slate is *concentrating* into a sector
relative to its own recent baseline — an early read on where attractive setups are
clustering ("rotation INTO health_care / OUT OF info-tech"). This is additional
context to help pick among candidates, NOT a gate and NOT an edge claim.

Honest scope: this measures where the SYSTEM's setup density is shifting (a function
of which sectors throw the vol/regime signatures each strategy wants). It often
coincides with real rotation but is not a measurement of fund flows. The sector-ETF
relative-strength confirm is what upgrades "setup density" toward "real rotation".

Method (3 layers, see docs design discussion 2026-06-06):
  L1  HHI of the candidate sector distribution → "are we clustering at all?"
  L2  per-sector z-score of candidate-share vs trailing baseline → "which sector, unusual?"
  L3  riser/faller pair, signed by structure direction → the rotation narrative
Confirm: 20-day sector-ETF relative return vs SPY.

Design guards:
  - Measured on the PRE-concentration-cap candidate set (GO + DOWNSIZE +
    SKIP_CONCENTRATION), deduped by symbol — the sector cap deliberately suppresses
    clustering in the placed book, so measuring post-cap would mask the signal.
  - _ETF / _UNKNOWN excluded from sector math.
  - STRICT: a sector fires only with >= MIN_NAMES candidates AND |z| >= Z_FIRE.

render_text/render_html return "" on a quiet day (nothing injected into the alert).
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path.home() / "MaxPain_Project"))
from lib.db import DB_PATH                      # noqa: E402
from lib.sector_map import get_sector, ETF_SENTINEL, UNKNOWN_SENTINEL  # noqa: E402

# ── Tunables (STRICT) ─────────────────────────────────────────────────────────
CAND_VERDICTS = ("GO", "DOWNSIZE", "SKIP_CONCENTRATION")  # pre-cap attractive set
MIN_NAMES = 3            # a sector needs >= this many candidates to fire
Z_FIRE = 2.0            # |z| threshold (strict)
TRAIL = 10              # trailing cycles for the baseline
MIN_CYCLES = 8          # need this many trailing cycles before z is trusted
RS_WINDOW = 20          # trading days for sector-ETF relative strength
NON_SECTORS = (ETF_SENTINEL, UNKNOWN_SENTINEL)

BULLISH = {"zebra_tier1", "zebra_tier2", "bull_put", "bull_put_mp"}  # premium-sell leans up
BEARISH = {"bear_call", "anti_zebra"}
LONGVOL = {"inverted_fly_pair", "inverted_fly_single"}

SECTOR_ETF = {
    "information_technology": "XLK", "health_care": "XLV", "financials": "XLF",
    "energy": "XLE", "industrials": "XLI", "consumer_discretionary": "XLY",
    "consumer_staples": "XLP", "utilities": "XLU", "materials": "XLB",
    "communication_services": "XLC", "real_estate": "XLRE",
}


def _load(conn):
    df = pd.read_sql_query(
        "SELECT run_date, symbol, structure, verdict FROM cycle_qualifier_runs", conn)
    df["sector"] = df["symbol"].map(get_sector)
    return df


def _daily_candidate_shares(df):
    """Per run_date: sector -> candidate share (single-name only), deduped by symbol.
    Returns (shares_wide, counts_by_date, structures_by_date_symbol)."""
    cand = df[df["verdict"].isin(CAND_VERDICTS)]
    shares, counts = {}, {}
    for rd, g in cand.groupby("run_date"):
        # dedupe symbol; keep its structures for direction
        sym_struct = g.groupby("symbol")["structure"].apply(set)
        sym_sector = g.groupby("symbol")["sector"].first()
        sectors = [sym_sector[s] for s in sym_struct.index
                   if sym_sector[s] not in NON_SECTORS]
        c = Counter(sectors)
        total = sum(c.values())
        counts[rd] = c
        if total:
            shares[rd] = {sec: n / total for sec, n in c.items()}
    wide = pd.DataFrame(shares).T.sort_index().fillna(0.0)  # rows=run_date, cols=sector
    return wide, counts


def _direction_mix(df, run_date, sector):
    """Bullish/bearish/longvol breakdown of a sector's candidates on run_date."""
    g = df[(df.run_date == run_date) & (df.verdict.isin(CAND_VERDICTS)) & (df.sector == sector)]
    sym_struct = g.groupby("symbol")["structure"].apply(set)
    b = be = lv = mixed = 0
    for structs in sym_struct:
        has_b, has_be = bool(structs & BULLISH), bool(structs & BEARISH)
        if has_b and not has_be:
            b += 1
        elif has_be and not has_b:
            be += 1
        elif structs & LONGVOL:
            lv += 1
        else:
            mixed += 1
    parts = []
    if b:
        parts.append(f"{b} bullish")
    if be:
        parts.append(f"{be} bearish")
    if lv:
        parts.append(f"{lv} long-vol")
    if mixed:
        parts.append(f"{mixed} mixed")
    lean = "bullish" if b > be else ("bearish" if be > b else "mixed")
    return lean, ", ".join(parts)


def _sector_rs(sectors):
    """20-day relative return (sector ETF − SPY) for the given sectors. Best-effort."""
    etfs = {SECTOR_ETF[s] for s in sectors if s in SECTOR_ETF}
    if not etfs:
        return {}
    try:
        import yfinance as yf
        tickers = sorted(etfs | {"SPY"})
        px = yf.download(tickers, period="3mo", interval="1d",
                         auto_adjust=True, progress=False, threads=True)["Close"]
        ret = px.iloc[-1] / px.iloc[-1 - RS_WINDOW] - 1
        spy = ret.get("SPY", np.nan)
        out = {}
        for s in sectors:
            etf = SECTOR_ETF.get(s)
            if etf and etf in ret and not np.isnan(spy):
                out[s] = (etf, float(ret[etf] - spy))
        return out
    except Exception:
        return {}


def compute_sector_drift(conn=None, run_date=None):
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    try:
        df = _load(conn)
    finally:
        if own:
            conn.close()

    wide, counts = _daily_candidate_shares(df)
    if wide.empty:
        return {"fired": False, "reason": "no candidate history"}
    run_date = run_date or wide.index[-1]
    if run_date not in wide.index:
        return {"fired": False, "reason": f"no candidates on {run_date}"}

    today = wide.loc[run_date]
    hist = wide.loc[wide.index < run_date].tail(TRAIL)
    n_cycles = len(hist)
    today_counts = counts[run_date]

    risers, fallers = [], []
    for sector in wide.columns:
        share = today[sector]
        cnt = today_counts.get(sector, 0)
        base = hist[sector] if sector in hist else pd.Series(dtype=float)
        mean, std = base.mean(), base.std()
        z = (share - mean) / std if (n_cycles >= MIN_CYCLES and std and std > 0) else np.nan
        rec = {"sector": sector, "count": int(cnt), "share": float(share),
               "base_share": float(mean) if n_cycles else np.nan, "z": z}
        if cnt >= MIN_NAMES and not np.isnan(z) and z >= Z_FIRE:
            risers.append(rec)
        elif not np.isnan(z) and z <= -Z_FIRE and mean and mean > 0:
            fallers.append(rec)

    # HHI of today's candidate sector distribution + its trailing z
    hhi_today = float((today ** 2).sum())
    hhi_hist = (hist ** 2).sum(axis=1)
    hhi_z = ((hhi_today - hhi_hist.mean()) / hhi_hist.std()
             if (n_cycles >= MIN_CYCLES and hhi_hist.std() > 0) else np.nan)

    fired = bool(risers)
    risers.sort(key=lambda r: -r["z"])
    fallers.sort(key=lambda r: r["z"])

    # direction + RS confirm only for fired sectors (lazy)
    rs = _sector_rs([r["sector"] for r in risers] + [f["sector"] for f in fallers]) if fired else {}
    for r in risers:
        r["lean"], r["mix"] = _direction_mix(df, run_date, r["sector"])
        r["rs"] = rs.get(r["sector"])
    for f in fallers:
        f["lean"], f["mix"] = _direction_mix(df, run_date, f["sector"])
        f["rs"] = rs.get(f["sector"])

    return {"fired": fired, "run_date": run_date, "n_cycles": n_cycles,
            "hhi": hhi_today, "hhi_z": hhi_z, "risers": risers, "fallers": fallers,
            "baseline_mature": n_cycles >= MIN_CYCLES}


def _rs_phrase(rec, rising):
    if not rec.get("rs"):
        return ""
    etf, rel = rec["rs"]
    agree = (rel > 0) if rising else (rel < 0)
    return f" [{etf} {rel*100:+.1f}% vs SPY 20d — {'confirms' if agree else 'diverges'}]"


def render_text(result):
    if not result.get("fired"):
        return ""
    lines = [f"SECTOR DRIFT WATCH (strict; HHI {result['hhi']:.2f}"
             + (f", z {result['hhi_z']:+.1f}" if not np.isnan(result['hhi_z']) else "") + "):"]
    for r in result["risers"]:
        lines.append(
            f"  ↑ {r['sector']} clustering — {r['count']} candidates "
            f"({r['share']*100:.0f}% of slate vs {r['base_share']*100:.0f}% avg, z {r['z']:+.1f}); "
            f"{r['mix']} ({r['lean']}-leaning){_rs_phrase(r, True)}")
    for f in result["fallers"]:
        lines.append(
            f"  ↓ {f['sector']} fading — {f['count']} ({f['share']*100:.0f}% vs "
            f"{f['base_share']*100:.0f}% avg, z {f['z']:+.1f}){_rs_phrase(f, False)}")
    if result["risers"] and result["fallers"]:
        lines.append(f"  → possible rotation INTO {result['risers'][0]['sector']} / "
                     f"OUT OF {result['fallers'][0]['sector']}")
    lines.append("  [descriptive: setup-density shift, not measured fund flows; not a gate]")
    return "\n".join(lines)


def render_html(result):
    if not result.get("fired"):
        return ""
    txt = render_text(result)
    body = txt.replace("\n", "<br>")
    return (f"<div style='font-size:13px;color:#1a5fb4;margin:8px 0;"
            f"padding:6px 10px;border-left:3px solid #1a5fb4;background:#f0f6ff'>{body}</div>")


def rotation_view(conn=None, n=12):
    """Standing, always-visible view: per-sector candidate counts over the last
    n cycles + today's share and trend z. Shows DEVELOPING drift before it trips
    the strict alert threshold (the 'weeks out' lens). Descriptive."""
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    try:
        df = _load(conn)
    finally:
        if own:
            conn.close()
    wide, counts = _daily_candidate_shares(df)
    if wide.empty:
        print("(no candidate history)")
        return
    dates = list(wide.index)[-n:]
    latest = dates[-1]
    cw = pd.DataFrame({d: counts[d] for d in dates}).fillna(0).astype(int)  # rows=sector
    cw = cw.reindex(cw[latest].sort_values(ascending=False).index)

    hhi = float((wide.loc[latest] ** 2).sum())
    print(f"SECTOR ROTATION VIEW — candidate count by sector, last {len(dates)} cycles "
          f"(pre-cap GO/DOWNSIZE/SKIP_CONC, deduped) | run {latest} | HHI {hhi:.2f}")
    hdr = f"{'sector':<24}" + "".join(f"{d[5:]:>6}" for d in dates) + f"{'now%':>7}{'z':>7}"
    print(hdr)
    print("-" * len(hdr))
    for sec in cw.index:
        cells = "".join(f"{int(cw.at[sec, d]):>6}" for d in dates)
        share_today = wide.at[latest, sec] if sec in wide.columns else 0.0
        base = (wide[sec].loc[wide.index < latest].tail(TRAIL)
                if sec in wide.columns else pd.Series(dtype=float))
        z = ((share_today - base.mean()) / base.std()
             if (len(base) >= MIN_CYCLES and base.std() and base.std() > 0) else np.nan)
        zs = f"{z:+.1f}" if not np.isnan(z) else "  —"
        flag = " ←FIRES" if (not np.isnan(z) and z >= Z_FIRE
                             and int(cw.at[sec, latest]) >= MIN_NAMES) else ""
        print(f"{sec:<24}{cells}{share_today*100:>6.0f}%{zs:>7}{flag}")
    print("-" * len(hdr))
    print(f"strict fire = count≥{MIN_NAMES} & z≥{Z_FIRE}; baseline = trailing {TRAIL} cycles "
          f"(needs ≥{MIN_CYCLES}). Descriptive — not a gate.")


if __name__ == "__main__":
    if "--view" in sys.argv:
        rotation_view()
        sys.exit(0)
    res = compute_sector_drift(run_date=(sys.argv[1] if len(sys.argv) > 1 else None))
    if res.get("fired"):
        print(render_text(res))
    else:
        # diagnostic view even when nothing fires (so you can see the near-misses)
        print(f"No sector drift fired (run {res.get('run_date')}, "
              f"{res.get('n_cycles')} trailing cycles, baseline "
              f"{'mature' if res.get('baseline_mature') else 'BUILDING'}).")
        if "hhi" in res:
            hz = res['hhi_z']
            print(f"  HHI {res['hhi']:.3f}" + (f" (z {hz:+.1f})" if not np.isnan(hz) else ""))
