"""MP directional rule — final test before retiring MaxPain as a tradeable concept.

Pre-registered methodology (locked 2026-05-04, see chat transcript / memory):

Direction rule at T-5 trading days before monthly OpEx:
  - spot  <  MP  →  bull_put
  - spot  >  MP  →  bear_call
  - |spot - MP| / spot < 0.005  →  skip (deadband)

Two strike anchors per side (test as separate tracks, no rolling):
  - 30Δ  — short leg at the strike whose delta is closest to ±0.30 (always OTM)
  - MP   — short leg at the strike nearest MP. NOTE: under the directional rule
           this places the short ITM (mp_bull_put has MP > spot, mp_bear_call
           has MP < spot). That is a deliberate, mechanically valid ITM credit
           spread — flagged in output for context.

Cohort: union of COHORT_BULL_PUT + COHORT_BEAR_CALL from gate_config (26 names),
        i.e. the *currently-active* live cohort. Compare to the prior 19-name
        pin cohort used in Phase 2c–2g.

Slip = 0.25 (retail). Hold to expiry. Equal sizing (1 contract per cycle).
Output: full-cohort headline, per-ticker table, skip-zone rate, regime split.

Why we're running this: Phase 2c-2g tested MP as a strike anchor (Phase 2c-2f)
and as a roll-rescue subject (Phase 2g). bull_put_mp was tabled 2026-05-03 on
three falsifications (pin not causal, 0.50 floor unreachable, lift = signal-gate
not MP). This is the directional-selection variant — does picking SIDE by spot
vs MP add edge above always-trade-30Δ? If no, MP retires fully.
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd

ROOT = Path("/Users/josephmorris/MaxPain_Project")
OUT_DIR = ROOT / "data/profile"
sys.path.insert(0, str(ROOT))
from scripts.qualifier import gate_config as G  # noqa: E402

COHORT = sorted(set(G.COHORT_BULL_PUT) | set(G.COHORT_BEAR_CALL))

SLIP_FRAC = 0.25
SKIP_DEADBAND = 0.005   # |spot-MP|/spot < this → skip cycle


# ── OpEx + parsing helpers (same as Phase 2c) ──────────────────────────────

def third_friday(year, month):
    d = date(year, month, 1)
    offset = (4 - d.weekday()) % 7
    return d + timedelta(days=offset + 14)


def monthly_opex(sy, ey):
    return [third_friday(y, m) for y in range(sy, ey + 1) for m in range(1, 13)]


def parse_exp(s):
    try:
        p = s.split("/")
        return pd.Timestamp(year=int(p[2]), month=int(p[0]), day=int(p[1]))
    except Exception:
        return None


# ── MP + strike helpers ─────────────────────────────────────────────────────

def compute_max_pain(chain):
    c = chain.dropna(subset=["strike", "cOi", "pOi"])
    if c.empty:
        return None
    strikes = c["strike"].values
    call_oi = c["cOi"].values
    put_oi = c["pOi"].values
    best_K, best_pain = None, None
    for K in strikes:
        total = (call_oi * np.maximum(0.0, K - strikes)).sum() + \
                (put_oi  * np.maximum(0.0, strikes - K)).sum()
        if best_pain is None or total < best_pain:
            best_pain = total
            best_K = float(K)
    return best_K


def nth_strike_from(chain, reference, n):
    strikes = sorted(chain["strike"].dropna().unique())
    if not strikes:
        return None
    arr = np.array(strikes)
    idx = int(np.argmin(np.abs(arr - reference)))
    target_idx = idx + n
    if 0 <= target_idx < len(strikes):
        return float(strikes[target_idx])
    return None


def get_row(chain, K):
    rows = chain[chain["strike"] == K]
    if rows.empty:
        return None
    return rows.iloc[0]


def price_sell(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 - SLIP_FRAC * (ask - bid) / 2


def price_buy(bid, ask):
    if pd.isna(bid) or pd.isna(ask) or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return (bid + ask) / 2 + SLIP_FRAC * (ask - bid) / 2


def select_short_put_30d(chain):
    c = chain.dropna(subset=["delta", "strike", "pBidPx", "pAskPx"])
    if c.empty:
        return None
    idx = (c["delta"] - 0.70).abs().idxmin()
    row = c.loc[idx]
    if abs(row["delta"] - 0.70) > 0.08:
        return None
    return row


def select_short_call_30d(chain):
    c = chain.dropna(subset=["delta", "strike", "cBidPx", "cAskPx"])
    if c.empty:
        return None
    idx = (c["delta"] - 0.30).abs().idxmin()
    row = c.loc[idx]
    if abs(row["delta"] - 0.30) > 0.08:
        return None
    return row


# ── Spread builders ─────────────────────────────────────────────────────────

def build_bull_put(chain, short_put_K):
    sp_row = get_row(chain, short_put_K)
    long_K = nth_strike_from(chain, short_put_K, -1)
    if long_K is None:
        return None
    lp_row = get_row(chain, long_K)
    if sp_row is None or lp_row is None:
        return None
    sp = price_sell(sp_row["pBidPx"], sp_row["pAskPx"])
    lp = price_buy(lp_row["pBidPx"], lp_row["pAskPx"])
    if sp is None or lp is None:
        return None
    credit = sp - lp
    if credit <= 0:
        return None
    return {
        "entry_credit": credit,
        "short_K": short_put_K, "long_K": long_K,
        "wing_width": short_put_K - long_K,
        "legs": [("short", "put", short_put_K), ("long", "put", long_K)],
    }


def build_bear_call(chain, short_call_K):
    sc_row = get_row(chain, short_call_K)
    long_K = nth_strike_from(chain, short_call_K, +1)
    if long_K is None:
        return None
    lc_row = get_row(chain, long_K)
    if sc_row is None or lc_row is None:
        return None
    sc = price_sell(sc_row["cBidPx"], sc_row["cAskPx"])
    lc = price_buy(lc_row["cBidPx"], lc_row["cAskPx"])
    if sc is None or lc is None:
        return None
    credit = sc - lc
    if credit <= 0:
        return None
    return {
        "entry_credit": credit,
        "short_K": short_call_K, "long_K": long_K,
        "wing_width": long_K - short_call_K,
        "legs": [("short", "call", short_call_K), ("long", "call", long_K)],
    }


def intrinsic_leg(side, opt_type, strike, close):
    v = max(0.0, close - strike) if opt_type == "call" else max(0.0, strike - close)
    return v if side == "long" else -v


def settle_pnl(structure, close):
    intrinsic = sum(intrinsic_leg(s, t, k, close) for s, t, k in structure["legs"])
    return structure["entry_credit"] + intrinsic


# ── Per-ticker walker ───────────────────────────────────────────────────────

def run_ticker(ticker, opex_list):
    path = ROOT / f"data/orats/by_ticker/{ticker}.parquet"
    if not path.exists():
        return []
    df = pd.read_parquet(
        path, columns=["trade_date", "expirDate", "strike", "stkPx", "delta",
                       "cOi", "pOi", "cBidPx", "cAskPx", "pBidPx", "pAskPx"]
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    exp_map = {}
    for s in df["expirDate"].unique():
        d = parse_exp(s)
        if d is None:
            continue
        for opex in opex_list:
            if abs((d - opex).days) <= 1:
                exp_map[opex] = s
                break

    results = []
    for opex, exp_str in exp_map.items():
        sub = df[df["expirDate"] == exp_str]
        if sub.empty:
            continue
        target = opex - pd.Timedelta(days=5)
        pre = sub[sub["trade_date"] <= target]
        if pre.empty:
            continue
        t_entry = pre["trade_date"].max()
        chain = pre[pre["trade_date"] == t_entry].copy()
        if chain.empty:
            continue
        mp = compute_max_pain(chain)
        if mp is None:
            continue
        spot = float(chain["stkPx"].iloc[0])
        if spot <= 0:
            continue

        # Settlement spot at OpEx
        final = df[df["trade_date"] == opex]
        if final.empty:
            continue
        close = float(final["stkPx"].iloc[0])

        # ── Direction rule ──
        spread_pct = (spot - mp) / spot
        if abs(spread_pct) < SKIP_DEADBAND:
            direction = "SKIP"
        elif spot < mp:
            direction = "BP"   # bull_put: expect price to revert UP toward MP
        else:
            direction = "BC"   # bear_call: expect price to revert DOWN toward MP

        base = {
            "ticker": ticker, "opex": opex, "t_entry": t_entry,
            "spot_entry": spot, "spot_close": close,
            "mp_k": mp, "spot_minus_mp_pct": spread_pct,
            "direction": direction,
        }

        if direction == "SKIP":
            results.append({**base, "structure": "SKIP", "entry_credit": None,
                            "short_K": None, "long_K": None, "wing_width": None,
                            "pnl": None, "is_itm_at_entry": None})
            continue

        if direction == "BP":
            # bull_put_30d (always OTM)
            sp30 = select_short_put_30d(chain)
            if sp30 is not None:
                bp = build_bull_put(chain, float(sp30["strike"]))
                if bp is not None:
                    results.append({
                        **base, "structure": "bull_put_30d",
                        **{k: bp[k] for k in ("entry_credit", "short_K", "long_K", "wing_width")},
                        "pnl": settle_pnl(bp, close),
                        "is_itm_at_entry": bp["short_K"] > spot,
                    })
            # bull_put_mp (short at MP — ITM under directional rule, since MP > spot)
            mp_K = nth_strike_from(chain, mp, 0)
            if mp_K is not None:
                bpm = build_bull_put(chain, mp_K)
                if bpm is not None:
                    results.append({
                        **base, "structure": "bull_put_mp",
                        **{k: bpm[k] for k in ("entry_credit", "short_K", "long_K", "wing_width")},
                        "pnl": settle_pnl(bpm, close),
                        "is_itm_at_entry": bpm["short_K"] > spot,
                    })

        else:  # direction == "BC"
            sc30 = select_short_call_30d(chain)
            if sc30 is not None:
                bc = build_bear_call(chain, float(sc30["strike"]))
                if bc is not None:
                    results.append({
                        **base, "structure": "bear_call_30d",
                        **{k: bc[k] for k in ("entry_credit", "short_K", "long_K", "wing_width")},
                        "pnl": settle_pnl(bc, close),
                        "is_itm_at_entry": bc["short_K"] < spot,
                    })
            mp_K = nth_strike_from(chain, mp, 0)
            if mp_K is not None:
                bcm = build_bear_call(chain, mp_K)
                if bcm is not None:
                    results.append({
                        **base, "structure": "bear_call_mp",
                        **{k: bcm[k] for k in ("entry_credit", "short_K", "long_K", "wing_width")},
                        "pnl": settle_pnl(bcm, close),
                        "is_itm_at_entry": bcm["short_K"] < spot,
                    })

    return results


# ── Regime split helper ─────────────────────────────────────────────────────

def regime_label(opex: pd.Timestamp) -> str:
    """Coarse regime by year — same buckets as project_regime_window_findings."""
    y = opex.year
    if y in (2020, 2022):
        return "bear"
    if y in (2018, 2025):
        return "sideways"
    return "bull"


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    opex_list = [pd.Timestamp(d) for d in monthly_opex(2013, 2026)]
    print(f"Cohort: {len(COHORT)} tickers (union of COHORT_BULL_PUT + COHORT_BEAR_CALL)")

    all_rows: list[dict] = []
    for i, t in enumerate(COHORT, 1):
        rows = run_ticker(t, opex_list)
        all_rows.extend(rows)
        print(f"  [{i}/{len(COHORT)}] {t}: {len(rows)} rows")

    df = pd.DataFrame(all_rows)
    print(f"\nTotal rows: {len(df):,}")
    if df.empty:
        return

    # Skip-zone fire rate
    n_total_cycles = (df.groupby(["ticker", "opex"]).size().shape[0])
    n_skip = (df["direction"] == "SKIP").sum()
    print(f"\nDirection distribution (one count per ticker-opex pair):")
    direction_per_cycle = df.groupby(["ticker", "opex"])["direction"].first()
    print(direction_per_cycle.value_counts().to_string())
    print(f"  skip rate: {n_skip / n_total_cycles * 100:.1f}% of cycles fall in deadband")

    # Drop SKIPs from P&L analysis
    pnl = df[df["structure"] != "SKIP"].copy()
    pnl["regime"] = pnl["opex"].map(regime_label)

    print("\n═══ Headline by structure (full cohort, all regimes) ═══")
    head = pnl.groupby("structure").agg(
        n=("pnl", "count"),
        mean=("pnl", "mean"),
        median=("pnl", "median"),
        win=("pnl", lambda s: (s > 0).mean()),
        worst=("pnl", "min"),
        best=("pnl", "max"),
        total=("pnl", "sum"),
        itm_pct=("is_itm_at_entry", "mean"),
    ).round(4)
    print(head.to_string())

    print("\n═══ By regime × structure ═══")
    by_reg = pnl.groupby(["regime", "structure"]).agg(
        n=("pnl", "count"),
        mean=("pnl", "mean"),
        win=("pnl", lambda s: (s > 0).mean()),
        total=("pnl", "sum"),
    ).round(4)
    print(by_reg.to_string())

    print("\n═══ Per-ticker × structure (mean P&L) ═══")
    pv = pnl.pivot_table(index="ticker", columns="structure", values="pnl",
                          aggfunc="mean").round(3)
    print(pv.to_string())

    print("\n═══ Per-ticker × structure (N cycles) ═══")
    pvn = pnl.pivot_table(index="ticker", columns="structure", values="pnl",
                           aggfunc="count")
    print(pvn.to_string())

    # Control comparison: directional-rule track vs always-trade equivalents
    # The "30d" tracks under directional rule already only fire one side per
    # cycle. To compare against always-trade-bull_put_30d we'd need a separate
    # unconditional run — log a note for the next step.
    print("\nNote: this run only includes the side selected by the directional"
          " rule. To compare 'directional rule + 30Δ' vs 'always-trade-30Δ', a"
          " companion run with no direction filter is needed.")

    # Persist
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "mp_directional_rule.parquet"
    df.to_parquet(out_path, index=False)
    print(f"\nwrote: {out_path}")


if __name__ == "__main__":
    main()
