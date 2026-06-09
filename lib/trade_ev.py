"""EV-rank scoring for candidate trades — per docs/EV_RANK_TIEBREAKER_SPEC.md.

Ordinal reward/risk ranking among ALREADY-QUALIFIED candidates: "among equally
attractive candidates, which makes the most if it wins and loses the least if it
loses?" NOT a selection signal and NOT an alpha claim (see spec §Caveats:
POP-from-delta is a risk-neutral proxy, consistently biased, fine for ordering).

SINGLE SOURCE OF TRUTH: every raw number is read from the live opener path
(structures.open_* → Position.notes / .legs) — the SAME numbers
trade_construction.py displays — so the construction block and this ranker can
never drift. Only the EV/rank formulas live here.

Structure-aware rank key (`ev_per_risk`, higher = better):
  vertical (bull_put/bear_call[/_mp/_earnings]):
      credit, wing=notes["wing_width"], max_loss=wing−credit
      |short_delta| = |_display_delta(legs[0])|  (own-type delta; put = call_delta−1)
      POP ≈ 1 − |short_delta|;  EV = POP·credit − (1−POP)·max_loss
      rank = EV / max_loss
  zebra (zebra_tier1/2, anti_zebra):
      defined risk = debit; rank = net_delta·spot/debit = entry_delta / capital_efficiency
      (capital_efficiency = debit/spot, stored by the opener) — HIGHER = better.
      pre-gate: extrinsic_cushion ≥ 0
  inverted_fly:
      debit = −entry_credit; max_profit_per_side = wing − debit; rank = that / debit

Hard gates (EV never overrides risk discipline):
  vertical: credit > 0 AND credit/wing ≥ G.MIN_CREDIT_WIDTH AND max_loss > 0
  zebra:    extrinsic_cushion ≥ 0 AND debit > 0
  inv_fly:  debit > 0 AND max_profit_per_side > 0
Gate-fails / construction errors fail-open: scored but sorted last (EV unknown).

NOTE: ev_per_risk is comparable WITHIN a structure kind, not across kinds
(EV/max_loss for a vertical ≈ 0.x vs Δ·spot/debit ≈ 4 for a zebra). rank_candidates
ranks within (verdict, structure) groups. Cross-structure normalization is a
decision for the cap-wiring phase (spec step 4), deliberately not done here.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "backtest"))  # structures' `from legs import ...`

from scripts.qualifier import gate_config as G                     # noqa: E402
from lib.schwab_options import fetch_chain_with_greeks             # noqa: E402
# Reuse the construction routing + delta convention = single source of truth.
from scripts.monitor.trade_construction import (                   # noqa: E402
    STRUCTURE_TO_OPENER, _display_delta,
)
# Per-name moneyness recs + backtest selection config — applied before opening so
# the ranker scores the SAME strikes the live construction trades (no drift).
from scripts.monitor.moneyness_lookup import (                     # noqa: E402
    recommended_short_delta, recommended_if_wing,
)
import config as _bt_config  # noqa: E402  (trade_construction already activated v2)

VERTICAL_PREFIXES = ("bull_put", "bear_call")
ZEBRA_NAMES = {"zebra_tier1", "zebra_tier2", "anti_zebra"}
INVFLY_PREFIX = "inverted_fly"


def kind_of(structure: str) -> Optional[str]:
    if structure in ZEBRA_NAMES or structure.startswith("zebra"):
        return "zebra"
    if structure.startswith(INVFLY_PREFIX):
        return "inverted_fly"
    if structure.startswith(VERTICAL_PREFIXES):
        return "vertical"
    return None


@dataclass
class EVScore:
    symbol: str
    structure: str
    structure_kind: Optional[str] = None
    # vertical
    credit: Optional[float] = None
    wing: Optional[float] = None
    max_loss: Optional[float] = None
    short_delta: Optional[float] = None
    pop: Optional[float] = None
    ev: Optional[float] = None
    # zebra
    debit: Optional[float] = None
    net_delta: Optional[float] = None
    spot: Optional[float] = None
    extrinsic_cushion: Optional[float] = None
    # inverted fly
    max_profit_per_side: Optional[float] = None
    # common
    ev_per_risk: Optional[float] = None   # the rank key (per-structure semantics)
    rank_basis: str = ""
    passes_hard_gates: bool = False
    gate_note: str = ""
    error: Optional[str] = None


def _vertical(pos, score: EVScore):
    short_leg = pos.legs[0]
    credit = float(pos.entry_credit)
    wing = float(pos.notes["wing_width"])
    max_loss = wing - credit
    sd = abs(_display_delta(short_leg))
    pop = 1.0 - sd
    ev = pop * credit - (1.0 - pop) * max_loss
    score.credit, score.wing, score.max_loss = credit, wing, max_loss
    score.short_delta, score.pop, score.ev = sd, pop, ev
    cw = credit / wing if wing else 0.0
    gates = (credit > 0) and (cw >= G.MIN_CREDIT_WIDTH) and (max_loss > 0)
    score.passes_hard_gates = bool(gates)
    if credit <= 0:
        score.gate_note = "credit ≤ 0"
    elif cw < G.MIN_CREDIT_WIDTH:
        score.gate_note = f"C/W {cw:.2f} < {G.MIN_CREDIT_WIDTH:.2f} floor"
    score.ev_per_risk = (ev / max_loss) if max_loss > 0 else None
    score.rank_basis = "EV / max_loss"


def _zebra(pos, score: EVScore):
    n = pos.notes
    debit = float(n["debit"])
    net_delta = float(n["entry_delta"])
    cap_eff = float(n["capital_efficiency"])          # = debit / spot
    cushion = float(n["extrinsic_cushion"])
    score.debit, score.net_delta, score.extrinsic_cushion = debit, net_delta, cushion
    score.spot = (debit / cap_eff) if cap_eff else None
    # rank = net_delta·spot/debit = net_delta / (debit/spot) = net_delta / capital_efficiency
    score.ev_per_risk = (net_delta / cap_eff) if cap_eff else None
    score.rank_basis = "Δ·spot/debit (exposure per $ risk)"
    gates = (cushion >= 0) and (debit > 0)
    score.passes_hard_gates = bool(gates)
    if cushion < 0:
        score.gate_note = f"extrinsic_cushion {cushion:+.2f} < 0"


def _inverted_fly(pos, score: EVScore):
    debit = -float(pos.entry_credit)                  # entry_credit negative for IF
    wing = float(pos.notes["wing_width"])
    mpps = wing - debit
    score.debit, score.wing, score.max_profit_per_side = debit, wing, mpps
    score.ev_per_risk = (mpps / debit) if debit > 0 else None
    score.rank_basis = "max_profit_per_side / debit"
    gates = (debit > 0) and (mpps > 0)
    score.passes_hard_gates = bool(gates)
    if debit <= 0:
        score.gate_note = "debit ≤ 0"
    elif mpps <= 0:
        score.gate_note = "max_profit ≤ 0"


_DISPATCH = {"vertical": _vertical, "zebra": _zebra, "inverted_fly": _inverted_fly}


def _apply_moneyness(symbol: str, structure: str) -> None:
    """Mirror trade_construction.build_construction_block: set the per-name short
    delta / IF wing into _bt_config before opening, so scored strikes == traded
    strikes. ZEBRA has no per-name moneyness. Soft-fail to defaults on lookup error."""
    try:
        if structure.startswith(("bull_put", "bear_call")):
            _bt_config.VERTICAL_SHORT_DELTA = recommended_short_delta(
                symbol, structure, exit_rule="mgd50").short_delta
        elif structure.startswith("inverted_fly"):
            _bt_config.BFLY_WING_PCT_SPOT = recommended_if_wing(symbol).wing_pct
    except Exception:
        pass  # fall back to current config defaults


def score_candidate(symbol: str, structure: str, expiry: str,
                    chain: Optional[pd.DataFrame] = None,
                    spot: Optional[float] = None,
                    cache: Optional[dict] = None) -> EVScore:
    """Score one (symbol, structure, expiry). Fail-open: returns EVScore with
    .error set (and passes_hard_gates=False) on any chain/construction failure."""
    kind = kind_of(structure)
    s = EVScore(symbol=symbol, structure=structure, structure_kind=kind)
    if kind is None:
        s.error = f"unknown structure kind: {structure}"
        return s
    opener = STRUCTURE_TO_OPENER.get(structure)
    if opener is None:
        s.error = f"no opener for {structure}"
        return s
    try:
        if chain is None:
            key = (symbol, expiry)
            if cache is not None and key in cache:
                chain, spot = cache[key]
            else:
                chain, spot = fetch_chain_with_greeks(symbol, expiry)
                if cache is not None:
                    cache[key] = (chain, spot)
        if chain is None or getattr(chain, "empty", True):
            s.error = "no chain"
            return s
        if "_mp" in structure:   # mp-anchored opener needs max_pain= ; tabled style, not supported here
            s.error = "mp-anchored structures not supported in EV ranker yet"
            return s
        _apply_moneyness(symbol, structure)
        pos = opener(chain, pd.Timestamp.today(), pd.Timestamp(expiry))
        if pos is None:
            s.error = "no structure (strikes unavailable)"
            return s
        _DISPATCH[kind](pos, s)
    except Exception as e:
        s.error = f"{e.__class__.__name__}: {e}"
    return s


# verdict precedence (matches the qualifier: GO outranks DOWNSIZE)
VERDICT_RANK = {"GO": 0, "DOWNSIZE": 1, "SKIP_CONCENTRATION": 2}


def rank_candidates(rows: list[dict]) -> list[dict]:
    """Score + rank a slate. Each row: {symbol, structure, expiry, verdict?}.
    Ranks WITHIN (verdict, structure) groups by −ev_per_risk; gate-fails/errors
    sort last (fail-open). Returns rows annotated with `ev` (EVScore) and
    `ev_rank_position` ("k/N" within its group). One chain fetch per (symbol,expiry)."""
    cache: dict = {}
    scored = []
    for r in rows:
        ev = score_candidate(r["symbol"], r["structure"], r.get("expiry"), cache=cache)
        scored.append({**r, "ev": ev})

    # group by (verdict, structure); rank by passes-gate then −ev_per_risk then symbol
    from collections import defaultdict
    groups = defaultdict(list)
    for row in scored:
        groups[(row.get("verdict", ""), row["structure"])].append(row)
    for _, grp in groups.items():
        grp.sort(key=lambda x: (
            0 if x["ev"].passes_hard_gates else 1,
            -(x["ev"].ev_per_risk if x["ev"].ev_per_risk is not None else float("-inf")),
            x["symbol"]))
        n = len(grp)
        for i, row in enumerate(grp, 1):
            row["ev_rank_position"] = f"{i}/{n}"
    return scored


# ─── Cross-structure tiebreak for the concentration caps (spec step C) ────────

def annotate_bucket_ev(bucket: list[dict], cache: Optional[dict] = None) -> dict:
    """Score one OVER-cap bucket of candidate rows and attach a cross-structure-
    comparable ordinal tiebreak score. Mutates each row in place:

        row["_ev"]      -> EVScore (always set)
        row["_ev_epr"]  -> raw ev_per_risk if the row passes hard gates, else None
        row["_ev_norm"] -> within-(structure-kind) median-rank percentile in (0,1),
                           higher = better, or None if the row failed hard gates /
                           errored (→ caller sorts it last, i.e. alphabetical).

    Why a within-kind percentile and not raw ev_per_risk: ev_per_risk is only
    comparable WITHIN a structure kind (a vertical's EV/max_loss ≈ 0.x vs a zebra's
    Δ·spot/debit ≈ 4). A sector/regime bucket can mix kinds (e.g. MSFT bull_put +
    NVDA zebra), so sorting by raw ev_per_risk would rank zebras first purely as a
    units artifact. The median-rank percentile (i+0.5)/n is comparable across kinds
    and, for a single-kind bucket (the common case), is monotonic in ev_per_risk —
    so it reduces exactly to the spec's `−ev_per_risk`. A lone member of a kind
    lands at the neutral 0.5 rather than dominating.

    Fail-open: a candidate whose chain/construction fails (thin 9:25 chains,
    Schwab outage) gets _ev_norm=None and sorts last. Returns a small coverage
    dict {"n": total, "scored": usable, "failed": fail-open count} for logging.
    """
    from collections import defaultdict

    n_usable = 0
    for r in bucket:
        try:
            ev = score_candidate(r["symbol"], r["structure"],
                                 r.get("expiry") or r.get("opex"), cache=cache)
        except Exception as e:  # belt-and-suspenders; score_candidate already fails open
            ev = EVScore(symbol=r["symbol"], structure=r["structure"],
                         error=f"{e.__class__.__name__}: {e}")
        usable = (ev.error is None) and ev.passes_hard_gates and (ev.ev_per_risk is not None)
        r["_ev"] = ev
        r["_ev_epr"] = ev.ev_per_risk if usable else None
        r["_ev_norm"] = None
        n_usable += usable

    # within-kind median-rank percentile over the usable rows only
    by_kind: dict = defaultdict(list)
    for r in bucket:
        if r["_ev_epr"] is not None:
            by_kind[kind_of(r["structure"])].append(r)
    for members in by_kind.values():
        members.sort(key=lambda r: r["_ev_epr"])   # ascending: worst first
        n = len(members)
        for i, r in enumerate(members):             # i=0 worst → lowest percentile
            r["_ev_norm"] = (i + 0.5) / n

    return {"n": len(bucket), "scored": n_usable, "failed": len(bucket) - n_usable}


# ─── Demo CLI: rank today's committed slate across all structures ─────────────
def _demo():
    import sqlite3
    from lib.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    latest = conn.execute("SELECT MAX(run_date) FROM cycle_qualifier_runs").fetchone()[0]
    rows = [dict(symbol=s, structure=st, verdict=v, expiry=opex) for s, st, v, opex in
            conn.execute("SELECT symbol, structure, verdict, opex FROM cycle_qualifier_runs "
                         "WHERE run_date=? AND verdict IN ('GO','DOWNSIZE') ORDER BY structure, symbol",
                         (latest,))]
    conn.close()
    print(f"EV-RANK full-slate preview — run {latest} | {len(rows)} GO/DOWNSIZE candidates")
    print("ranked within (verdict, structure); ev_per_risk higher=better; gate-fails/errors last")
    print("=" * 92)
    scored = rank_candidates(rows)
    from collections import defaultdict
    by = defaultdict(list)
    for r in scored:
        by[(r["structure"], r["verdict"])].append(r)
    for (structure, verdict) in sorted(by):
        print(f"\n── {structure} / {verdict} ── ({kind_of(structure)}; rank = "
              f"{(by[(structure, verdict)][0]['ev'].rank_basis or '—')})")
        for r in sorted(by[(structure, verdict)],
                        key=lambda x: int(x["ev_rank_position"].split("/")[0])):
            ev = r["ev"]
            epr = f"{ev.ev_per_risk:+.3f}" if ev.ev_per_risk is not None else "   —  "
            status = "" if ev.passes_hard_gates else f"  ⚠{ev.gate_note or ev.error or 'gate-fail'}"
            print(f"  {r['ev_rank_position']:>5}  {r['symbol']:<6} ev/risk={epr}{status}")


if __name__ == "__main__":
    _demo()
