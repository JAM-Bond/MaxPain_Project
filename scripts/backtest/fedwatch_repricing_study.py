#!/usr/bin/env python3.11
"""
FedWatch Repricing × Vertical Side — Phase 1 study.

Implements docs/FEDWATCH_REPRICING_PREREG.md (sealed 2026-06-04). Tests whether
the DIRECTION of rate repricing during the entry window predicts which side a
credit vertical should favor, via each name's regime_primary PC1 (reflation/rates)
loading.

Two tracks:
  • Track A (proxy, POWERED): anticipation proxy = 20-trading-day change in the
    2y Treasury yield (DGS2_d20). 2015→2026, full 4-split walk-forward. A close
    cousin of the already-nulled TRAILING regime-conditioning, so it's a
    lower-bound / sanity check, NOT a verdict on the anticipatory thesis.
  • Track B (native FedWatch, N-LIMITED): cme_fedwatch_history repricing velocity.
    Series starts 2026-02-23, so today this is ~1 cycle → UNDERPOWERED.

⚠️ NON-TERMINAL NULL: while Gate P (power) is unmet, Track B's verdict is
INCONCLUSIVE, not REJECTED. Re-run as the FedWatch series grows (every +3
episodes or each quarter). See the pre-reg. An accrual log row is appended each
run so power accumulation is visible.

Usage: python3.11 -m scripts.backtest.fedwatch_repricing_study
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

# ─── Sealed thresholds (pre-reg) ──────────────────────────────────────
EPISODE_FW_PP = 5.0           # Track B: |Δ(hike−cut)| over 14d, percentage points
EPISODE_FW_LOOKBACK_D = 14
EPISODE_PROXY_BP = 0.25       # Track A: |ΔDGS2| over ~20 td (sealed Track-A choice ≈ ¼ point)
PROXY_COL = "DGS2_d20"
GATE_A_EFFECT = 0.05          # aligned − adverse mean mgd50 P/L ($/contract/cycle)
GATE_B_WF_MIN = 3             # of 4 walk-forward windows (Track A)
GATE_C_LOSSRATE_PP = 0.10     # H2: adverse loss-rate ≥ aligned + 10pp
GATE_C_TAIL_MULT = 1.30       # H2 alt: adverse mean-loss ≥ 1.3× aligned
GATE_P_MIN_EPISODES = 6       # POWER gate — terminal-vs-inconclusive switch
GATE_P_MIN_TRADES = 40

FW_START = date(2026, 2, 23)
OUT = ROOT / "data/profile/fedwatch_repricing_study.parquet"
ACCRUAL = ROOT / "data/profile/fedwatch_repricing_accrual.jsonl"


# ─── Inputs ───────────────────────────────────────────────────────────

def load_outcomes() -> pd.DataFrame:
    """OTM per-cycle mgd50 outcomes for both verticals (the H-A/H-B substrate)."""
    frames = []
    for fn, struct in [("bull_put_moneyness_results", "bull_put"),
                       ("bear_call_moneyness_results", "bear_call")]:
        d = pd.read_parquet(ROOT / f"data/profile/{fn}.parquet",
                            columns=["ticker", "moneyness", "entry_date",
                                     "mgd50_pnl", "mgd50_win"])
        d = d[d["moneyness"] == "OTM"].copy()
        d["structure"] = struct
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["entry_date"] = pd.to_datetime(df["entry_date"])
    return df


def load_pc1_sign() -> pd.DataFrame:
    """ticker → PC1 sign (+1 PC1+, −1 PC1−); other buckets excluded (not the bet)."""
    prof = pd.read_parquet(ROOT / "data/macro/macro_profile.parquet",
                           columns=["ticker", "regime_primary"])
    prof["pc1_sign"] = prof["regime_primary"].map({"PC1+": 1, "PC1-": -1})
    return prof.dropna(subset=["pc1_sign"])[["ticker", "pc1_sign"]]


def proxy_series() -> pd.Series:
    """date → DGS2_d20 (20-td change in the 2y yield), the Track-A anticipation proxy."""
    j = pd.read_parquet(ROOT / "data/macro/macro_join_13y.parquet",
                        columns=["date", PROXY_COL])
    j["date"] = pd.to_datetime(j["date"])
    return j.drop_duplicates("date").set_index("date")[PROXY_COL].sort_index()


# ─── Alignment + contrast ─────────────────────────────────────────────

def alignment(structure: pd.Series, pc1_sign: pd.Series, ep_dir: pd.Series) -> np.ndarray:
    """Sealed mapping. bull_put wants a tailwind (pc1_sign == dir); bear_call wants
    a headwind (pc1_sign == −dir). dir: +1 hike-repricing, −1 cut-repricing."""
    return np.where(structure.values == "bull_put",
                    pc1_sign.values == ep_dir.values,
                    pc1_sign.values == -ep_dir.values)


def contrast(ep: pd.DataFrame) -> dict:
    """Aligned vs adverse summary on an episode cohort."""
    a = ep.loc[ep["aligned"], "mgd50_pnl"]
    v = ep.loc[~ep["aligned"], "mgd50_pnl"]
    def lossmean(x):
        neg = x[x < 0]
        return float(-neg.mean()) if len(neg) else 0.0
    return {
        "n_aligned": int(len(a)), "n_adverse": int(len(v)),
        "mean_aligned": float(a.mean()) if len(a) else float("nan"),
        "mean_adverse": float(v.mean()) if len(v) else float("nan"),
        "effect": (float(a.mean() - v.mean()) if len(a) and len(v) else float("nan")),
        "lossrate_aligned": float((a < 0).mean()) if len(a) else float("nan"),
        "lossrate_adverse": float((v < 0).mean()) if len(v) else float("nan"),
        "meanloss_aligned": lossmean(a), "meanloss_adverse": lossmean(v),
    }


def episodes_and_trades(ep: pd.DataFrame) -> tuple[int, int]:
    return int(ep["entry_date"].nunique()), int(len(ep))


# ─── Track A (proxy, powered) ─────────────────────────────────────────

def run_track_a(out: pd.DataFrame) -> dict:
    px = proxy_series()
    df = out.copy()
    df["proxy"] = df["entry_date"].map(px)
    df = df.dropna(subset=["proxy"])
    df["episode"] = df["proxy"].abs() >= EPISODE_PROXY_BP
    df["ep_dir"] = np.sign(df["proxy"]).astype(int)
    ep = df[df["episode"]].copy()
    ep["aligned"] = alignment(ep["structure"], ep["pc1_sign"], ep["ep_dir"])

    n_ep, n_tr = episodes_and_trades(ep)
    summ = contrast(ep)

    # 4-split chronological walk-forward
    yr = ep["entry_date"].dt.year
    edges = [(2015, 2017), (2018, 2020), (2021, 2023), (2024, 2026)]
    wf = []
    for lo, hi in edges:
        sub = ep[(yr >= lo) & (yr <= hi)]
        c = contrast(sub)
        wins = (c["effect"] > 0) if c["effect"] == c["effect"] else False
        wf.append({"window": f"{lo}-{hi}", "effect": c["effect"],
                   "n_aligned": c["n_aligned"], "n_adverse": c["n_adverse"],
                   "aligned_wins": bool(wins)})
    wf_wins = sum(w["aligned_wins"] for w in wf)

    powered = n_ep >= GATE_P_MIN_EPISODES and n_tr >= GATE_P_MIN_TRADES
    gate_a = summ["effect"] >= GATE_A_EFFECT if summ["effect"] == summ["effect"] else False
    gate_b = wf_wins >= GATE_B_WF_MIN
    gate_c = (
        (summ["lossrate_adverse"] - summ["lossrate_aligned"]) >= GATE_C_LOSSRATE_PP
        or (summ["meanloss_aligned"] > 0
            and summ["meanloss_adverse"] >= GATE_C_TAIL_MULT * summ["meanloss_aligned"])
    )
    return {"track": "A_proxy", "powered": powered, "n_episodes": n_ep, "n_trades": n_tr,
            **summ, "wf": wf, "wf_wins": wf_wins,
            "gate_A": bool(gate_a), "gate_B": bool(gate_b), "gate_C": bool(gate_c)}


# ─── Track B (native FedWatch, N-limited) ─────────────────────────────

def _fw_history_by_meeting() -> dict[date, dict[date, float]]:
    """meeting_date → {scrape_date: (hike% − cut%)} from cme_fedwatch_history."""
    sys.path.insert(0, str(Path.home() / "Agent_Project"))
    from shared.chromadb_client import DataPipelineChromaDB
    db = DataPipelineChromaDB()
    hist = db.get_all_documents("cme_fedwatch_history")
    out: dict[date, dict[date, float]] = defaultdict(dict)
    if not hist or not hist.get("metadatas"):
        return out
    for m in hist["metadatas"]:
        ms, sds = m.get("meeting_date"), m.get("scrape_date")
        if not ms or not sds:
            continue
        try:
            mtg = datetime.strptime(ms, "%m/%d/%Y").date()
            sd = datetime.fromisoformat(sds.rstrip("Z")).date()
        except Exception:
            continue
        hike, cut = m.get("hike_probability"), m.get("cut_probability")
        if hike is None or cut is None:
            continue
        out[mtg][sd] = float(hike) - float(cut)
    return out


def _fw_episode_asof(by_meeting: dict, asof: date) -> tuple[bool, int, float]:
    """Classify a FedWatch repricing episode as-of `asof`: nearest future meeting,
    Δ(hike−cut) over the trailing ~14d. Returns (is_episode, direction, velocity)."""
    future = sorted(m for m in by_meeting if m > asof)
    for mtg in future:                       # nearest meeting with usable history
        series = {sd: v for sd, v in by_meeting[mtg].items() if sd <= asof}
        if len(series) < 2:
            continue
        latest = max(series)
        target = latest - timedelta(days=EPISODE_FW_LOOKBACK_D)
        baseline = min(series, key=lambda s: abs((s - target).days))
        if baseline == latest:
            continue
        vel = series[latest] - series[baseline]
        return (abs(vel) >= EPISODE_FW_PP, int(np.sign(vel)), float(vel))
    return (False, 0, float("nan"))


def run_track_b(out: pd.DataFrame) -> dict:
    by_meeting = _fw_history_by_meeting()
    recent = out[out["entry_date"].dt.date >= FW_START].copy()
    classifiable = []
    ep_dates = []
    for ed, grp in recent.groupby(recent["entry_date"].dt.date):
        is_ep, ep_dir, vel = _fw_episode_asof(by_meeting, ed)
        if not is_ep:
            continue
        ep_dates.append((ed, ep_dir, vel))
        g = grp.copy()
        g["ep_dir"] = ep_dir
        g["aligned"] = alignment(g["structure"], g["pc1_sign"], g["ep_dir"])
        classifiable.append(g)

    if classifiable:
        ep = pd.concat(classifiable, ignore_index=True)
        n_ep, n_tr = episodes_and_trades(ep)
        summ = contrast(ep)
    else:
        n_ep, n_tr = 0, 0
        summ = {k: (0 if k.startswith("n_") else float("nan"))
                for k in ("n_aligned", "n_adverse", "mean_aligned", "mean_adverse",
                          "effect", "lossrate_aligned", "lossrate_adverse",
                          "meanloss_aligned", "meanloss_adverse")}

    powered = n_ep >= GATE_P_MIN_EPISODES and n_tr >= GATE_P_MIN_TRADES
    verdict = "INCONCLUSIVE (underpowered — NON-TERMINAL; re-run as N grows)" if not powered \
        else "POWERED — apply gates"
    return {"track": "B_fedwatch", "powered": powered, "n_episodes": n_ep, "n_trades": n_tr,
            "episode_dates": [(str(d), dr, round(v, 1)) for d, dr, v in ep_dates],
            "n_entries_since_fw_start": int(recent["entry_date"].dt.date.nunique()),
            "verdict": verdict, **summ}


# ─── Report + persist ─────────────────────────────────────────────────

def _fmt(x):
    return f"{x:+.3f}" if isinstance(x, float) and x == x else "n/a"


def main() -> int:
    out = load_outcomes().merge(load_pc1_sign(), on="ticker", how="inner")
    print(f"Substrate: {len(out):,} OTM cycle-trades on {out['ticker'].nunique()} "
          f"PC1± cohort names, {out['entry_date'].dt.date.min()}→{out['entry_date'].dt.date.max()}")
    print("=" * 74)

    A = run_track_a(out)
    print("\nTRACK A — front-end-yield proxy (POWERED; lower-bound, not a verdict on the")
    print("          anticipatory thesis — close to the already-nulled trailing conditioning)")
    print(f"  episodes={A['n_episodes']}  trades={A['n_trades']}  powered={A['powered']}")
    print(f"  aligned mean {_fmt(A['mean_aligned'])}  vs adverse {_fmt(A['mean_adverse'])}"
          f"  → effect {_fmt(A['effect'])}/contract  (Gate A ≥ +{GATE_A_EFFECT}: {A['gate_A']})")
    print(f"  walk-forward aligned-wins: {A['wf_wins']}/4  (Gate B ≥ {GATE_B_WF_MIN}: {A['gate_B']})")
    for w in A["wf"]:
        print(f"     {w['window']}  effect {_fmt(w['effect'])}  "
              f"(n_al={w['n_aligned']}, n_adv={w['n_adverse']})  win={w['aligned_wins']}")
    print(f"  H2 tail: adverse loss-rate {_fmt(A['lossrate_adverse'])} vs aligned "
          f"{_fmt(A['lossrate_aligned'])}; adverse mean-loss {_fmt(A['meanloss_adverse'])} "
          f"vs {_fmt(A['meanloss_aligned'])}  (Gate C: {A['gate_C']})")

    B = run_track_b(out)
    print("\nTRACK B — native FedWatch repricing (cme_fedwatch_history)")
    print(f"  entries since {FW_START}: {B['n_entries_since_fw_start']}  |  "
          f"episodes={B['n_episodes']}  trades={B['n_trades']}  "
          f"(Gate P needs ≥{GATE_P_MIN_EPISODES} ep & ≥{GATE_P_MIN_TRADES} trades)")
    if B["episode_dates"]:
        print(f"  classified episodes: {B['episode_dates']}")
    print(f"  → {B['verdict']}")
    if B["n_trades"]:
        print(f"     (point estimate, NOT a verdict: aligned {_fmt(B['mean_aligned'])} "
              f"vs adverse {_fmt(B['mean_adverse'])}, effect {_fmt(B['effect'])})")

    print("\n" + "=" * 74)
    print("STUDY VERDICT: " + (
        "Track B INCONCLUSIVE (underpowered, NON-TERMINAL — re-run as the FedWatch\n"
        "  series accrues). Track A reported as a powered lower-bound only."
        if not B["powered"] else "Track B POWERED — see gates above."))

    # Persist a flat results row + append the accrual log
    OUT.parent.mkdir(parents=True, exist_ok=True)
    run_day = out["entry_date"].dt.date.max()  # no Date.now in this env-agnostic sense; use latest data date
    row = {"run_data_through": str(run_day),
           **{f"A_{k}": v for k, v in A.items() if not isinstance(v, list)},
           **{f"B_{k}": v for k, v in B.items() if not isinstance(v, list)}}
    pd.DataFrame([row]).to_parquet(OUT, index=False)
    print(f"\nWrote {OUT.relative_to(ROOT)}")

    accr = {"data_through": str(run_day),
            "B_n_episodes": B["n_episodes"], "B_n_trades": B["n_trades"],
            "B_powered": B["powered"], "B_effect": B["effect"],
            "B_episode_dates": B["episode_dates"]}
    with open(ACCRUAL, "a") as f:
        f.write(json.dumps(accr) + "\n")
    print(f"Appended accrual log → {ACCRUAL.relative_to(ROOT)} "
          f"(re-run trigger: +3 episodes or each quarter until Gate P met)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
