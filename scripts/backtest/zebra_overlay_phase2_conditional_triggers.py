"""ZEBRA + put overlay Phase 2 — C1/C2/C3/C4 conditional triggers.

The phase 1 V3 default attaches a 10%-OTM put at every ZEBRA entry. Conditional
triggers only attach the put when a regime / drawdown signal fires. The
operational benefit (if it works): lower average premium spend per cycle
because cycles in benign regimes skip the overlay entirely.

Variants:
  C1 : add 10%-OTM put on the first day after ZEBRA entry where spot <=
       0.95 * spot_entry  (drawdown trigger). Strike is 10% OTM from the
       NEW spot on that day (not 10% OTM from the original entry spot).
  C2 : add 10%-OTM put AT ZEBRA ENTRY iff SPY term_spread < 0
       (term-structure inversion)
  C3 : add 10%-OTM put AT ZEBRA ENTRY iff breadth divergence fires
       (spx_pct_to_50dma > 7 AND pct_above_50dma < 55)
  C4 : add 10%-OTM put AT ZEBRA ENTRY iff DGS30 >= 5.0% (long-end
       repricing regime — first since 2007). DGS30 history sourced from
       Agent_Project ChromaDB fred_historical_data collection.
       Added 2026-05-17 after 30Y crossed 5% on 2026-05-14.

Baselines for comparison:
  BARE  : ZEBRA only (no overlay)
  HOLD  : ZEBRA + V3 attached every cycle (Phase 1 default, always-on)

Decision rule: a C-variant promotes over HOLD if mean lift >= +$5/cyc
AND 4/4 walk-forward splits positive. The interesting alternative is
beat-BARE-but-also-cheaper-than-HOLD — if a variant ties HOLD on mean
but with substantially lower premium spend, that's an efficiency win.

Output: data/profile/zebra_put_overlay_phase2_conditional.parquet
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import config as C
from opex_calendar import monthly_opex_dates, nearest_trading_day_on_or_before
from structures import open_zebra, intrinsic_value_at_expiry
from legs import price_long_put

ROOT = Path("/Users/josephmorris/MaxPain_Project")
BY_TICKER = ROOT / "data/orats/by_ticker"
RESULTS_OUT = ROOT / "data/profile/zebra_put_overlay_phase2_conditional.parquet"

TERM_PATH = ROOT / "data/profile/signal_vrp_termstruct_spy.parquet"
BREADTH_PATH = ROOT / "data/profile/breadth_spx500_v2.parquet"

ENTRY_DTE = 75
SLIP = 0.25
TIER1 = ["SPY", "QQQ", "MSFT", "NVDA", "GOOGL", "META", "AMZN"]

PUT_PCT_BELOW = 0.10
C1_DRAWDOWN = 0.05           # 5% drop from ZEBRA entry spot
C3_SPX_50DMA_THRESHOLD = 7.0  # SPX > 7% above its 50dma
C3_BREADTH_THRESHOLD = 55.0   # but breadth < 55%
C4_DGS30_THRESHOLD = 5.0      # 30Y yield ≥ 5% (long-end repricing regime)


def _parse_exp(s):
    try:
        m, d, y = s.split("/")
        return pd.Timestamp(year=int(y), month=int(m), day=int(d))
    except Exception:
        return None


def _load_dgs30_map():
    """Read DGS30 daily series from Agent_Project ChromaDB and return
    {date: yield_pct}. Empty dict if Agent_Project unavailable — caller
    treats missing dates as "no C4 fire", matching how C2/C3 handle gaps.
    """
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path.home() / "Agent_Project"))
        from shared.chromadb_client import DataPipelineChromaDB
    except Exception:
        return {}
    db = DataPipelineChromaDB()
    res = db.query_by_metadata("fred_historical_data", {"series_id": "DGS30"})
    if not res:
        return {}
    out = {}
    for md in res["metadatas"]:
        d = md.get("data_date")
        v = md.get("value")
        if d and v is not None:
            try:
                out[pd.to_datetime(d).date()] = float(v)
            except Exception:
                continue
    return out


def load_signals():
    term = pd.read_parquet(TERM_PATH)
    term["trade_date"] = pd.to_datetime(term["trade_date"]).dt.date
    term_map = term.set_index("trade_date")["term_spread"].to_dict()

    breadth = pd.read_parquet(BREADTH_PATH)
    breadth["date"] = pd.to_datetime(breadth["date"]).dt.date
    breadth_map = breadth.set_index("date")[
        ["pct_above_50dma", "spx_pct_to_50dma"]
    ].to_dict("index")

    dgs30_map = _load_dgs30_map()
    return term_map, breadth_map, dgs30_map


def open_long_put_at_pct(chain, spot, signed_pct):
    target_strike = spot * (1.0 - signed_pct)
    cand = chain.dropna(subset=["pBidPx", "pAskPx", "pMidIv"]).copy()
    if cand.empty:
        return None, None
    cand = cand[cand["pMidIv"] >= C.MIN_IV_FOR_PRICING]
    if cand.empty:
        return None, None
    idx = (cand["strike"] - target_strike).abs().idxmin()
    row = cand.loc[idx]
    K = float(row["strike"])
    px = price_long_put(row)
    if px is None or px <= 0:
        return None, None
    return {"strike": K}, float(px)


def intrinsic_put(K, S_exp):
    return max(0.0, K - S_exp)


def simulate_cycle(slice_by_day, available_days, entry_date, expiration,
                   ticker, term_map, breadth_map, dgs30_map):
    entry_chain = slice_by_day.get(entry_date)
    if entry_chain is None or entry_chain.empty:
        return None
    zpos = open_zebra(entry_chain, pd.Timestamp(entry_date), expiration)
    if zpos is None:
        return None

    spot_entry = zpos.underlying_entry

    # Always open V3 (HOLD baseline) at entry
    hold_put, hold_debit = open_long_put_at_pct(entry_chain, spot_entry, PUT_PCT_BELOW)
    if hold_put is None:
        return None

    # C2: term inversion at entry
    term_spread_entry = term_map.get(entry_date)
    c2_fire = term_spread_entry is not None and term_spread_entry < 0

    # C3: breadth divergence at entry
    breadth_row = breadth_map.get(entry_date)
    c3_fire = (breadth_row is not None
               and pd.notna(breadth_row.get("spx_pct_to_50dma"))
               and pd.notna(breadth_row.get("pct_above_50dma"))
               and breadth_row["spx_pct_to_50dma"] > C3_SPX_50DMA_THRESHOLD
               and breadth_row["pct_above_50dma"] < C3_BREADTH_THRESHOLD)

    # C4: 30Y yield ≥ 5% at entry
    dgs30_entry = dgs30_map.get(entry_date)
    c4_fire = dgs30_entry is not None and dgs30_entry >= C4_DGS30_THRESHOLD

    # If C2 / C3 / C4 fire, attach an at-entry V3 put (same as HOLD)
    c2_debit = hold_debit if c2_fire else 0.0
    c3_debit = hold_debit if c3_fire else 0.0
    c4_debit = hold_debit if c4_fire else 0.0
    c2_strike = hold_put["strike"]
    c3_strike = hold_put["strike"]
    c4_strike = hold_put["strike"]

    # C1: walk forward to find first day where spot <= 0.95 * spot_entry
    # If trigger fires, attach a 10%-OTM put on THAT day's chain at the new spot.
    forward_days = [d for d in available_days
                    if d > entry_date and d <= expiration.date()]

    c1_fire = False
    c1_strike = None
    c1_debit = 0.0
    c1_trigger_date = None
    threshold_spot = spot_entry * (1.0 - C1_DRAWDOWN)
    for d in forward_days:
        chain_d = slice_by_day.get(d)
        if chain_d is None or chain_d.empty:
            continue
        spot_d = float(chain_d["stkPx"].iloc[0])
        if spot_d <= threshold_spot:
            put_pos, debit = open_long_put_at_pct(chain_d, spot_d, PUT_PCT_BELOW)
            if put_pos is not None:
                c1_fire = True
                c1_strike = put_pos["strike"]
                c1_debit = debit
                c1_trigger_date = d
                break

    # Settle ZEBRA at expiry; settle each put on intrinsic if attached
    last_chain = slice_by_day.get(expiration.date())
    if last_chain is None or last_chain.empty:
        last_d = forward_days[-1] if forward_days else None
        if last_d is None:
            return None
        last_chain = slice_by_day.get(last_d)
        if last_chain is None or last_chain.empty:
            return None
    S_exp = float(last_chain["stkPx"].iloc[0])
    pnl_zebra = float(zpos.entry_credit + intrinsic_value_at_expiry(zpos, S_exp))

    pnl_hold = intrinsic_put(hold_put["strike"], S_exp) - hold_debit
    pnl_c1 = (intrinsic_put(c1_strike, S_exp) - c1_debit) if c1_fire else 0.0
    pnl_c2 = (intrinsic_put(c2_strike, S_exp) - c2_debit) if c2_fire else 0.0
    pnl_c3 = (intrinsic_put(c3_strike, S_exp) - c3_debit) if c3_fire else 0.0
    pnl_c4 = (intrinsic_put(c4_strike, S_exp) - c4_debit) if c4_fire else 0.0

    return {
        "ticker": ticker,
        "expiration": expiration,
        "entry_date": pd.Timestamp(entry_date),
        "spot_entry": spot_entry,
        "spot_exit": S_exp,
        "return_pct": (S_exp / spot_entry - 1.0) * 100,
        "pnl_zebra": pnl_zebra,

        "pnl_put_hold": float(pnl_hold),
        "pnl_combined_hold": float(pnl_zebra + pnl_hold),
        "hold_debit": float(hold_debit),

        "c1_fired": c1_fire,
        "c1_trigger_date": pd.Timestamp(c1_trigger_date) if c1_trigger_date else pd.NaT,
        "c1_debit": float(c1_debit),
        "pnl_put_c1": float(pnl_c1),
        "pnl_combined_c1": float(pnl_zebra + pnl_c1),

        "c2_fired": c2_fire,
        "c2_debit": float(c2_debit),
        "pnl_put_c2": float(pnl_c2),
        "pnl_combined_c2": float(pnl_zebra + pnl_c2),

        "c3_fired": c3_fire,
        "c3_debit": float(c3_debit),
        "pnl_put_c3": float(pnl_c3),
        "pnl_combined_c3": float(pnl_zebra + pnl_c3),

        "c4_fired": c4_fire,
        "c4_debit": float(c4_debit),
        "pnl_put_c4": float(pnl_c4),
        "pnl_combined_c4": float(pnl_zebra + pnl_c4),
        "dgs30_entry": float(dgs30_entry) if dgs30_entry is not None else float("nan"),
    }


def simulate_ticker(ticker, term_map, breadth_map, dgs30_map):
    path = BY_TICKER / f"{ticker}.parquet"
    if not path.exists():
        return []
    tdf = pd.read_parquet(path)
    if tdf.empty:
        return []
    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"])
    tdf["date_only"] = tdf["trade_date"].dt.date
    first_date = tdf["trade_date"].min().date()
    last_date = tdf["trade_date"].max().date()

    exp_str_to_date = {}
    for s in tdf["expirDate"].unique():
        ts = _parse_exp(s)
        if ts is not None:
            exp_str_to_date[s] = ts

    opex_eligible = [d for d in monthly_opex_dates(first_date.year, last_date.year + 1)
                     if first_date <= d <= last_date]
    opex_to_exp = {}
    for opex in opex_eligible:
        ts = pd.Timestamp(opex)
        for s, d in exp_str_to_date.items():
            if abs((d - ts).days) <= 1:
                opex_to_exp[ts] = s
                break

    exp_groups = {s: sub for s, sub in tdf.groupby("expirDate", sort=False)}
    summaries = []

    C.activate_slip(SLIP)
    for opex_ts, exp_str in opex_to_exp.items():
        exp_df = exp_groups[exp_str]
        slice_by_day = {d: sub for d, sub in exp_df.groupby("date_only", sort=False)}
        available_days = sorted(slice_by_day.keys())

        target = (opex_ts - pd.Timedelta(days=ENTRY_DTE)).date()
        entry_date = nearest_trading_day_on_or_before(target, available_days)
        if entry_date is None:
            continue
        s = simulate_cycle(slice_by_day, available_days, entry_date, opex_ts,
                          ticker, term_map, breadth_map, dgs30_map)
        if s is not None:
            summaries.append(s)
    return summaries


def report(df):
    n = len(df)
    print(f"\n=== Phase 2 conditional triggers (V3 10%-OTM put, slip={SLIP}) ===")
    print(f"cycles: {n}\n")

    base_bare = df["pnl_zebra"].mean()
    base_hold = df["pnl_combined_hold"].mean()
    print(f"  BARE (ZEBRA only):                    mean=${base_bare:+.2f}")
    print(f"  HOLD (always-on V3 put):              mean=${base_hold:+.2f}  avg_cost=${df['hold_debit'].mean():.2f}")

    variants = [
        ("C1 drawdown -5%",     "pnl_combined_c1",  "c1_fired",  "c1_debit"),
        ("C2 term inversion",   "pnl_combined_c2",  "c2_fired",  "c2_debit"),
        ("C3 breadth diverge",  "pnl_combined_c3",  "c3_fired",  "c3_debit"),
        ("C4 DGS30 ≥ 5%",       "pnl_combined_c4",  "c4_fired",  "c4_debit"),
    ]
    print()
    for label, col, fircol, costcol in variants:
        m = df[col].mean()
        w = (df[col] > 0).mean()
        mn = df[col].min()
        sd = df[col].std()
        fire = df[fircol].mean()
        cost = df[costcol].mean()
        lift_hold = m - base_hold
        lift_bare = m - base_bare
        print(f"  {label:22s} mean=${m:+.2f}  win={w:.1%}  worst=${mn:+.2f}  std=${sd:.2f}  fire={fire:.1%}  avg_cost=${cost:.2f}  vs_HOLD=${lift_hold:+.2f}  vs_BARE=${lift_bare:+.2f}")

    print("\n=== Walk-forward (lift vs HOLD per split × variant) ===")
    df = df.copy()
    df["val_year"] = pd.to_datetime(df["expiration"]).dt.year
    splits = [
        ("2021-2023", range(2021, 2024)),
        ("2022-2024", range(2022, 2025)),
        ("2023-2025", range(2023, 2026)),
        ("2024-2026", range(2024, 2027)),
    ]
    cols = ["pnl_combined_c1", "pnl_combined_c2", "pnl_combined_c3", "pnl_combined_c4"]
    headers = ["C1", "C2", "C3", "C4"]
    print("  split        " + "  ".join(f"{h:>7s}" for h in headers))
    pos_count = {c: 0 for c in cols}
    for slabel, yrs in splits:
        m = df[df["val_year"].isin(list(yrs))]
        if m.empty:
            continue
        hbase = m["pnl_combined_hold"].mean()
        parts = []
        for col in cols:
            lift = m[col].mean() - hbase
            if lift > 0:
                pos_count[col] += 1
            parts.append(f"{lift:+7.2f}")
        print(f"  {slabel}: " + "  ".join(parts))
    print("\n  Positive splits / 4 (lift vs HOLD):")
    for col, h in zip(cols, headers):
        print(f"    {h:3s}  {pos_count[col]}/4")

    # Conditional-on-fire performance (the meaningful slice)
    print("\n=== Conditional-on-fire detail (put-only P/L on cycles where trigger fired) ===")
    for label, col, fircol, costcol in variants:
        sub = df[df[fircol]]
        if sub.empty:
            print(f"  {label}: NEVER FIRED (no cycles)")
            continue
        put_only_col = col.replace("pnl_combined", "pnl_put")
        m_put = sub[put_only_col].mean()
        m_combined = sub[col].mean()
        m_combined_hold_same = sub["pnl_combined_hold"].mean()
        n_fire = len(sub)
        print(f"  {label:22s}  N={n_fire}  put-only mean=${m_put:+.2f}  combined mean=${m_combined:+.2f}  HOLD on same cycles=${m_combined_hold_same:+.2f}  diff_vs_HOLD=${m_combined - m_combined_hold_same:+.2f}")


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("zebra_p2_cond")
    log.info("Phase 2 conditional triggers on tier-1: %s", TIER1)
    term_map, breadth_map, dgs30_map = load_signals()
    log.info("Loaded %d term-spread + %d breadth + %d dgs30 observations",
             len(term_map), len(breadth_map), len(dgs30_map))
    if not dgs30_map:
        log.warning("DGS30 map empty — C4 will record zero fires. Check Agent_Project ChromaDB.")

    all_results = []
    for i, t in enumerate(TIER1, 1):
        s = simulate_ticker(t, term_map, breadth_map, dgs30_map)
        all_results.extend(s)
        log.info("  [%d/%d] %s: %d cycles", i, len(TIER1), t, len(s))

    if not all_results:
        log.error("No cycles produced")
        return

    df = pd.DataFrame(all_results)
    RESULTS_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(RESULTS_OUT, index=False)
    log.info("Wrote %d cycles to %s", len(df), RESULTS_OUT)
    report(df)


if __name__ == "__main__":
    main()
