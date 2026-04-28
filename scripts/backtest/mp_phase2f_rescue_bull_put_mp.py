"""MP Phase 2f — Rescue sweep on bull_put_mp.

Three interventions tested in sequence, all on the same 19-name cohort:
  1. Entry-gate filters — features at T-5 that might predict failure
  2. Spot-based stop — exit if daily spot breaches the short strike
  3. Signal-driven entry — filter by VRP / term-structure signals (quiet-market regime)

Target structure: bull_put_mp (87% win, +$0.010 mean hold-to-expiry)
Baseline to beat: +$0.010 mean, −$6.29 worst.

Also tracks daily spot (needed for intervention 2).
"""
from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
OUT_DIR = ROOT / "data/profile"

TIER1 = ["BKLN", "HYG", "JNK", "TLT"]
TIER2 = ["SPX", "SPY", "DIA", "QQQ", "IWM"]
TIER3 = ["XLU", "XLV", "IYR", "GLD", "VZ", "KO", "PG", "WMT", "EFA", "VNQ"]
COHORT = TIER1 + TIER2 + TIER3

SLIP_FRAC = 0.25


def third_friday(y, m):
    d = date(y, m, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7 + 14)


def monthly_opex(sy, ey):
    return [third_friday(y, m) for y in range(sy, ey + 1) for m in range(1, 13)]


def parse_exp(s):
    try:
        p = s.split("/")
        return pd.Timestamp(year=int(p[2]), month=int(p[0]), day=int(p[1]))
    except Exception:
        return None


def compute_max_pain(chain):
    c = chain.dropna(subset=["strike", "cOi", "pOi"])
    if c.empty:
        return None
    ks = c["strike"].values; co = c["cOi"].values; po = c["pOi"].values
    best_K, best = None, None
    for K in ks:
        t = (co * np.maximum(0.0, K - ks)).sum() + (po * np.maximum(0.0, ks - K)).sum()
        if best is None or t < best:
            best, best_K = t, float(K)
    return best_K


def nth(chain, ref, n):
    ks = sorted(chain["strike"].dropna().unique())
    arr = np.array(ks)
    i = int(np.argmin(np.abs(arr - ref)))
    t = i + n
    return float(ks[t]) if 0 <= t < len(ks) else None


def sell(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 - SLIP_FRAC * (ask - bid) / 2


def buy(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 + SLIP_FRAC * (ask - bid) / 2


def mtm_close_cost(chain_today, short_K, long_K):
    sp_row = chain_today[chain_today["strike"] == short_K]
    lp_row = chain_today[chain_today["strike"] == long_K]
    if sp_row.empty or lp_row.empty:
        return None
    sp = sp_row.iloc[0]; lp = lp_row.iloc[0]
    bb = buy(sp["pBidPx"], sp["pAskPx"])
    sb = sell(lp["pBidPx"], lp["pAskPx"])
    if bb is None or sb is None:
        return None
    return bb - sb


def settle_at_close(short_K, long_K, spot_close, entry_credit):
    short_intr = max(0.0, short_K - spot_close)
    long_intr = max(0.0, long_K - spot_close)
    return entry_credit + (-short_intr + long_intr)


def run_ticker(ticker, opex_list):
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(path, columns=["trade_date","expirDate","strike","stkPx","cOi","pOi","pBidPx","pAskPx","cMidIv","pMidIv"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["exp_dt"] = df["expirDate"].map(parse_exp)

    exp_map = {}
    for s in df["expirDate"].unique():
        d = parse_exp(s)
        if d is None:
            continue
        for opex in opex_list:
            if abs((d - opex).days) <= 1:
                exp_map[opex] = s
                break

    out = []
    for opex, exp_str in exp_map.items():
        sub = df[df["expirDate"] == exp_str]
        if sub.empty:
            continue
        target = opex - pd.Timedelta(days=5)
        pre = sub[sub["trade_date"] <= target]
        if pre.empty:
            continue
        t_entry = pre["trade_date"].max()
        chain_entry = pre[pre["trade_date"] == t_entry].copy()
        if chain_entry.empty:
            continue
        mp = compute_max_pain(chain_entry)
        if mp is None:
            continue
        spot = float(chain_entry["stkPx"].iloc[0])
        if spot < mp:
            continue
        short_K = nth(chain_entry, mp, 0)
        if short_K is None or short_K >= spot:
            continue
        long_K = nth(chain_entry, short_K, -1)
        if long_K is None:
            continue
        sp_row = chain_entry[chain_entry["strike"] == short_K].iloc[0]
        lp_row = chain_entry[chain_entry["strike"] == long_K].iloc[0]
        sp_px = sell(sp_row["pBidPx"], sp_row["pAskPx"])
        lp_px = buy(lp_row["pBidPx"], lp_row["pAskPx"])
        if sp_px is None or lp_px is None:
            continue
        entry_credit = sp_px - lp_px
        if entry_credit <= 0:
            continue

        # Entry features
        atm_row = chain_entry.iloc[(chain_entry["strike"] - spot).abs().argsort()[:1]].iloc[0]
        entry_iv = float((atm_row["cMidIv"] + atm_row["pMidIv"]) / 2) if pd.notna(atm_row["cMidIv"]) else np.nan
        cushion_pct = (spot - short_K) / spot
        mp_gap_pct = (spot - mp) / spot
        wing_width = short_K - long_K
        credit_pct = entry_credit / wing_width if wing_width > 0 else np.nan

        # Daily walk — track spot and MTM
        forward = sorted(sub[(sub["trade_date"] > t_entry) & (sub["trade_date"] <= opex)]["trade_date"].unique())
        daily = []
        pnl_if_stopped = None  # spot-based stop
        for d in forward:
            ch = sub[sub["trade_date"] == d]
            if ch.empty:
                continue
            dte = max(0, (opex.date() - d.date()).days)
            spot_d = float(ch["stkPx"].iloc[0])
            if d == opex:
                pnl_d = settle_at_close(short_K, long_K, spot_d, entry_credit)
            else:
                cc = mtm_close_cost(ch, short_K, long_K)
                pnl_d = entry_credit - cc if cc is not None else np.nan
            daily.append({"dte": dte, "spot": spot_d, "pnl": pnl_d})
            # Spot-based stop: exit if spot breaches short_K
            if pnl_if_stopped is None and spot_d <= short_K and pd.notna(pnl_d):
                pnl_if_stopped = pnl_d

        if not daily:
            continue
        pnl_expiry = daily[-1]["pnl"]
        spot_close = daily[-1]["spot"]

        out.append({
            "ticker": ticker, "opex": opex, "t_entry": t_entry,
            "spot_entry": spot, "spot_close": spot_close, "mp_k": mp,
            "short_K": short_K, "long_K": long_K,
            "entry_credit": entry_credit, "entry_iv": entry_iv,
            "cushion_pct": cushion_pct, "mp_gap_pct": mp_gap_pct,
            "wing_width": wing_width, "credit_pct": credit_pct,
            "pnl_expiry": pnl_expiry,
            "pnl_spot_stop": pnl_if_stopped if pnl_if_stopped is not None else pnl_expiry,
            "stopped_out": pnl_if_stopped is not None,
        })
    return out


def rescue_summary(df, mask, label):
    kept = df[mask]
    dropped = df[~mask]
    print(f"  {label}")
    print(f"    kept   N={len(kept):4d}  mean ${kept['pnl_expiry'].mean():+.4f}  median ${kept['pnl_expiry'].median():+.4f}  win {(kept['pnl_expiry']>0).mean():.3f}  worst ${kept['pnl_expiry'].min():+.2f}  total ${kept['pnl_expiry'].sum():+.2f}")
    if len(dropped) > 0:
        print(f"    dropped N={len(dropped):4d}  mean ${dropped['pnl_expiry'].mean():+.4f}  worst ${dropped['pnl_expiry'].min():+.2f}  (these were avoided)")


def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers — bull_put_mp with entry features + daily spot walk")

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        print(f"  [{i}/{len(COHORT)}] {t}: {len(rows)}")
    df = pd.DataFrame(all_rows)
    df["pnl_expiry"] = pd.to_numeric(df["pnl_expiry"], errors="coerce")
    df = df.dropna(subset=["pnl_expiry"])
    print(f"\nTotal cycles: {len(df):,}")
    if df.empty:
        return

    print("\n═══ Baseline (hold-to-expiry, no filter, no stop) ═══")
    print(f"  N={len(df)}  mean ${df['pnl_expiry'].mean():+.4f}  median ${df['pnl_expiry'].median():+.4f}  win {(df['pnl_expiry']>0).mean():.3f}  worst ${df['pnl_expiry'].min():+.2f}  total ${df['pnl_expiry'].sum():+.2f}")

    # ─── INTERVENTION 1: Entry-gate filters ───
    print("\n═══ INTERVENTION 1: Entry-gate filter quintile buckets ═══")
    for feat in ["cushion_pct", "mp_gap_pct", "credit_pct", "entry_iv"]:
        sub = df.dropna(subset=[feat]).copy()
        if len(sub) < 50:
            continue
        try:
            sub["q"] = pd.qcut(sub[feat], 5, labels=["Q1 (low)","Q2","Q3","Q4","Q5 (high)"], duplicates="drop")
        except Exception:
            continue
        agg = sub.groupby("q", observed=True).agg(
            n=("pnl_expiry","count"),
            feat_mean=(feat, "mean"),
            pnl_mean=("pnl_expiry","mean"),
            pnl_median=("pnl_expiry","median"),
            win=("pnl_expiry", lambda s: (s>0).mean()),
            worst=("pnl_expiry","min"),
        ).round(4)
        print(f"\n  Feature: {feat}")
        print(agg.to_string())

    # Candidate filter: drop bottom cushion quintile (cycles with tightest cushion to short)
    print("\n═══ Candidate filter test: drop bottom-quintile cushion cycles ═══")
    thr = df["cushion_pct"].quantile(0.20)
    rescue_summary(df, df["cushion_pct"] > thr, f"drop cushion_pct ≤ {thr:.4f}")

    # Candidate filter: drop low-credit cycles (credit < 5% of wing)
    print("\n═══ Candidate filter test: drop low credit_pct cycles ═══")
    thr = df["credit_pct"].quantile(0.20)
    rescue_summary(df, df["credit_pct"] > thr, f"drop credit_pct ≤ {thr:.4f}")

    # ─── INTERVENTION 2: Spot-based stop ───
    print("\n═══ INTERVENTION 2: Spot-based stop (exit if spot ≤ short_K intraday) ═══")
    df["pnl_spot_stop"] = pd.to_numeric(df["pnl_spot_stop"], errors="coerce")
    valid = df.dropna(subset=["pnl_spot_stop"])
    print(f"  N={len(valid)}  baseline mean ${valid['pnl_expiry'].mean():+.4f}")
    print(f"  Spot-stop: stopped-out cycles: {valid['stopped_out'].sum()} ({valid['stopped_out'].mean():.1%})")
    print(f"    mean ${valid['pnl_spot_stop'].mean():+.4f}  median ${valid['pnl_spot_stop'].median():+.4f}  win {(valid['pnl_spot_stop']>0).mean():.3f}  worst ${valid['pnl_spot_stop'].min():+.2f}  total ${valid['pnl_spot_stop'].sum():+.2f}")
    stopped = valid[valid["stopped_out"]]
    not_stopped = valid[~valid["stopped_out"]]
    print(f"    stopped-out cycles' outcome: mean ${stopped['pnl_spot_stop'].mean():+.4f}  if held: ${stopped['pnl_expiry'].mean():+.4f}")
    print(f"    never-breached cycles:        mean ${not_stopped['pnl_expiry'].mean():+.4f}")

    # ─── INTERVENTION 3: Signal-driven entry ───
    print("\n═══ INTERVENTION 3: Signal-driven entry (VRP + term structure on SPY) ═══")
    sig = pd.read_parquet(OUT_DIR / "signal_vrp_termstruct_spy.parquet")
    sig = sig[["trade_date","vrp","term_spread"]].copy()
    sig["trade_date"] = pd.to_datetime(sig["trade_date"])
    merged = df.merge(sig, left_on="t_entry", right_on="trade_date", how="left").drop(columns=["trade_date"])
    m = merged.dropna(subset=["vrp","term_spread"]).copy()
    print(f"  signal-merged cycles: {len(m)} / {len(df)}  (some missing signal data on early dates)")

    # Quiet-market filter: keep cycles with term_spread < 0 (contango) AND VRP > 0 (implied premium exists)
    quiet = m[(m["term_spread"] < 0) & (m["vrp"] > 0)]
    not_quiet = m[~((m["term_spread"] < 0) & (m["vrp"] > 0))]
    print(f"  filter: term_spread<0 (contango) AND vrp>0 (premium bid)")
    print(f"    kept   N={len(quiet):4d}  mean ${quiet['pnl_expiry'].mean():+.4f}  win {(quiet['pnl_expiry']>0).mean():.3f}  worst ${quiet['pnl_expiry'].min():+.2f}  total ${quiet['pnl_expiry'].sum():+.2f}")
    print(f"    dropped N={len(not_quiet):4d}  mean ${not_quiet['pnl_expiry'].mean():+.4f}  worst ${not_quiet['pnl_expiry'].min():+.2f}")

    # Tighter: only Q1/Q2 term-spread (deep contango)
    ts_q = m["term_spread"].quantile(0.40)
    deep_quiet = m[m["term_spread"] < ts_q]
    print(f"  tighter filter: term_spread in bottom-40% (deep contango, term_spread ≤ {ts_q:+.4f})")
    print(f"    kept   N={len(deep_quiet):4d}  mean ${deep_quiet['pnl_expiry'].mean():+.4f}  win {(deep_quiet['pnl_expiry']>0).mean():.3f}  worst ${deep_quiet['pnl_expiry'].min():+.2f}  total ${deep_quiet['pnl_expiry'].sum():+.2f}")

    # Inverted term structure SKIP filter
    inv = m[m["term_spread"] > 0]
    non_inv = m[m["term_spread"] <= 0]
    print(f"  SKIP days with inverted term structure (term_spread > 0):")
    print(f"    dropped inverted days: N={len(inv):4d}  mean ${inv['pnl_expiry'].mean():+.4f}  worst ${inv['pnl_expiry'].min():+.2f}")
    print(f"    kept non-inverted:     N={len(non_inv):4d}  mean ${non_inv['pnl_expiry'].mean():+.4f}  worst ${non_inv['pnl_expiry'].min():+.2f}  total ${non_inv['pnl_expiry'].sum():+.2f}")

    # ─── COMBINED: all three interventions ───
    print("\n═══ COMBINED: cushion filter + spot stop + non-inverted term structure ═══")
    cush_thr = df["cushion_pct"].quantile(0.20)
    combo = m[(m["cushion_pct"] > cush_thr) & (m["term_spread"] <= 0)].copy()
    # Use pnl_spot_stop where available
    combo["pnl_final"] = combo["pnl_spot_stop"].fillna(combo["pnl_expiry"])
    print(f"  kept   N={len(combo):4d}  mean ${combo['pnl_final'].mean():+.4f}  median ${combo['pnl_final'].median():+.4f}  win {(combo['pnl_final']>0).mean():.3f}  worst ${combo['pnl_final'].min():+.2f}  total ${combo['pnl_final'].sum():+.2f}")
    # Stopped-out share
    print(f"  stopped-out share: {combo['stopped_out'].sum()}/{len(combo)} ({combo['stopped_out'].mean():.1%})")

    df.to_parquet(OUT_DIR / "mp_phase2f_rescue_bull_put_mp.parquet", index=False)
    print("\nwrote: data/profile/mp_phase2f_rescue_bull_put_mp.parquet")


if __name__ == "__main__":
    main()
