"""MP Phase 2g — Roll-to-save study on bull_put_mp.

Pre-registered (see project_roll_to_save_study_plan.md):

Triggers (compare each separately):
  T1 spot_breach  — first day where spot_d <= original short_K
  T2 mtm_1x       — first day where daily MTM <= -1x entry_credit
  T3 spot_T2      — spot_breach AND trigger day is on or before T-2 calendar days

Roll styles:
  S1 straight_out   — close cycle N at trigger day; open new bull_put_mp for
                      NEXT monthly OpEx at trigger-day chain, KEEP original short_K
                      (requires original short_K to exist in new chain)
  S2 down_and_out   — close cycle N at trigger day; open new cycle at NEW MP
                      computed from trigger-day chain on new expiration

Signal gate on rolled cycle (optional): require SPY term_spread<0 (contango)
AND VRP>0 at the trigger day. If the gate fails, the rolled entry is skipped
and the realized close at the trigger day is accepted as final.

Requirements:
  - New rolled entry must be a net credit (entry_credit_new > 0); else skip roll
  - Cap at 1 roll per original cycle (cycle N+1 held to its own expiry no matter what)
  - Use same pricing convention as Phase 2f (slip=0.25)

Output: data/profile/mp_phase2g_roll_to_save.parquet (one row per original cycle,
with columns capturing baseline P&L and per-(trigger, style, filter) combined P&L.)
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
    ks = c["strike"].values
    co = c["cOi"].values
    po = c["pOi"].values
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
    """Cost to buy back the short and sell the long (close a bull_put)."""
    sp_row = chain_today[chain_today["strike"] == short_K]
    lp_row = chain_today[chain_today["strike"] == long_K]
    if sp_row.empty or lp_row.empty:
        return None
    sp = sp_row.iloc[0]
    lp = lp_row.iloc[0]
    bb = buy(sp["pBidPx"], sp["pAskPx"])  # buy back the short put
    sb = sell(lp["pBidPx"], lp["pAskPx"])  # sell the long put
    if bb is None or sb is None:
        return None
    return bb - sb


def settle_at_close(short_K, long_K, spot_close, entry_credit):
    """P&L at expiry given spot close."""
    short_intr = max(0.0, short_K - spot_close)
    long_intr = max(0.0, long_K - spot_close)
    return entry_credit + (-short_intr + long_intr)


def open_bull_put(chain_entry, short_K_target, long_K_offset=-1):
    """Build a bull_put at short_K_target (or closest strike). Returns dict or None.

    long_K_offset = -1 means 1 strike lower than short_K (standard).
    """
    if short_K_target is None:
        return None
    short_K = nth(chain_entry, short_K_target, 0)
    if short_K is None:
        return None
    long_K = nth(chain_entry, short_K, long_K_offset)
    if long_K is None or long_K >= short_K:
        return None
    sp_row = chain_entry[chain_entry["strike"] == short_K]
    lp_row = chain_entry[chain_entry["strike"] == long_K]
    if sp_row.empty or lp_row.empty:
        return None
    sp_px = sell(sp_row["pBidPx"].iloc[0], sp_row["pAskPx"].iloc[0])
    lp_px = buy(lp_row["pBidPx"].iloc[0], lp_row["pAskPx"].iloc[0])
    if sp_px is None or lp_px is None:
        return None
    credit = sp_px - lp_px
    if credit <= 0:
        return None
    return {"short_K": short_K, "long_K": long_K, "entry_credit": credit}


def find_exp_for_opex(df_ticker, opex_ts):
    """Given a ticker dataframe and a target OpEx Friday, return the expirDate string."""
    for s in df_ticker["expirDate"].unique():
        d = parse_exp(s)
        if d is None:
            continue
        if abs((d - opex_ts).days) <= 1:
            return s
    return None


def walk_cycle_from(sub_exp, start_t, opex, short_K, long_K, entry_credit):
    """Walk daily from AFTER start_t through opex. Return list of dicts with dte/spot/pnl."""
    forward = sorted(
        sub_exp[(sub_exp["trade_date"] > start_t) & (sub_exp["trade_date"] <= opex)][
            "trade_date"
        ].unique()
    )
    daily = []
    for d in forward:
        ch = sub_exp[sub_exp["trade_date"] == d]
        if ch.empty:
            continue
        dte = max(0, (opex.date() - d.date()).days)
        spot_d = float(ch["stkPx"].iloc[0])
        if d == opex:
            pnl_d = settle_at_close(short_K, long_K, spot_d, entry_credit)
        else:
            cc = mtm_close_cost(ch, short_K, long_K)
            pnl_d = entry_credit - cc if cc is not None else np.nan
        daily.append({"date": d, "dte": dte, "spot": spot_d, "pnl": pnl_d})
    return daily


def run_ticker(ticker, opex_list, sig_df):
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    cols = [
        "trade_date",
        "expirDate",
        "strike",
        "stkPx",
        "cOi",
        "pOi",
        "pBidPx",
        "pAskPx",
        "cMidIv",
        "pMidIv",
    ]
    df = pd.read_parquet(path, columns=cols)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # Map opex → expirDate string for this ticker
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

        # T-5 entry (Phase 2f convention)
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
        spot_entry = float(chain_entry["stkPx"].iloc[0])
        if spot_entry < mp:
            continue
        spread = open_bull_put(chain_entry, mp)
        if spread is None:
            continue
        short_K = spread["short_K"]
        long_K = spread["long_K"]
        entry_credit = spread["entry_credit"]
        if short_K >= spot_entry:
            continue

        # Daily walk
        daily = walk_cycle_from(sub, t_entry, opex, short_K, long_K, entry_credit)
        if not daily:
            continue
        pnl_expiry = daily[-1]["pnl"]
        if pd.isna(pnl_expiry):
            continue

        # Identify first trigger days for each trigger type
        first_spot_breach = None  # dict {date, pnl}
        first_mtm_1x = None
        for rec in daily:
            if pd.isna(rec["pnl"]):
                continue
            if first_spot_breach is None and rec["spot"] <= short_K:
                first_spot_breach = rec
            if first_mtm_1x is None and rec["pnl"] <= -entry_credit:
                first_mtm_1x = rec
            if first_spot_breach is not None and first_mtm_1x is not None:
                break

        # Next monthly OpEx after this one
        later_opex = [o for o in opex_list if o > opex]
        next_opex = later_opex[0] if later_opex else None
        next_exp_str = find_exp_for_opex(df, next_opex) if next_opex is not None else None

        # Baseline row
        row = {
            "ticker": ticker,
            "opex": opex,
            "t_entry": t_entry,
            "spot_entry": spot_entry,
            "mp_k": mp,
            "short_K": short_K,
            "long_K": long_K,
            "entry_credit": entry_credit,
            "wing_width": short_K - long_K,
            "pnl_baseline": pnl_expiry,
            "spot_close": daily[-1]["spot"],
            "t_breach_spot": first_spot_breach["date"] if first_spot_breach else pd.NaT,
            "pnl_at_spot_breach": first_spot_breach["pnl"] if first_spot_breach else np.nan,
            "dte_at_spot_breach": first_spot_breach["dte"] if first_spot_breach else np.nan,
            "t_breach_mtm": first_mtm_1x["date"] if first_mtm_1x else pd.NaT,
            "pnl_at_mtm_breach": first_mtm_1x["pnl"] if first_mtm_1x else np.nan,
            "dte_at_mtm_breach": first_mtm_1x["dte"] if first_mtm_1x else np.nan,
        }

        # Simulate rolls for each (trigger, style) combo
        combos = [
            ("T1_spot", first_spot_breach, False),
            ("T2_mtm", first_mtm_1x, False),
            ("T3_spotT2", first_spot_breach, True),  # T-2 filter
        ]
        styles = ["straight", "down_and_out"]

        for trig_label, trig_rec, time_filter in combos:
            if trig_rec is None:
                # Trigger never fired — baseline is the outcome across all styles
                for style in styles:
                    row[f"pnl_{trig_label}_{style}"] = pnl_expiry
                    row[f"pnl_{trig_label}_{style}_sig"] = pnl_expiry
                    row[f"rolled_{trig_label}_{style}"] = False
                    row[f"rolled_{trig_label}_{style}_sig"] = False
                continue

            trig_date = trig_rec["date"]
            trig_dte = trig_rec["dte"]
            pnl_at_trig = trig_rec["pnl"]

            # Time filter (T3): only roll if trig_dte >= 2 calendar days before opex
            # "on or before T-2 (Wed)" → at least 2 days of time left before expiry
            if time_filter and trig_dte < 2:
                # Past the deadline — accept the close at trigger
                for style in styles:
                    row[f"pnl_{trig_label}_{style}"] = pnl_at_trig
                    row[f"pnl_{trig_label}_{style}_sig"] = pnl_at_trig
                    row[f"rolled_{trig_label}_{style}"] = False
                    row[f"rolled_{trig_label}_{style}_sig"] = False
                continue

            # Check signal at trigger day
            sig_row = sig_df[sig_df["trade_date"] == trig_date]
            signal_ok = False
            if not sig_row.empty:
                vrp = sig_row.iloc[0]["vrp"]
                ts = sig_row.iloc[0]["term_spread"]
                signal_ok = bool(pd.notna(vrp) and pd.notna(ts) and vrp > 0 and ts < 0)

            # Prepare rollover: need next_opex + its chain snapshot on trig_date
            if next_opex is None or next_exp_str is None:
                for style in styles:
                    # Roll infeasible → accept close
                    row[f"pnl_{trig_label}_{style}"] = pnl_at_trig
                    row[f"pnl_{trig_label}_{style}_sig"] = pnl_at_trig
                    row[f"rolled_{trig_label}_{style}"] = False
                    row[f"rolled_{trig_label}_{style}_sig"] = False
                continue

            sub_next = df[df["expirDate"] == next_exp_str]
            # Chain at or just before trigger day on next-month expiry
            nc_pre = sub_next[sub_next["trade_date"] == trig_date]
            if nc_pre.empty:
                # Try last available day ≤ trig_date
                nc_prior = sub_next[sub_next["trade_date"] <= trig_date]
                if nc_prior.empty:
                    for style in styles:
                        row[f"pnl_{trig_label}_{style}"] = pnl_at_trig
                        row[f"pnl_{trig_label}_{style}_sig"] = pnl_at_trig
                        row[f"rolled_{trig_label}_{style}"] = False
                        row[f"rolled_{trig_label}_{style}_sig"] = False
                    continue
                roll_day = nc_prior["trade_date"].max()
                nc_pre = sub_next[sub_next["trade_date"] == roll_day]
            else:
                roll_day = trig_date

            chain_roll = nc_pre.copy()

            for style in styles:
                if style == "straight":
                    # Keep original short_K
                    target_short = short_K
                else:
                    # Recompute MP on new chain
                    new_mp = compute_max_pain(chain_roll)
                    target_short = new_mp

                new_spread = open_bull_put(chain_roll, target_short)
                if new_spread is None:
                    row[f"pnl_{trig_label}_{style}"] = pnl_at_trig
                    row[f"pnl_{trig_label}_{style}_sig"] = pnl_at_trig
                    row[f"rolled_{trig_label}_{style}"] = False
                    row[f"rolled_{trig_label}_{style}_sig"] = False
                    continue

                # Walk the new cycle to its expiry
                new_daily = walk_cycle_from(
                    sub_next,
                    roll_day,
                    next_opex,
                    new_spread["short_K"],
                    new_spread["long_K"],
                    new_spread["entry_credit"],
                )
                if not new_daily or pd.isna(new_daily[-1]["pnl"]):
                    row[f"pnl_{trig_label}_{style}"] = pnl_at_trig
                    row[f"pnl_{trig_label}_{style}_sig"] = pnl_at_trig
                    row[f"rolled_{trig_label}_{style}"] = False
                    row[f"rolled_{trig_label}_{style}_sig"] = False
                    continue

                pnl_new = new_daily[-1]["pnl"]
                combined = pnl_at_trig + pnl_new

                # Always-roll variant
                row[f"pnl_{trig_label}_{style}"] = combined
                row[f"rolled_{trig_label}_{style}"] = True

                # Signal-gated variant
                if signal_ok:
                    row[f"pnl_{trig_label}_{style}_sig"] = combined
                    row[f"rolled_{trig_label}_{style}_sig"] = True
                else:
                    row[f"pnl_{trig_label}_{style}_sig"] = pnl_at_trig
                    row[f"rolled_{trig_label}_{style}_sig"] = False

        out.append(row)
    return out


def summarize(df, col, label):
    s = df[col].dropna()
    if s.empty:
        return
    mean = s.mean()
    median = s.median()
    win = (s > 0).mean()
    worst = s.min()
    best = s.max()
    total = s.sum()
    print(
        f"  {label:<50} N={len(s):4d}  mean ${mean:+.4f}  med ${median:+.4f}  win {win:.3f}  worst ${worst:+6.2f}  best ${best:+5.2f}  total ${total:+8.2f}"
    )


def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers — Phase 2g roll-to-save on bull_put_mp\n")

    sig_df = pd.read_parquet(OUT_DIR / "signal_vrp_termstruct_spy.parquet")[
        ["trade_date", "vrp", "term_spread"]
    ].copy()
    sig_df["trade_date"] = pd.to_datetime(sig_df["trade_date"])

    all_rows = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list, sig_df)
        all_rows.extend(rows)
        print(f"  [{i:2d}/{len(COHORT)}] {t}: {len(rows)}")
    df = pd.DataFrame(all_rows)
    print(f"\nTotal cycles: {len(df):,}")
    if df.empty:
        return

    df.to_parquet(OUT_DIR / "mp_phase2g_roll_to_save.parquet", index=False)
    print(f"wrote: data/profile/mp_phase2g_roll_to_save.parquet\n")

    print("═══ Baseline (no roll, hold-to-expiry) ═══")
    summarize(df, "pnl_baseline", "baseline")
    print()

    print("═══ Trigger incidence (any-day + T-2-filtered) ═══")
    spot_fires = df["t_breach_spot"].notna().sum()
    mtm_fires = df["t_breach_mtm"].notna().sum()
    spotT2_fires = ((df["t_breach_spot"].notna()) & (df["dte_at_spot_breach"] >= 2)).sum()
    print(f"  spot_breach fires: {spot_fires}/{len(df)} ({spot_fires/len(df):.1%})")
    print(f"  mtm_1x fires:      {mtm_fires}/{len(df)} ({mtm_fires/len(df):.1%})")
    print(f"  spot_T2 fires:     {spotT2_fires}/{len(df)} ({spotT2_fires/len(df):.1%})")
    print()

    # On trigger-only subset (to isolate the "did rolling help?" question)
    for trig_label, mask_col in [
        ("T1_spot", "t_breach_spot"),
        ("T2_mtm", "t_breach_mtm"),
        ("T3_spotT2", "t_breach_spot"),
    ]:
        print(f"═══ {trig_label} — cycles where trigger fired ═══")
        if trig_label == "T3_spotT2":
            mask = (df[mask_col].notna()) & (df["dte_at_spot_breach"] >= 2)
        else:
            mask = df[mask_col].notna()
        trig_df = df[mask]
        if trig_df.empty:
            print("  (no cycles)")
            continue
        # Accept-loss reference (no-roll): use pnl_at_trigger as realized
        accept_col = "pnl_at_spot_breach" if "spot" in trig_label else "pnl_at_mtm_breach"
        print(
            f"  triggered cycles N={len(trig_df)}  baseline (held to expiry) mean ${trig_df['pnl_baseline'].mean():+.4f}"
        )
        summarize(trig_df, accept_col, "accept-loss at trigger (no roll)")
        for style in ["straight", "down_and_out"]:
            summarize(trig_df, f"pnl_{trig_label}_{style}", f"roll {style}")
            summarize(trig_df, f"pnl_{trig_label}_{style}_sig", f"roll {style} + signal gate")
        # Roll depth / fire rate inside the triggered subset
        for style in ["straight", "down_and_out"]:
            rolled = trig_df[f"rolled_{trig_label}_{style}"].sum()
            rolled_sig = trig_df[f"rolled_{trig_label}_{style}_sig"].sum()
            print(
                f"  rolled: {style}={rolled}/{len(trig_df)} ({rolled/len(trig_df):.0%})  "
                f"{style}+sig={rolled_sig}/{len(trig_df)} ({rolled_sig/len(trig_df):.0%})"
            )
        print()

    # Full-cohort comparison (baseline vs roll on EVERY cycle using the per-row outcome)
    print("═══ FULL-COHORT OUTCOME COMPARISON ═══")
    print(
        "  (pnl_<T>_<style> = baseline pnl on non-triggered cycles, combined pnl on triggered cycles)"
    )
    summarize(df, "pnl_baseline", "baseline")
    for trig_label in ["T1_spot", "T2_mtm", "T3_spotT2"]:
        for style in ["straight", "down_and_out"]:
            summarize(df, f"pnl_{trig_label}_{style}", f"{trig_label} × {style}")
            summarize(df, f"pnl_{trig_label}_{style}_sig", f"{trig_label} × {style} + signal")
    print()


if __name__ == "__main__":
    main()
