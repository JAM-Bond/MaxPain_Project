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
from lib.opex_calendar import third_friday  # noqa: E402
from scripts.monitor.moneyness_lookup import recommended_short_delta, recommended_if_wing  # noqa: E402
from scripts.qualifier import gate_config as G  # noqa: E402
from structures import (  # noqa: E402
    open_zebra, open_inverted_fly, open_bull_put, open_bear_call,
    open_bull_put_mp,
)
import config as _bt_config  # noqa: E402

# Match the backtest's effective pricing for SELECTION. The backtest's
# 95% fire rate + 1.05-1.15 entry-delta range (per project_zebra_findings)
# was achieved with ORATS-clean mids; live bidask/slip pricing is
# meaningfully more conservative on names with wide spreads (e.g. KRE
# 75-DTE deep ITM has $2.50 spreads = 38% of mid), which can push
# selection to deeper-ITM strikes than the validated cohort.
#
# Decision: select on mid (so live picks match validated selections),
# display real bid/ask in the construction block so user sees actual
# execution slippage.
_bt_config.activate_v2()  # PRICING_MODE = "mid"


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
    # bull_put_mp routes through a per-call wrapper that loads max_pain
    # from live_snapshots — see _open_bull_put_mp_route in build_construction_block.
}


def _load_max_pain(symbol: str, expiry: str) -> Optional[float]:
    """Latest max_pain from live_snapshots for (symbol, opex_date=expiry)."""
    import sqlite3
    db_path = Path.home() / "Metal_Project/data/shared/metal_project.db"
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT max_pain FROM live_snapshots "
                "WHERE symbol = ? AND opex_date = ? AND max_pain IS NOT NULL "
                "ORDER BY snapshot_date DESC LIMIT 1",
                (symbol, expiry),
            ).fetchone()
    except Exception:
        return None
    return float(row[0]) if row and row[0] is not None else None


# ─── Risk metrics per structure ───────────────────────────────────────

def _display_delta(leg) -> float:
    """Convert engine call-delta to standard trader convention.
    Calls keep positive delta; puts become negative via put-call parity."""
    if leg.option_type == "put":
        return leg.delta - 1.0
    return leg.delta


def _leg_bidask(chain, leg) -> tuple[float, float]:
    """Look up (bid, ask) for a Leg in the chain DataFrame.
    Returns (0.0, 0.0) if no matching strike row found."""
    try:
        match = chain[chain["strike"] == leg.strike]
        if match.empty:
            return 0.0, 0.0
        row = match.iloc[0]
        if leg.option_type == "call":
            return float(row.get("cBidPx", 0) or 0), float(row.get("cAskPx", 0) or 0)
        else:
            return float(row.get("pBidPx", 0) or 0), float(row.get("pAskPx", 0) or 0)
    except Exception:
        return 0.0, 0.0


# Heuristics for recommended limit prices (patient-trader defaults, 2026-05-06).
# Rule: always sit on the favorable side of mid. Trade fill speed for fill
# quality. On WIDE bid-ask names (flagged separately), be more aggressive or
# skip — the patient limit may not fill there.
_LIMIT_SLIP_CREDIT = 0.05    # selling premium → ask ≥ mid + $0.05
_LIMIT_SLIP_DEBIT = 0.05     # buying premium → bid ≤ mid − $0.05
_WIDE_BIDASK_RATIO = 0.20    # flag if any leg's (ask-bid)/mid > 20%


def _liquidity_flag(legs_bidask: list[tuple[float, float, float]]) -> str | None:
    """Given list of (bid, mid, ask), return a warning string if any leg's
    bid-ask spread exceeds the WIDE threshold, else None."""
    worst = 0.0
    for bid, mid, ask in legs_bidask:
        if mid <= 0 or bid <= 0 or ask <= 0:
            continue
        ratio = (ask - bid) / mid
        if ratio > worst:
            worst = ratio
    if worst > _WIDE_BIDASK_RATIO:
        return f"WIDE BID-ASK ({worst*100:.0f}% of mid worst leg) — fill will be well below mid"
    return None


def _zebra_metrics(pos, chain=None) -> dict:
    n = pos.notes
    long_leg = pos.legs[0]
    short_leg = pos.legs[2]
    debit = n["debit"]

    long_bid, long_ask = _leg_bidask(chain, long_leg) if chain is not None else (0.0, 0.0)
    short_bid, short_ask = _leg_bidask(chain, short_leg) if chain is not None else (0.0, 0.0)

    # Natural-worst debit: pay ask on the 2 longs, receive bid on the 1 short
    natural_debit = (2 * long_ask) - short_bid if long_ask and short_bid else None
    limit_debit = debit - _LIMIT_SLIP_DEBIT

    range_block = []
    if natural_debit is not None:
        range_block = [
            ("─── Tradeable range ───", ""),
            ("Mid debit (theoretical)", f"${debit:.2f}"),
            ("Recommended limit DEBIT", f"≤ ${limit_debit:.2f}  (mid − ${_LIMIT_SLIP_DEBIT:.2f} — patient buyer)"),
            ("Natural worst (ask×2 − bid)", f"${natural_debit:.2f}  ← walk away above this"),
        ]
    legs_ba = [(long_bid, long_leg.price, long_ask),
               (short_bid, short_leg.price, short_ask)]
    liq_warn = _liquidity_flag(legs_ba)

    return {
        "structure_label": "ZEBRA (Zero Extrinsic Back Ratio)",
        "rows": [
            ("Long  call  ITM",  "+2", long_leg.strike, _display_delta(long_leg), long_bid, long_leg.price, long_ask),
            ("Short call  ATM",  "-1", short_leg.strike, _display_delta(short_leg), short_bid, short_leg.price, short_ask),
        ],
        "summary": [
            ("Net debit (per ZEBRA)", f"${debit:.2f}"),
            ("Capital outlay / contract", f"${debit*100:.0f}"),
            ("Capital efficiency", f"{n['capital_efficiency']*100:.1f}% of stock cost"),
            ("Max loss (defined risk)", f"${debit*100:.0f} — only if spot < ${long_leg.strike:.2f} at expiry"),
            ("Net entry delta", f"{n['entry_delta']:+.2f} (≈ stock-equiv + gamma kicker)"),
            ("Extrinsic cushion", f"${n['extrinsic_cushion']:+.2f} ({'PASS' if n['extrinsic_cushion'] >= 0 else 'FAIL'})"),
        ] + range_block,
        "sizing": "Capital outlay = 5–10% of book equity per ZEBRA position.",
        "liquidity_warning": liq_warn,
    }


def _inverted_fly_metrics(pos, chain=None) -> dict:
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

    # Bid/ask lookup
    lc_bid, lc_ask = _leg_bidask(chain, long_call) if chain is not None else (0.0, 0.0)
    sc_bid, sc_ask = _leg_bidask(chain, short_call_wing) if chain is not None else (0.0, 0.0)
    lp_bid, lp_ask = _leg_bidask(chain, long_put) if chain is not None else (0.0, 0.0)
    sp_bid, sp_ask = _leg_bidask(chain, short_put_wing) if chain is not None else (0.0, 0.0)

    # Natural-worst debit: pay ask on longs, receive bid on shorts
    if all((lc_ask, sc_bid, lp_ask, sp_bid)):
        natural_debit = (lc_ask + lp_ask) - (sc_bid + sp_bid)
    else:
        natural_debit = None
    limit_debit = debit - _LIMIT_SLIP_DEBIT

    range_block = []
    if natural_debit is not None:
        range_block = [
            ("─── Tradeable range ───", ""),
            ("Mid debit (theoretical)", f"${debit:.2f}"),
            ("Recommended limit DEBIT", f"≤ ${limit_debit:.2f}  (mid − ${_LIMIT_SLIP_DEBIT:.2f} — patient buyer)"),
            ("Natural worst (longs@ask − shorts@bid)", f"${natural_debit:.2f}  ← walk away above this"),
        ]
    legs_ba = [(lc_bid, long_call.price, lc_ask), (sc_bid, short_call_wing.price, sc_ask),
               (lp_bid, long_put.price, lp_ask), (sp_bid, short_put_wing.price, sp_ask)]
    liq_warn = _liquidity_flag(legs_ba)

    return {
        "structure_label": "Inverted Fly (long-vol; profits on big move)",
        "rows": [
            ("Long  call  ATM",  "+1", long_call.strike, _display_delta(long_call), lc_bid, long_call.price, lc_ask),
            ("Short call  wing", "-1", short_call_wing.strike, _display_delta(short_call_wing), sc_bid, short_call_wing.price, sc_ask),
            ("Long  put   ATM",  "+1", long_put.strike, _display_delta(long_put), lp_bid, long_put.price, lp_ask),
            ("Short put   wing", "-1", short_put_wing.strike, _display_delta(short_put_wing), sp_bid, short_put_wing.price, sp_ask),
        ],
        "summary": [
            ("Net debit (per IF)", f"${debit:.2f}"),
            ("Capital outlay / contract", f"${debit*100:.0f}"),
            ("Wing width", f"${wing:.2f}"),
            ("Max loss (defined risk)", f"${debit*100:.0f} — at center ${K:.2f} at expiry"),
            ("Max profit per side", f"${max_profit_per_side*100:.0f} — at or beyond wings"),
            ("Breakeven down", f"${breakeven_dn:.2f}"),
            ("Breakeven up", f"${breakeven_up:.2f}"),
        ] + range_block,
        "sizing": "1 contract per intended risk slot (max loss = debit). Plan: 50% mgd-exit on big-move wins.",
        "liquidity_warning": liq_warn,
    }


def _vertical_metrics(pos, kind: str, chain=None) -> dict:
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

    # Bid/ask lookup + tradeable-range computation
    short_bid, short_ask = _leg_bidask(chain, short_leg) if chain is not None else (0.0, 0.0)
    long_bid, long_ask = _leg_bidask(chain, long_leg) if chain is not None else (0.0, 0.0)
    # Natural-worst credit: sell short@bid, buy long@ask
    if all((short_bid, long_ask)):
        natural_credit = short_bid - long_ask
    else:
        natural_credit = None
    limit_credit = credit + _LIMIT_SLIP_CREDIT
    floor_credit = G.MIN_CREDIT_WIDTH * wing  # the C/W floor in $ terms

    range_block = []
    if natural_credit is not None:
        natural_note = " ← if this is below the C/W floor, fill won't pass framework" \
            if natural_credit < floor_credit else ""
        range_block = [
            ("─── Tradeable range ───", ""),
            ("Mid credit (theoretical)", f"${credit:.2f}"),
            ("Recommended limit CREDIT", f"≥ ${limit_credit:.2f}  (mid + ${_LIMIT_SLIP_CREDIT:.2f} — patient seller)"),
            ("C/W floor credit", f"${floor_credit:.2f}  ({G.MIN_CREDIT_WIDTH:.2f} × ${wing:.2f} wing)"),
            ("Natural worst (short@bid − long@ask)", f"${natural_credit:.2f}{natural_note}"),
        ]
    legs_ba = [(short_bid, short_leg.price, short_ask), (long_bid, long_leg.price, long_ask)]
    liq_warn = _liquidity_flag(legs_ba)

    return {
        "structure_label": f"{kind.replace('_', ' ').title()} — {side_label}",
        "rows": [
            (leg_label_short, "-1", short_leg.strike, _display_delta(short_leg), short_bid, short_leg.price, short_ask),
            (leg_label_long,  "+1", long_leg.strike,  _display_delta(long_leg),  long_bid, long_leg.price, long_ask),
        ],
        "summary": [
            ("Net credit", f"${credit:.2f}  (max profit per contract = ${credit*100:.0f})"),
            ("Wing width", f"${wing:.2f}"),
            ("Max loss (defined risk)", f"${max_loss*100:.0f}"),
            ("Credit / width ratio",
             f"{credit/wing:.2f}  ("
             f"{'PASS — meets ' + f'{G.MIN_CREDIT_WIDTH:.2f}' + ' loss-cap floor' if credit/wing >= G.MIN_CREDIT_WIDTH else 'FAIL — below ' + f'{G.MIN_CREDIT_WIDTH:.2f}' + ' floor'})"),
            (be_label, f"${breakeven:.2f}"),
        ] + range_block,
        "sizing": (f"Per loss-cap rule: realized loss ≤ 2× target win. "
                    f"Skip if credit/width < {G.MIN_CREDIT_WIDTH:.2f}."),
        "liquidity_warning": liq_warn,
    }


def _metrics_for(pos, structure: str, chain=None) -> dict:
    if structure.startswith("zebra"):
        return _zebra_metrics(pos, chain)
    if structure.startswith("inverted_fly"):
        return _inverted_fly_metrics(pos, chain)
    if structure.startswith("bull_put"):
        return _vertical_metrics(pos, "bull_put", chain)
    if structure.startswith("bear_call"):
        return _vertical_metrics(pos, "bear_call", chain)
    raise ValueError(f"unknown structure {structure!r}")


# ─── Render: text + html ──────────────────────────────────────────────

def _render_text(symbol: str, structure: str, expiry: str, spot: float, m: dict) -> str:
    lines = [
        f"  {m['structure_label']} — {symbol} (spot ${spot:.2f}, expiration {expiry})",
        "",
        f"    {'LEG':<18} {'QTY':>4}  {'STRIKE':>7}  {'DELTA':>6}   {'BID':>6}  {'MID':>6}  {'ASK':>6}",
    ]
    for leg, qty, strike, delta, bid, mid, ask in m["rows"]:
        bid_str = f"${bid:>5.2f}" if bid > 0 else "   n/a"
        ask_str = f"${ask:>5.2f}" if ask > 0 else "   n/a"
        lines.append(
            f"    {leg:<18} {qty:>4}  ${strike:>6.2f}  {delta:>+6.2f}   {bid_str:>6}  ${mid:>5.2f}  {ask_str:>6}"
        )
    lines.append("")
    for label, value in m["summary"]:
        if label.startswith("───"):
            lines.append(f"    {label}")
        else:
            lines.append(f"    {label:<40} {value}")
    if m.get("liquidity_warning"):
        lines.append("")
        lines.append(f"    🚨 {m['liquidity_warning']}")
    lines.append("")
    lines.append(f"    Sizing: {m['sizing']}")
    return "\n".join(lines)


def _render_html(symbol: str, structure: str, expiry: str, spot: float, m: dict) -> str:
    def _fmt_price(p):
        return f"${p:.2f}" if p > 0 else "n/a"
    legs_html = "".join(
        f"<tr><td>{leg}</td><td align=center>{qty}</td>"
        f"<td align=right>${strike:.2f}</td>"
        f"<td align=right>{delta:+.2f}</td>"
        f"<td align=right style='color:#888'>{_fmt_price(bid)}</td>"
        f"<td align=right><b>${mid:.2f}</b></td>"
        f"<td align=right style='color:#888'>{_fmt_price(ask)}</td></tr>"
        for (leg, qty, strike, delta, bid, mid, ask) in m["rows"]
    )
    def _fmt_summary_row(label, value):
        if label.startswith("───"):
            return (f"<tr><td colspan=2 style='padding-top:6px;color:#666;"
                    f"font-size:11px;border-top:1px solid #ddd'>{label.replace('───','').strip()}</td></tr>")
        return f"<tr><td>{label}</td><td>{value}</td></tr>"
    summary_html = "".join(_fmt_summary_row(l, v) for (l, v) in m["summary"])
    liq_html = ""
    if m.get("liquidity_warning"):
        liq_html = (f"<div style='font-size:12px;color:#a02020;margin-top:6px;"
                    f"font-weight:bold'>🚨 {m['liquidity_warning']}</div>")
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
      <th style="padding:2px 8px">BID</th>
      <th style="padding:2px 8px">MID</th>
      <th style="padding:2px 8px">ASK</th>
    </tr></thead>
    <tbody>{legs_html}</tbody>
  </table>
  <table style="border-collapse:collapse;font-size:13px">
    <tbody>{summary_html}</tbody>
  </table>
  {liq_html}
  <div style="font-size:12px;color:#666;margin-top:6px">Sizing: {m['sizing']}</div>
</div>
"""


# ─── Public entry point ───────────────────────────────────────────────

def _moneyness_annotation(rec) -> tuple[str, str]:
    """Return (text_line, html_line) describing the per-ticker moneyness pick."""
    if rec.is_default:
        text = f"    Moneyness: {rec.label} (default — no walk-forward advantage)"
        html = (f"<div style='font-size:12px;color:#888;margin-top:4px'>"
                f"Moneyness: <b>{rec.label}</b> (default — no walk-forward advantage)</div>")
        return text, html
    p_str = f"train p={rec.train_p:.4f} (n={rec.train_n}), val p={rec.val_p:.4f} (n={rec.val_n})"
    text = (f"    Moneyness: {rec.label} (per-ticker walk-forward: {rec.evidence_pair} → "
            f"{rec.label} wins; {p_str})")
    html = (f"<div style='font-size:12px;color:#1a6b1a;margin-top:4px'>"
            f"Moneyness: <b>{rec.label}</b> (walk-forward validated: "
            f"<i>{rec.evidence_pair}</i> → {rec.label} wins; {p_str})</div>")
    return text, html


def _ma_bucket_annotation(symbol: str) -> Optional[tuple[str, str]]:
    """Construction-block sibling of the qualifier's Rule #1 DOWNSIZE gate.

    For bull_put* structures: when spot is below the 200-DMA by more than
    G.BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD (default -10%), render a warning
    line surfacing the MA bucket inside the construction block. Silent
    otherwise so the alert stays scannable.

    Returns None when MA history is unavailable or the warning doesn't apply.
    """
    from scripts.qualifier.cycle_qualifier import bull_put_ma_pct
    try:
        ma_pct = bull_put_ma_pct(symbol)
    except Exception:
        return None
    if ma_pct is None:
        return None
    threshold = G.BULL_PUT_BELOW_MA_DOWNSIZE_THRESHOLD
    if ma_pct >= threshold:
        return None
    bucket = f"BELOW_{int(abs(threshold)*100)}PCT"
    text = (f"    MA bucket: {bucket} — spot {ma_pct*100:+.1f}% vs 200-DMA "
            f"(Rule #1 DOWNSIZE: half-size)")
    html = (f"<div style='font-size:12px;color:#a06400;margin-top:4px'>"
            f"⚠ MA bucket: <b>{bucket}</b> — spot {ma_pct*100:+.1f}% vs 200-DMA "
            f"(Rule #1 DOWNSIZE: half-size)</div>")
    return text, html


def _if_wing_annotation(rec) -> tuple[str, str]:
    """Return (text_line, html_line) describing the per-ticker IF wing pick."""
    pct_label = f"{rec.wing_pct*100:.0f}%"
    if rec.is_default:
        text = f"    Wing width: {rec.label} ({pct_label} of spot, default — no walk-forward advantage)"
        html = (f"<div style='font-size:12px;color:#888;margin-top:4px'>"
                f"Wing width: <b>{rec.label}</b> ({pct_label} of spot, default — no walk-forward advantage)</div>")
        return text, html
    p_str = f"train p={rec.train_p:.4f} (n={rec.train_n}), val p={rec.val_p:.4f} (n={rec.val_n})"
    text = (f"    Wing width: {rec.label} ({pct_label} of spot — per-ticker walk-forward: "
            f"{rec.evidence_pair} → {rec.label} wins; {p_str})")
    html = (f"<div style='font-size:12px;color:#1a6b1a;margin-top:4px'>"
            f"Wing width: <b>{rec.label}</b> ({pct_label} of spot — walk-forward validated: "
            f"<i>{rec.evidence_pair}</i> → {rec.label} wins; {p_str})</div>")
    return text, html


def build_construction_block(
    symbol: str, structure: str, expiry: str,
) -> dict:
    """Pull live Schwab chain, construct a position, render text + html.

    For bull_put / bear_call structures, applies the per-ticker walk-forward-
    validated moneyness recommendation (mgd50 exit, since that's what the
    framework actually trades). Falls back to OTM 0.30 default for tickers
    without a validated recommendation.

    Returns a dict:
      {ok: bool, text: str, html: str, error: str | None}

    Never raises — alert should not fail because one chain fetch failed.
    """
    is_mp_anchored = structure == "bull_put_mp"
    if is_mp_anchored:
        opener = open_bull_put_mp
    else:
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

    # Per-ticker moneyness pick for delta-anchored vertical structures.
    # bull_put_mp uses MP-anchored strike selection so the delta rec doesn't
    # apply. ZEBRA uses its own selection logic. IF uses BFLY_WING_PCT_SPOT.
    rec = None
    if_wing_rec = None
    mp_value = None
    if is_mp_anchored:
        mp_value = _load_max_pain(symbol, expiry)
        if mp_value is None:
            return {"ok": False, "text": "", "html": "",
                    "error": f"bull_put_mp: max_pain unavailable in live_snapshots for {symbol} @ {expiry}"}
    elif structure.startswith("bull_put") or structure.startswith("bear_call"):
        rec = recommended_short_delta(symbol, structure, exit_rule="mgd50")
        _bt_config.VERTICAL_SHORT_DELTA = rec.short_delta
    elif structure.startswith("inverted_fly"):
        if_wing_rec = recommended_if_wing(symbol)
        _bt_config.BFLY_WING_PCT_SPOT = if_wing_rec.wing_pct

    try:
        if is_mp_anchored:
            pos = opener(chain, pd.Timestamp.today(), pd.Timestamp(expiry),
                         max_pain=mp_value)
        else:
            pos = opener(chain, pd.Timestamp.today(), pd.Timestamp(expiry))
    except Exception as e:
        return {"ok": False, "text": "", "html": "",
                "error": f"{structure} construction error for {symbol}: {e}"}

    if pos is None:
        if is_mp_anchored and spot < mp_value:
            err = (f"bull_put_mp: spot ${spot:.2f} < MP ${mp_value:.2f} — "
                   f"Phase 2c entry rule SKIPPED for {symbol}")
        else:
            err = f"{structure} could not be constructed for {symbol} (no qualifying strikes)"
        return {"ok": False, "text": "", "html": "", "error": err}

    try:
        m = _metrics_for(pos, structure, chain)
        if is_mp_anchored:
            m["structure_label"] = (
                f"Bull Put (MP-anchored, T-5) — put credit spread "
                f"@ MP ${mp_value:.2f}, paper-test"
            )
        text = _render_text(symbol, structure, expiry, spot, m)
        html = _render_html(symbol, structure, expiry, spot, m)
        if rec is not None:
            ann_text, ann_html = _moneyness_annotation(rec)
            text = text + "\n" + ann_text
            html = html.replace(
                "<div style=\"font-size:12px;color:#666;margin-top:6px\">Sizing:",
                ann_html + "\n  <div style=\"font-size:12px;color:#666;margin-top:6px\">Sizing:",
            )
        if if_wing_rec is not None:
            ann_text, ann_html = _if_wing_annotation(if_wing_rec)
            text = text + "\n" + ann_text
            html = html.replace(
                "<div style=\"font-size:12px;color:#666;margin-top:6px\">Sizing:",
                ann_html + "\n  <div style=\"font-size:12px;color:#666;margin-top:6px\">Sizing:",
            )
        if structure.startswith("bull_put") and not is_mp_anchored:
            ma_ann = _ma_bucket_annotation(symbol)
            if ma_ann is not None:
                ma_text, ma_html = ma_ann
                text = text + "\n" + ma_text
                html = html.replace(
                    "<div style=\"font-size:12px;color:#666;margin-top:6px\">Sizing:",
                    ma_html + "\n  <div style=\"font-size:12px;color:#666;margin-top:6px\">Sizing:",
                )
        return {"ok": True, "text": text, "html": html, "error": None}
    except Exception as e:
        return {"ok": False, "text": "", "html": "",
                "error": f"render error for {symbol} {structure}: {e}"}


# ─── ZEBRA-protected variant (multi-expiration) ───────────────────────

def _prior_monthly_opex(zebra_expiry: str) -> str:
    """Calendar-month-back from ZEBRA expiry, snap to that month's third Friday.
    Used as the hedge-put expiration for zebra_protected."""
    from datetime import datetime
    z = datetime.strptime(zebra_expiry, "%Y-%m-%d").date()
    prev_y = z.year if z.month > 1 else z.year - 1
    prev_m = z.month - 1 if z.month > 1 else 12
    return third_friday(prev_y, prev_m).isoformat()


def _pick_put_at_strike(chain, target_strike: float):
    """Pick the put row with strike closest to target. Requires valid pBidPx/pAskPx."""
    sub = chain.dropna(subset=["pBidPx", "pAskPx"])
    sub = sub[sub["pBidPx"] > 0]
    if sub.empty:
        return None
    idx = (sub["strike"] - target_strike).abs().idxmin()
    row = sub.loc[idx]
    return row if abs(row["strike"] - target_strike) <= 1.0 else None


def build_zebra_protected_block(symbol: str, zebra_expiry: str) -> dict:
    """ZEBRA + ATM long put at the prior monthly OpEx — downside hedge.

    The protective put expires ~30 days before the ZEBRA, capping downside
    during the early life of the trade. After the put expires (or is closed
    at T-10 per the framework's exit rule for residual value), the position
    reverts to a base ZEBRA for its final ~4 weeks.

    Strike = short-call strike (ATM at entry, matches the user's TOS hedge
    convention). Returns same {ok, text, html, error} shape as the base
    construction blocks.
    """
    # 1. Build base ZEBRA on the JUL chain
    zebra_chain, zebra_spot = fetch_chain_with_greeks(symbol, zebra_expiry)
    if zebra_chain is None or zebra_chain.empty:
        return {"ok": False, "text": "", "html": "",
                "error": f"empty ZEBRA chain for {symbol} @ {zebra_expiry}"}
    zebra_pos = open_zebra(zebra_chain, pd.Timestamp.today(), pd.Timestamp(zebra_expiry))
    if zebra_pos is None:
        return {"ok": False, "text": "", "html": "",
                "error": f"ZEBRA construction failed for {symbol}"}

    # 2. Hedge-put expiration = prior monthly OpEx
    hedge_expiry = _prior_monthly_opex(zebra_expiry)

    # 3. Fetch hedge chain + pick ATM put at short-call strike
    hedge_chain, _ = fetch_chain_with_greeks(symbol, hedge_expiry)
    if hedge_chain is None or hedge_chain.empty:
        return {"ok": False, "text": "", "html": "",
                "error": f"empty hedge chain for {symbol} @ {hedge_expiry}"}

    short_call_leg = zebra_pos.legs[2]
    put_row = _pick_put_at_strike(hedge_chain, short_call_leg.strike)
    if put_row is None:
        return {"ok": False, "text": "", "html": "",
                "error": f"no qualifying ATM put at ${short_call_leg.strike:.0f} on {hedge_expiry}"}

    put_strike = float(put_row["strike"])
    put_mid = float((put_row["pBidPx"] + put_row["pAskPx"]) / 2.0)
    put_delta_call_convention = float(put_row.get("delta", float("nan")))  # call delta at strike
    # Convert to put delta (standard trader convention: negative)
    put_delta = put_delta_call_convention - 1.0 if not pd.isna(put_delta_call_convention) else float("nan")

    # 4. Combined metrics
    long_leg = zebra_pos.legs[0]
    zebra_debit = zebra_pos.notes["debit"]
    total_debit = zebra_debit + put_mid
    zebra_net_delta = zebra_pos.notes["entry_delta"]
    combined_net_delta = zebra_net_delta + put_delta

    # 5. Render
    rows_text = [
        ("Long  call  ITM",  "+2", long_leg.strike, _display_delta(long_leg), long_leg.price, zebra_expiry),
        ("Short call  ATM",  "-1", short_call_leg.strike, _display_delta(short_call_leg), short_call_leg.price, zebra_expiry),
        ("Long  put   ATM",  "+1", put_strike, put_delta, put_mid, hedge_expiry),
    ]
    text_lines = [
        f"  ZEBRA-protected — {symbol} (spot ${zebra_spot:.2f})",
        f"    ZEBRA expiration:   {zebra_expiry}  (75-DTE)",
        f"    Hedge expiration:   {hedge_expiry}  (prior monthly OpEx)",
        "",
        f"    {'LEG':<18} {'QTY':>4}  {'STRIKE':>7}  {'DELTA':>6}   {'PRICE':>6}   EXP",
    ]
    for leg, qty, strike, delta, price, exp in rows_text:
        text_lines.append(
            f"    {leg:<18} {qty:>4}  ${strike:>6.2f}  {delta:>+6.2f}   ${price:>5.2f}   {exp}"
        )
    text_lines += [
        "",
        f"    Total debit                  ${total_debit:.2f}  (ZEBRA ${zebra_debit:.2f} + put ${put_mid:.2f})",
        f"    Capital outlay / contract    ${total_debit*100:.0f}",
        f"    Initial net delta            {combined_net_delta:+.2f}  (≈ half-stock during hedge window)",
        f"    Net delta after hedge expiry {zebra_net_delta:+.2f}  (full ZEBRA — put expired or closed)",
        f"    ZEBRA structural max loss    ${zebra_debit*100:.0f} — only if spot < ${long_leg.strike:.2f} at expiry",
        f"    Put hedge protection          intrinsic value below ${put_strike:.2f} on {hedge_expiry}",
        "",
        f"    Tactical: close protective put at T-10 from {hedge_expiry} for residual time-value.",
        f"    Sizing: same 5–10% of book equity; total outlay ${total_debit*100:.0f} ≈ {(total_debit/zebra_debit-1)*100:.0f}% premium over base ZEBRA.",
    ]

    # HTML
    legs_html = "".join(
        f"<tr><td>{leg}</td><td align=center>{qty}</td>"
        f"<td align=right>${strike:.2f}</td>"
        f"<td align=right>{delta:+.2f}</td>"
        f"<td align=right>${price:.2f}</td>"
        f"<td align=right style='color:#666;font-size:11px'>{exp}</td></tr>"
        for (leg, qty, strike, delta, price, exp) in rows_text
    )
    summary_html = "".join(
        f"<tr><td>{lab}</td><td>{val}</td></tr>" for lab, val in [
            ("Total debit", f"${total_debit:.2f}  (ZEBRA ${zebra_debit:.2f} + put ${put_mid:.2f})"),
            ("Capital outlay / contract", f"${total_debit*100:.0f}"),
            ("Initial net delta", f"{combined_net_delta:+.2f} (half-stock during hedge)"),
            ("Net delta after hedge expiry", f"{zebra_net_delta:+.2f} (full ZEBRA)"),
            ("ZEBRA structural max loss", f"${zebra_debit*100:.0f} if spot < ${long_leg.strike:.2f} at JUL expiry"),
            ("Tactical exit", f"close protective put at T-10 from {hedge_expiry}"),
        ]
    )
    html = f"""
<div style="font-family:Menlo,Consolas,monospace;border:1px solid #b58900;
            border-left:4px solid #b58900;padding:10px;margin:8px 0;background:#fffaf0">
  <div style="font-weight:bold;margin-bottom:4px">ZEBRA-protected — {symbol}</div>
  <div style="color:#555;margin-bottom:6px">
    spot ${zebra_spot:.2f} · ZEBRA expiry {zebra_expiry} · hedge expiry {hedge_expiry}
  </div>
  <table style="border-collapse:collapse;font-size:13px;margin-bottom:6px">
    <thead><tr style="background:#eee">
      <th align=left style="padding:2px 8px">LEG</th>
      <th style="padding:2px 8px">QTY</th>
      <th style="padding:2px 8px">STRIKE</th>
      <th style="padding:2px 8px">DELTA</th>
      <th style="padding:2px 8px">PRICE</th>
      <th style="padding:2px 8px">EXP</th>
    </tr></thead>
    <tbody>{legs_html}</tbody>
  </table>
  <table style="border-collapse:collapse;font-size:13px"><tbody>{summary_html}</tbody></table>
  <div style="font-size:12px;color:#666;margin-top:6px">
    Hedge premium adds ~{(total_debit/zebra_debit-1)*100:.0f}% to outlay; protects through {hedge_expiry}.
  </div>
</div>
"""
    return {"ok": True, "text": "\n".join(text_lines), "html": html, "error": None}


# ─── CLI for ad-hoc preview ───────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Preview a construction block.")
    ap.add_argument("symbol")
    ap.add_argument("structure",
                    choices=list(STRUCTURE_TO_OPENER.keys()) + ["zebra_protected"])
    ap.add_argument("expiry", help="YYYY-MM-DD")
    ap.add_argument("--html", action="store_true", help="emit HTML instead of text")
    args = ap.parse_args()

    if args.structure == "zebra_protected":
        result = build_zebra_protected_block(args.symbol, args.expiry)
    else:
        result = build_construction_block(args.symbol, args.structure, args.expiry)
    if not result["ok"]:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    print(result["html"] if args.html else result["text"])
