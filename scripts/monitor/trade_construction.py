"""
Live trade construction blocks for the daily alert.

For each actionable (GO/DOWNSIZE) row in cycle_qualifier_runs, this module
pulls the live Schwab option chain and runs the corresponding open_*
selection logic from scripts/backtest/structures.py to produce a concrete
trade specification (legs + strikes + deltas + prices + risk metrics).

Output formats:
  - text: monospace, suitable for terminal/log
  - html: table-based, suitable for email

Supported structures:
  - zebra_tier1, zebra_tier2  → 3-leg call structure
  - inverted_fly_pair, inverted_fly_single, inverted_fly_earnings → 4-leg
  - bull_put, bull_put_earnings → 2-leg put credit spread
  - bear_call, bear_call_earnings → 2-leg call credit spread
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
METAL_ROOT = Path.home() / "Metal_Project"
BACKTEST_DIR = ROOT / "scripts/backtest"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(METAL_ROOT))
sys.path.insert(0, str(BACKTEST_DIR))

from lib.schwab_options import fetch_chain_with_greeks  # noqa: E402
from structures import (  # noqa: E402
    open_zebra, open_inverted_fly, open_bull_put, open_bear_call,
)


# ─── Routing: structure name → open_* helper ──────────────────────────

STRUCTURE_TO_OPENER = {
    "zebra_tier1": open_zebra,
    "zebra_tier2": open_zebra,
    "inverted_fly_pair": open_inverted_fly,
    "inverted_fly_single": open_inverted_fly,
    "inverted_fly_earnings": open_inverted_fly,
    "bull_put": open_bull_put,
    "bull_put_earnings": open_bull_put,
    "bear_call": open_bear_call,
    "bear_call_earnings": open_bear_call,
}


# ─── Risk metrics per structure ───────────────────────────────────────

def _display_delta(leg) -> float:
    """Convert engine call-delta to standard trader convention.
    Calls keep positive delta; puts become negative via put-call parity."""
    if leg.option_type == "put":
        return leg.delta - 1.0
    return leg.delta


def _zebra_metrics(pos) -> dict:
    n = pos.notes
    long_leg = pos.legs[0]
    short_leg = pos.legs[2]
    debit = n["debit"]
    return {
        "structure_label": "ZEBRA (Zero Extrinsic Back Ratio)",
        "rows": [
            ("Long  call  ITM",  "+2", long_leg.strike, _display_delta(long_leg), long_leg.price),
            ("Short call  ATM",  "-1", short_leg.strike, _display_delta(short_leg), short_leg.price),
        ],
        "summary": [
            ("Net debit (per ZEBRA)", f"${debit:.2f}"),
            ("Capital outlay / contract", f"${debit*100:.0f}"),
            ("Capital efficiency", f"{n['capital_efficiency']*100:.1f}% of stock cost"),
            ("Max loss (defined risk)", f"${debit*100:.0f} — only if spot < ${long_leg.strike:.2f} at expiry"),
            ("Net entry delta", f"{n['entry_delta']:+.2f} (≈ stock-equiv + gamma kicker)"),
            ("Extrinsic cushion", f"${n['extrinsic_cushion']:+.2f} ({'PASS' if n['extrinsic_cushion'] >= 0 else 'FAIL'})"),
        ],
        "sizing": "Capital outlay = 5–10% of book equity per ZEBRA position.",
    }


def _inverted_fly_metrics(pos) -> dict:
    """Inverted fly: long ATM call+put, short OTM wings.
    Pays debit; profits on large move either direction.
    Max loss = debit (occurs at center). Max profit per side = wing − debit.
    """
    n = pos.notes
    long_call, short_call_wing, long_put, short_put_wing = pos.legs
    debit = -pos.entry_credit  # entry_credit is negative for IF
    wing = n["wing_width"]
    K = n["center_k"]
    max_profit_per_side = wing - debit
    breakeven_up = K + debit
    breakeven_dn = K - debit
    return {
        "structure_label": "Inverted Fly (long-vol; profits on big move)",
        "rows": [
            ("Long  call  ATM",  "+1", long_call.strike, _display_delta(long_call), long_call.price),
            ("Short call  wing", "-1", short_call_wing.strike, _display_delta(short_call_wing), short_call_wing.price),
            ("Long  put   ATM",  "+1", long_put.strike, _display_delta(long_put), long_put.price),
            ("Short put   wing", "-1", short_put_wing.strike, _display_delta(short_put_wing), short_put_wing.price),
        ],
        "summary": [
            ("Net debit (per IF)", f"${debit:.2f}"),
            ("Capital outlay / contract", f"${debit*100:.0f}"),
            ("Wing width", f"${wing:.2f}"),
            ("Max loss (defined risk)", f"${debit*100:.0f} — at center ${K:.2f} at expiry"),
            ("Max profit per side", f"${max_profit_per_side*100:.0f} — at or beyond wings"),
            ("Breakeven down", f"${breakeven_dn:.2f}"),
            ("Breakeven up", f"${breakeven_up:.2f}"),
        ],
        "sizing": "1 contract per intended risk slot (max loss = debit). Plan: 50% mgd-exit on big-move wins.",
    }


def _vertical_metrics(pos, kind: str) -> dict:
    """bull_put or bear_call — 2-leg credit vertical."""
    n = pos.notes
    short_leg, long_leg = pos.legs  # convention: [short, long]
    credit = pos.entry_credit
    wing = n["wing_width"]
    max_loss = wing - credit
    if kind == "bull_put":
        breakeven = short_leg.strike - credit
        be_label = "Breakeven (price floor)"
        side_label = "put credit spread (bullish)"
        leg_label_short = "Short put"
        leg_label_long = "Long  put"
    else:  # bear_call
        breakeven = short_leg.strike + credit
        be_label = "Breakeven (price ceiling)"
        side_label = "call credit spread (bearish)"
        leg_label_short = "Short call"
        leg_label_long = "Long  call"

    return {
        "structure_label": f"{kind.replace('_', ' ').title()} — {side_label}",
        "rows": [
            (leg_label_short, "-1", short_leg.strike, _display_delta(short_leg), short_leg.price),
            (leg_label_long,  "+1", long_leg.strike,  _display_delta(long_leg),  long_leg.price),
        ],
        "summary": [
            ("Net credit", f"${credit:.2f}  (max profit per contract = ${credit*100:.0f})"),
            ("Wing width", f"${wing:.2f}"),
            ("Max loss (defined risk)", f"${max_loss*100:.0f}"),
            ("Credit / width ratio", f"{credit/wing:.2f}  ({'PASS — meets 0.50 loss-cap' if credit/wing >= 0.50 else 'FAIL — below 0.50 floor'})"),
            (be_label, f"${breakeven:.2f}"),
        ],
        "sizing": "Per loss-cap rule: realized loss ≤ 2× target win. Skip if credit/width < 0.50.",
    }


def _metrics_for(pos, structure: str) -> dict:
    if structure.startswith("zebra"):
        return _zebra_metrics(pos)
    if structure.startswith("inverted_fly"):
        return _inverted_fly_metrics(pos)
    if structure.startswith("bull_put"):
        return _vertical_metrics(pos, "bull_put")
    if structure.startswith("bear_call"):
        return _vertical_metrics(pos, "bear_call")
    raise ValueError(f"unknown structure {structure!r}")


# ─── Render: text + html ──────────────────────────────────────────────

def _render_text(symbol: str, structure: str, expiry: str, spot: float, m: dict) -> str:
    lines = [
        f"  {m['structure_label']} — {symbol} (spot ${spot:.2f}, expiration {expiry})",
        "",
        f"    {'LEG':<18} {'QTY':>4}  {'STRIKE':>7}  {'DELTA':>6}   {'PRICE':>6}",
    ]
    for leg, qty, strike, delta, price in m["rows"]:
        lines.append(
            f"    {leg:<18} {qty:>4}  ${strike:>6.2f}  {delta:>+6.2f}   ${price:>5.2f}"
        )
    lines.append("")
    for label, value in m["summary"]:
        lines.append(f"    {label:<28} {value}")
    lines.append("")
    lines.append(f"    Sizing: {m['sizing']}")
    return "\n".join(lines)


def _render_html(symbol: str, structure: str, expiry: str, spot: float, m: dict) -> str:
    legs_html = "".join(
        f"<tr><td>{leg}</td><td align=center>{qty}</td>"
        f"<td align=right>${strike:.2f}</td>"
        f"<td align=right>{delta:+.2f}</td>"
        f"<td align=right>${price:.2f}</td></tr>"
        for (leg, qty, strike, delta, price) in m["rows"]
    )
    summary_html = "".join(
        f"<tr><td>{label}</td><td>{value}</td></tr>"
        for (label, value) in m["summary"]
    )
    return f"""
<div style="font-family:Menlo,Consolas,monospace;border:1px solid #ccc;padding:10px;margin:8px 0;background:#fafafa">
  <div style="font-weight:bold;margin-bottom:4px">{m['structure_label']} — {symbol}</div>
  <div style="color:#555;margin-bottom:6px">spot ${spot:.2f} · expiration {expiry}</div>
  <table style="border-collapse:collapse;font-size:13px;margin-bottom:6px">
    <thead><tr style="background:#eee">
      <th align=left style="padding:2px 8px">LEG</th>
      <th style="padding:2px 8px">QTY</th>
      <th style="padding:2px 8px">STRIKE</th>
      <th style="padding:2px 8px">DELTA</th>
      <th style="padding:2px 8px">PRICE</th>
    </tr></thead>
    <tbody>{legs_html}</tbody>
  </table>
  <table style="border-collapse:collapse;font-size:13px">
    <tbody>{summary_html}</tbody>
  </table>
  <div style="font-size:12px;color:#666;margin-top:6px">Sizing: {m['sizing']}</div>
</div>
"""


# ─── Public entry point ───────────────────────────────────────────────

def build_construction_block(
    symbol: str, structure: str, expiry: str,
) -> dict:
    """Pull live Schwab chain, construct a position, render text + html.

    Returns a dict:
      {ok: bool, text: str, html: str, error: str | None}

    Never raises — alert should not fail because one chain fetch failed.
    """
    opener = STRUCTURE_TO_OPENER.get(structure)
    if opener is None:
        return {"ok": False, "text": "", "html": "", "error": f"unknown structure {structure!r}"}

    try:
        chain, spot = fetch_chain_with_greeks(symbol, expiry)
    except Exception as e:
        return {"ok": False, "text": "", "html": "",
                "error": f"Schwab chain fetch failed for {symbol}: {e}"}
    if chain is None or chain.empty:
        return {"ok": False, "text": "", "html": "",
                "error": f"empty Schwab chain for {symbol} @ {expiry}"}

    try:
        pos = opener(chain, pd.Timestamp.today(), pd.Timestamp(expiry))
    except Exception as e:
        return {"ok": False, "text": "", "html": "",
                "error": f"{structure} construction error for {symbol}: {e}"}

    if pos is None:
        return {"ok": False, "text": "", "html": "",
                "error": f"{structure} could not be constructed for {symbol} (no qualifying strikes)"}

    try:
        m = _metrics_for(pos, structure)
        text = _render_text(symbol, structure, expiry, spot, m)
        html = _render_html(symbol, structure, expiry, spot, m)
        return {"ok": True, "text": text, "html": html, "error": None}
    except Exception as e:
        return {"ok": False, "text": "", "html": "",
                "error": f"render error for {symbol} {structure}: {e}"}


# ─── CLI for ad-hoc preview ───────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Preview a construction block.")
    ap.add_argument("symbol")
    ap.add_argument("structure", choices=list(STRUCTURE_TO_OPENER.keys()))
    ap.add_argument("expiry", help="YYYY-MM-DD")
    ap.add_argument("--html", action="store_true", help="emit HTML instead of text")
    args = ap.parse_args()

    result = build_construction_block(args.symbol, args.structure, args.expiry)
    if not result["ok"]:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    print(result["html"] if args.html else result["text"])
