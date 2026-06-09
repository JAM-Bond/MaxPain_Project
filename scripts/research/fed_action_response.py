#!/usr/bin/env python3.11
"""Fed-action response study — per scope (SPY/QQQ/sector ETFs/cohort), forward
returns conditioned on hold/cut/hike AND expected-vs-surprise.

Pre-registered: docs/FED_ACTION_RESPONSE_PREREG.md (SEALED 2026-06-09). DESCRIPTIVE
context annotation, NOT a gate. Builds on scripts/macro/fed_action_spy_study.py.

Sources (frozen): bond_agent.db:fomc_decisions (cut/hike) + config/fomc_calendar.csv
(holds = scheduled meetings minus changes); FedWatch historical implied probability
(/forecasts?meetingDt=&reportingDt=, prior business day) for the surprise cut; ORATS
price panel data/macro/prices_daily_13y.parquet (+ adjusted_close fallback).

Usage:
  python3.11 -m scripts.research.fed_action_response                 # default scopes
  python3.11 -m scripts.research.fed_action_response --scopes SPY QQQ
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path.home() / "Agent_Project" / "CME_FedWatch"))

BOND_DB = Path.home() / "Agent_Project" / "data" / "bond_agent.db"
PANEL = ROOT / "data/macro/prices_daily_13y.parquet"
CAL = ROOT / "config/fomc_calendar.csv"
OUT = ROOT / "data/profile/fed_action_response.parquet"

HORIZONS = [1, 5, 25]
EXPECTED_THRESH = 0.65          # P_implied(realized action) >= this -> EXPECTED
SURPRISE_THRESH = 0.35          # <= this -> SURPRISE; between -> AMBIGUOUS
SURFACE_FLOOR = 8               # cells below this N are stored but not surfaced
H2_RATIO_GATE = 1.5             # surprise |move| must be >= this x expected (H2)

import time                     # noqa: E402

import api_ingester as fw       # noqa: E402  (Agent_Project FedWatch reader)
_ING = fw.FedWatchAPIIngester()
_FW_CACHE: dict = {}
FW_CACHE_PATH = ROOT / "data/profile/fed_action_fedwatch_cache.parquet"


def _payload_retry(path: str, params: dict | None = None, tries: int = 4):
    """_ING._get + _payload with retry — the CME endpoint occasionally hangs
    (20s read timeout); one timeout must not abort a ~300-call enrichment run."""
    for attempt in range(tries):
        try:
            return _ING._payload(_ING._get(path, params=params))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return []


# ── adequacy ────────────────────────────────────────────────────────────────
def adequacy(n: int) -> str:
    return ("PRELIMINARY" if n < 10 else "SUGGESTIVE" if n < 20
            else "DEVELOPING" if n < 30 else "ADEQUATE")


# ── prices ──────────────────────────────────────────────────────────────────
def load_panel() -> pd.DataFrame:
    p = pd.read_parquet(PANEL)[["date", "ticker", "close"]].copy()
    p["date"] = pd.to_datetime(p["date"])
    return p


def price_series(panel: pd.DataFrame, ticker: str):
    s = (panel[panel["ticker"] == ticker][["date", "close"]].dropna()
         .drop_duplicates("date").sort_values("date").set_index("date")["close"])
    if len(s) > 50:
        return s
    try:                                       # fallback for names not in the panel
        from lib.adjusted_close import load_adjusted_close
        a = load_adjusted_close(ticker)
        col = "adj_close" if "adj_close" in a.columns else "close"
        a = a.dropna(subset=[col]).copy()
        a.index = pd.to_datetime(a.index if a.index.name else a["date"])
        return a[col].sort_index()
    except Exception:
        return None


# ── FedWatch: resolve meeting date + implied probability ──────────────────────
def fw_meeting_date(decision_date: str):
    """FedWatch needs the exact announcement Wednesday; fomc_decisions stores it
    offset (usually +1). Probe nearby dates for a non-empty /forecasts."""
    base = pd.Timestamp(decision_date)
    for off in (0, -1, -2, -3, 1):
        d = (base + pd.Timedelta(days=int(off))).strftime("%Y-%m-%d")
        if d in _FW_CACHE.get("valid", set()):
            return d
        pl = _payload_retry("/forecasts", params={"meetingDt": d})
        if pl:
            _FW_CACHE.setdefault("valid", set()).add(d)
            return d
    return None


def fw_implied_prob(meeting_dt: str, prev_rate: float, action: str, lead_days: int):
    """Implied probability of the REALIZED action as of ~lead_days BEFORE the
    meeting. Measuring at T-1 is degenerate — FedWatch is ~always right the day
    before (the Fed telegraphs), so there are no surprises. A ~42-day lead (≈ the
    45-DTE entry horizon) captures genuine ex-ante uncertainty.

    Anchors 'current range' on the known prior rate (non-circular):
      hike = prob mass in buckets above current; cut = below; hold = the bucket.
    Returns (p_implied, reporting_dt) or (None, None)."""
    key = (meeting_dt, round(prev_rate, 4), action, lead_days)
    if key in _FW_CACHE:
        return _FW_CACHE[key]
    cur_upper = round(prev_rate * 100)         # e.g. 3.75% -> 375 bp == upperRt
    base = pd.Timestamp(meeting_dt)
    for off in (lead_days, lead_days + 1, lead_days + 2, lead_days + 3, lead_days - 1):
        rep = (base - pd.Timedelta(days=off)).strftime("%Y-%m-%d")
        pl = _payload_retry("/forecasts", params={"meetingDt": meeting_dt, "reportingDt": rep})
        if not pl:
            continue
        rr = [x for x in pl[0].get("rateRange", []) if x.get("probability") is not None]
        if not rr:
            continue
        p_hike = sum(x["probability"] for x in rr if int(x["upperRt"]) > cur_upper)
        p_cut = sum(x["probability"] for x in rr if int(x["upperRt"]) < cur_upper)
        p_hold = sum(x["probability"] for x in rr if int(x["upperRt"]) == cur_upper)
        p = {"hike": p_hike, "cut": p_cut, "hold": p_hold}.get(action)
        _FW_CACHE[key] = (p, rep)
        return p, rep
    _FW_CACHE[key] = (None, None)
    return None, None


def surprise_bucket(p_implied):
    if p_implied is None:
        return "unknown"
    if p_implied >= EXPECTED_THRESH:
        return "expected"
    if p_implied <= SURPRISE_THRESH:
        return "surprise"
    return "ambiguous"


# ── events: actions (cut/hike) + holds ────────────────────────────────────────
def load_events() -> pd.DataFrame:
    c = sqlite3.connect(BOND_DB)
    chg = pd.read_sql_query(
        "SELECT meeting_date, action, change_bps, previous_rate, new_rate "
        "FROM fomc_decisions ORDER BY meeting_date", c)
    c.close()
    chg["decision_date"] = pd.to_datetime(chg["meeting_date"])

    cal = pd.read_csv(CAL)
    cal["meeting_date"] = pd.to_datetime(cal["meeting_date"])

    # assert every change matches a calendar meeting within +/-3 days
    unmatched = []
    cal_dates = cal["meeting_date"].values
    for d in chg["decision_date"]:
        if np.min(np.abs((cal_dates - np.datetime64(d)) / np.timedelta64(1, "D"))) > 3:
            unmatched.append(str(d.date()))
    if unmatched:
        raise SystemExit(f"FOMC calendar missing changes within +/-3d: {unmatched}")

    rows = []
    # changes
    for _, r in chg.iterrows():
        rows.append({"decision_date": r["decision_date"], "action": r["action"],
                     "previous_rate": r["previous_rate"], "type": "change"})
    # holds = scheduled calendar meetings not within +/-3d of any change
    chg_dates = chg["decision_date"].values
    last_rate = None
    for _, r in cal.sort_values("meeting_date").iterrows():
        d = r["meeting_date"]
        is_change = np.min(np.abs((chg_dates - np.datetime64(d)) / np.timedelta64(1, "D"))) <= 3
        # track prevailing rate from the most recent change for hold anchoring
        same = chg[np.abs((chg["decision_date"] - d).dt.days) <= 3]
        if not same.empty:
            last_rate = float(same.iloc[0]["new_rate"])
        if is_change or r["type"] == "emergency":
            continue
        rows.append({"decision_date": d, "action": "hold",
                     "previous_rate": last_rate, "type": "hold"})

    ev = pd.DataFrame(rows).sort_values("decision_date").reset_index(drop=True)
    return ev


def enrich_fedwatch(ev: pd.DataFrame, lead_days: int) -> pd.DataFrame:
    """Resolve each event's FedWatch meeting date + implied prob (at `lead_days`
    before the meeting) + surprise bucket.

    Resumable: FedWatch historical readings are immutable, so results are cached to
    disk. The implied prob is lead-DEPENDENT (keyed by lead_days); the resolved
    meeting date is lead-INDEPENDENT and reused across leads. A transient timeout
    mid-run only loses the current event; re-run resumes."""
    cdf = pd.read_parquet(FW_CACHE_PATH) if FW_CACHE_PATH.exists() else pd.DataFrame(
        columns=["decision_date", "action", "lead_days", "fw_meeting_date", "p_implied"])
    if "lead_days" not in cdf.columns:          # migrate any pre-lead cache (prior-day run)
        cdf["lead_days"] = -1
    implied_cache = {(c["decision_date"], c["action"], int(c["lead_days"])):
                     (c["fw_meeting_date"], c["p_implied"]) for _, c in cdf.iterrows()}
    mdate_cache = {c["decision_date"]: c["fw_meeting_date"] for _, c in cdf.iterrows()
                   if not (isinstance(c["fw_meeting_date"], float) and pd.isna(c["fw_meeting_date"]))}

    mdates, pimp, sbucket = [], [], []
    new_rows = []
    for _, r in ev.iterrows():
        dkey = r["decision_date"].strftime("%Y-%m-%d")
        ckey = (dkey, r["action"], lead_days)
        if ckey in implied_cache:
            md, p = implied_cache[ckey]
            md = None if (isinstance(md, float) and pd.isna(md)) else md
            p = None if (p is None or pd.isna(p)) else p
        else:
            md = mdate_cache.get(dkey) or fw_meeting_date(dkey)
            p = None
            if md is not None and pd.notna(r["previous_rate"]):
                p, _ = fw_implied_prob(md, float(r["previous_rate"]), r["action"], lead_days)
            new_rows.append({"decision_date": dkey, "action": r["action"],
                             "lead_days": lead_days, "fw_meeting_date": md, "p_implied": p})
        mdates.append(md); pimp.append(p); sbucket.append(surprise_bucket(p))

    if new_rows:                                # persist
        merged = pd.concat([cdf, pd.DataFrame(new_rows)], ignore_index=True)
        merged = merged.drop_duplicates(["decision_date", "action", "lead_days"], keep="last")
        FW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(FW_CACHE_PATH, index=False)

    ev = ev.copy()
    ev["fw_meeting_date"] = mdates
    ev["p_implied"] = pimp
    ev["surprise"] = sbucket
    # anchor returns on the resolved announcement date when available, else decision_date
    ev["event_date"] = pd.to_datetime(
        [m if m else d.strftime("%Y-%m-%d")
         for m, d in zip(ev["fw_meeting_date"], ev["decision_date"])])
    ev["year"] = ev["event_date"].dt.year
    return ev


# ── forward returns ───────────────────────────────────────────────────────────
def forward_returns(event_dates: pd.Series, s: pd.Series) -> pd.DataFrame:
    """close-to-close return from the trading day at/after each event date to +h."""
    idx = s.index
    pos = idx.searchsorted(pd.DatetimeIndex(event_dates.values), side="left")
    out = {"event_date": event_dates.values}
    base = np.array([s.iloc[i] if 0 <= i < len(s) else np.nan for i in pos])
    for h in HORIZONS:
        fwd = np.array([s.iloc[i + h] if 0 <= i + h < len(s) else np.nan for i in pos])
        out[f"ret_{h}"] = (fwd / base - 1.0) * 100
    return pd.DataFrame(out)


def scope_returns(ev: pd.DataFrame, panel: pd.DataFrame, ticker: str,
                  spy_ret: pd.DataFrame | None):
    s = price_series(panel, ticker)
    if s is None:
        return None
    r = forward_returns(ev["event_date"], s)
    if spy_ret is not None:                    # market-adjust non-SPY
        for h in HORIZONS:
            r[f"ret_{h}"] = r[f"ret_{h}"] - spy_ret[f"ret_{h}"].values
    return r


# ── aggregation ───────────────────────────────────────────────────────────────
def aggregate(ev: pd.DataFrame, r: pd.DataFrame, scope: str) -> list[dict]:
    df = ev[["action", "surprise", "year"]].reset_index(drop=True).join(
        r[[f"ret_{h}" for h in HORIZONS]].reset_index(drop=True))
    cells = []
    def emit(sub, action, surprise):
        for h in HORIZONS:
            x = sub[f"ret_{h}"].dropna()
            if x.empty:
                continue
            ymix = sub.loc[x.index, "year"].value_counts().sort_index()
            cells.append({
                "scope": scope, "action": action, "surprise": surprise, "horizon": h,
                "n": len(x), "mean": round(x.mean(), 3), "median": round(x.median(), 3),
                "win_rate": round((x > 0).mean(), 3), "abs_mean": round(x.abs().mean(), 3),
                "adequacy": adequacy(len(x)),
                "year_mix": ", ".join(f"{y}:{c}" for y, c in ymix.items()),
                "surfaced": len(x) >= SURFACE_FLOOR,
            })
    for action in ["hold", "cut", "hike"]:
        a = df[df["action"] == action]
        if a.empty:
            continue
        emit(a, action, "all")
        for sb in ["expected", "surprise"]:
            s = a[a["surprise"] == sb]
            if not s.empty:
                emit(s, action, sb)
    return cells


# ── H2 / H3 ───────────────────────────────────────────────────────────────────
def h2_surprise_amplifies(allcells: pd.DataFrame) -> str:
    """Broad-level: surprise 5d |move| >= 1.5x expected, N>=5/side."""
    out = []
    for scope in ["SPY", "QQQ"]:
        for action in ["cut", "hike"]:
            base = allcells[(allcells.scope == scope) & (allcells.action == action)
                            & (allcells.horizon == 5)]
            exp = base[base.surprise == "expected"]
            sur = base[base.surprise == "surprise"]
            if exp.empty or sur.empty or exp.iloc[0]["n"] < 5 or sur.iloc[0]["n"] < 5:
                out.append(f"  {scope} {action}: insufficient N (exp/sur "
                           f"{exp['n'].sum()}/{sur['n'].sum()}) — not testable")
                continue
            ratio = sur.iloc[0]["abs_mean"] / max(exp.iloc[0]["abs_mean"], 1e-9)
            verdict = "PASS" if ratio >= H2_RATIO_GATE else "FAIL"
            out.append(f"  {scope} {action}: surprise |5d|={sur.iloc[0]['abs_mean']:.2f} vs "
                       f"expected {exp.iloc[0]['abs_mean']:.2f} -> {ratio:.2f}x [{verdict}]")
    return "\n".join(out) or "  (no testable cells)"


def h3_deconfound(ev: pd.DataFrame, panel: pd.DataFrame) -> str:
    """SPY hike-window 5d return vs same-year non-event baseline (5d returns)."""
    s = price_series(panel, "SPY")
    ret5 = (s.shift(-5) / s - 1.0) * 100
    hikes = ev[ev["action"] == "hike"]
    out = []
    for _, h in hikes.groupby(ev["year"]):
        yrs = h["year"].iloc[0]
        ev_dates = set(pd.DatetimeIndex(h["event_date"]).normalize())
        year_mask = (ret5.index.year == yrs)
        non_event = ret5[year_mask & ~ret5.index.normalize().isin(ev_dates)].dropna()
        # event 5d returns this year
        pos = s.index.searchsorted(pd.DatetimeIndex(h["event_date"].values), side="left")
        ev_r = np.array([ (s.iloc[i+5]/s.iloc[i]-1)*100 if 0<=i+5<len(s) else np.nan for i in pos])
        ev_r = ev_r[~np.isnan(ev_r)]
        if len(ev_r) == 0 or non_event.empty:
            continue
        z = (np.mean(ev_r) - non_event.mean()) / max(non_event.std(), 1e-9)
        tag = "regime-driven" if abs(z) < 0.5 else "distinct-from-baseline"
        out.append(f"  {yrs}: hike 5d mean {np.mean(ev_r):+.2f}% vs same-yr baseline "
                   f"{non_event.mean():+.2f}% (z={z:+.2f}) -> {tag}")
    return "\n".join(out) or "  (no hike years)"


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scopes", nargs="*", default=None)
    ap.add_argument("--lead-days", type=int, default=42,
                    help="measure surprise implied-prob this many days before the meeting "
                         "(T-1 is degenerate — FedWatch is ~always right the day before)")
    args = ap.parse_args()

    panel = load_panel()
    ev = enrich_fedwatch(load_events(), args.lead_days)
    print(f"Events: {len(ev)} | actions {ev['action'].value_counts().to_dict()} | "
          f"surprise lead = {args.lead_days}d")
    print(f"Surprise buckets: {ev['surprise'].value_counts().to_dict()}")
    print(f"FedWatch resolved: {ev['fw_meeting_date'].notna().sum()}/{len(ev)} | "
          f"implied prob present: {ev['p_implied'].notna().sum()}/{len(ev)}")

    default = ["SPY", "QQQ", "XLE", "XLF", "XLK", "XLP", "XLU"]
    scopes = args.scopes or default
    spy_ret = forward_returns(ev["event_date"], price_series(panel, "SPY"))

    allcells = []
    for sc in scopes:
        r = scope_returns(ev, panel, sc, None if sc == "SPY" else spy_ret)
        if r is None:
            print(f"  (skip {sc}: no price series)")
            continue
        allcells.extend(aggregate(ev, r, sc))
    cells = pd.DataFrame(allcells)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cells.to_parquet(OUT, index=False)

    # ── report ──
    pd.set_option("display.width", 160, "display.max_rows", 200)
    print("\n" + "=" * 78)
    print("FED-ACTION RESPONSE — by scope/action/surprise (market-adj for non-SPY)")
    print("=" * 78)
    show = cells[(cells.surprise == "all")].copy()
    print("\n-- action 'all' (every event), 5d + 25d --")
    for sc in scopes:
        sub = show[(show.scope == sc) & (show.horizon.isin([5, 25]))]
        if sub.empty:
            continue
        print(f"\n  {sc}")
        for _, c in sub.iterrows():
            print(f"    {c['action']:<5} T+{c['horizon']:<2} mean {c['mean']:+6.2f}%  "
                  f"med {c['median']:+6.2f}%  win {c['win_rate']*100:4.0f}%  N={c['n']:<3} "
                  f"{c['adequacy']:<11} [{c['year_mix']}]")
    print("\n-- SURPRISE split (SPY/QQQ, 5d) --")
    sp = cells[(cells.scope.isin(["SPY", "QQQ"])) & (cells.horizon == 5)
               & (cells.surprise.isin(["expected", "surprise"]))]
    for _, c in sp.sort_values(["scope", "action", "surprise"]).iterrows():
        print(f"  {c['scope']} {c['action']:<5} {c['surprise']:<9} 5d mean {c['mean']:+6.2f}%  "
              f"|mean| {c['abs_mean']:.2f}  N={c['n']:<2} {c['adequacy']}")
    print("\n-- H2: surprise amplifies move size (5d |move|, gate 1.5x) --")
    print(h2_surprise_amplifies(cells))
    print("\n-- H3: SPY hikes vs same-year non-event baseline (de-confound) --")
    print(h3_deconfound(ev, panel))
    print(f"\nWrote {len(cells)} cells -> {OUT}")


if __name__ == "__main__":
    main()
