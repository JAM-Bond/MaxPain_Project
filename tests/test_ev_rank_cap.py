"""Unit test for the EV-rank tiebreak wired into the concentration caps (spec step C).

Standalone (no pytest in this env): `python3.11 -m tests.test_ev_rank_cap`.
Monkeypatches lib.trade_ev.score_candidate so NO live Schwab chain is fetched —
the test only exercises the cross-structure normalization + sort + fail-open logic.

Covers:
  1. single-kind bucket   → order reduces to raw ev_per_risk descending (the spec)
  2. mixed-kind bucket     → a zebra's large raw ev_per_risk does NOT dominate a
                             vertical (units artifact removed); best-of-each-kind kept
  3. per-candidate failure → unscored row sorts last (alphabetical fallback)
  4. whole-bucket failure  → every candidate failed → identical to old alphabetical
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

import lib.trade_ev as ev                                   # noqa: E402
from scripts.qualifier.cycle_qualifier import _ev_rank_bucket  # noqa: E402

VR = {"GO": 0, "DOWNSIZE": 1}


def _stub_scores(table: dict[str, tuple[float | None, bool, str]]):
    """Patch score_candidate to return synthetic EVScores keyed by symbol.
    table[symbol] = (ev_per_risk_or_None, passes_hard_gates, error_or_'')."""
    def fake(symbol, structure, expiry, chain=None, spot=None, cache=None):
        epr, gates, err = table[symbol]
        return ev.EVScore(
            symbol=symbol, structure=structure,
            structure_kind=ev.kind_of(structure),
            ev_per_risk=epr, passes_hard_gates=gates,
            error=(err or None),
        )
    ev.score_candidate = fake


def _row(sym, structure, verdict="GO"):
    return {"symbol": sym, "structure": structure, "verdict": verdict,
            "opex": "2026-08-21", "expiry": "2026-08-21"}


def test_single_kind_reduces_to_ev_per_risk():
    _stub_scores({"AAA": (0.10, True, ""), "BBB": (0.30, True, ""),
                  "CCC": (0.20, True, "")})
    bucket = [_row("AAA", "bull_put"), _row("BBB", "bull_put"), _row("CCC", "bull_put")]
    ranked, cov = _ev_rank_bucket(bucket, VR, {})
    order = [r["symbol"] for r in ranked]
    assert order == ["BBB", "CCC", "AAA"], order          # 0.30 > 0.20 > 0.10
    assert cov["scored"] == 3
    print("  ✓ single-kind bucket orders by ev_per_risk descending")


def test_mixed_kind_no_units_dominance():
    # raw ev_per_risk would rank both zebras (5.0, 3.0) above both verticals (.30,.10).
    _stub_scores({"VLO": (0.30, True, ""), "APA": (0.10, True, ""),
                  "NVDA": (5.0, True, ""), "MSFT": (3.0, True, "")})
    bucket = [_row("VLO", "bull_put"), _row("APA", "bull_put"),
              _row("NVDA", "zebra_tier1"), _row("MSFT", "zebra_tier1")]
    ranked, cov = _ev_rank_bucket(bucket, VR, {})
    order = [r["symbol"] for r in ranked]
    # within-kind percentile: best vertical (VLO) and best zebra (NVDA) tie at top;
    # worst-of-each (APA, MSFT) tie at bottom. Top-2 (a cap=2) keeps one of each kind.
    assert set(order[:2]) == {"VLO", "NVDA"}, order
    assert set(order[2:]) == {"APA", "MSFT"}, order
    print("  ✓ mixed-kind bucket: zebra's raw scale does not dominate vertical")


def test_failopen_unscored_sorts_last():
    _stub_scores({"AAA": (0.10, True, ""), "BBB": (None, False, "no chain"),
                  "CCC": (0.05, True, "")})
    bucket = [_row("AAA", "bull_put"), _row("BBB", "bull_put"), _row("CCC", "bull_put")]
    ranked, cov = _ev_rank_bucket(bucket, VR, {})
    order = [r["symbol"] for r in ranked]
    assert order[-1] == "BBB", order                       # unscored sorts last
    assert order[:2] == ["AAA", "CCC"], order              # 0.10 > 0.05
    assert cov["scored"] == 2 and cov["failed"] == 1
    print("  ✓ per-candidate failure sorts last (alphabetical fallback for it)")


def test_whole_bucket_failure_is_alphabetical():
    _stub_scores({"ZZZ": (None, False, "no chain"), "AAA": (None, False, "no chain"),
                  "MMM": (None, False, "no chain")})
    bucket = [_row("ZZZ", "bull_put"), _row("AAA", "bull_put"), _row("MMM", "bull_put")]
    ranked, cov = _ev_rank_bucket(bucket, VR, {})
    order = [r["symbol"] for r in ranked]
    assert order == ["AAA", "MMM", "ZZZ"], order           # pure alphabetical
    assert cov["scored"] == 0
    print("  ✓ whole-bucket failure → identical to old alphabetical order")


def test_verdict_tier_beats_ev():
    # a DOWNSIZE with great EV still ranks below a GO with poor EV
    _stub_scores({"AAA": (0.01, True, ""), "BBB": (0.99, True, "")})
    bucket = [_row("AAA", "bull_put", "GO"), _row("BBB", "bull_put", "DOWNSIZE")]
    ranked, _ = _ev_rank_bucket(bucket, VR, {})
    assert [r["symbol"] for r in ranked] == ["AAA", "BBB"]
    print("  ✓ verdict tier (GO > DOWNSIZE) still outranks EV")


def main():
    orig = ev.score_candidate
    try:
        print("EV-rank cap tiebreak — unit tests")
        test_single_kind_reduces_to_ev_per_risk()
        test_mixed_kind_no_units_dominance()
        test_failopen_unscored_sorts_last()
        test_whole_bucket_failure_is_alphabetical()
        test_verdict_tier_beats_ev()
        print("ALL PASSED")
    finally:
        ev.score_candidate = orig


if __name__ == "__main__":
    main()
