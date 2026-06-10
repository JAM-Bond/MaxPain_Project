"""Daily Macro Brief — reads Agent_Project ChromaDB and renders a compact
multi-section brief for the 4:45 PM ET daily alert.

Sections:
  1. CURVE — latest yield-curve snapshot + spreads vs 30-day average
  2. FEDWATCH — next 4 FOMC meetings, current probabilities + day-over-day shifts
  3. FED NEWS — recent Fed RSS items (last N days)
  4. GEOPOLITICAL — BlackRock BII HIGH/MEDIUM risk dashboard (monthly) + weekly
     commentary, each with a manual-refresh nudge when stale (both are manual
     scrapes in Agent_Project, like FedWatch).

Architecture rule (from project_agent_project_integration_queue.md):
  READ from Agent_Project ChromaDB; never query FRED/CME directly here.
  If Agent_Project's scrapers haven't run yet today, the brief says so
  explicitly rather than papering over with stale-cached data.

Usage:
    from lib.macro_brief import build_macro_brief, render_text, render_html
    brief = build_macro_brief()
    print(render_text(brief))
"""
from __future__ import annotations

import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any

AGENT_ROOT = Path.home() / "Agent_Project"
sys.path.insert(0, str(AGENT_ROOT))
sys.path.insert(0, str(Path.home() / "MaxPain_Project"))  # so `from lib import …` works when run directly


from lib import recession_panel  # noqa: E402


def _client():
    from shared.chromadb_client import DataPipelineChromaDB
    return DataPipelineChromaDB()


def _parse_iso_to_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.rstrip("Z")).date()
    except Exception:
        return None


def _scraped_age(scraped_at: str | None) -> int | None:
    """Return calendar-days-ago for a scraped_at ISO timestamp."""
    d = _parse_iso_to_date(scraped_at)
    if d is None:
        return None
    return (date.today() - d).days


# ─── Curve ─────────────────────────────────────────────────────────────

def get_curve_summary() -> dict[str, Any]:
    """Latest yield-curve snapshot + 30-day average comparison."""
    db = _client()
    cur = db.query_by_metadata("yield_curve_snapshots", {"data_type": "yield_curve_snapshot"})
    if not cur:
        return {"ok": False, "error": "yield_curve_snapshots empty"}
    md_cur = cur["metadatas"][0]

    # Historical, last 30 calendar days. ChromaDB string comparators
    # don't work cleanly with $gte on ISO-date strings; fetch all and
    # filter in Python (collection is ~1,300 rows — trivial cost).
    today = date.today()
    hist = db.get_all_documents("yield_curve_history")
    hist_rows = []
    if hist and hist.get("metadatas"):
        for m in hist["metadatas"]:
            sd = _parse_iso_to_date(m.get("snapshot_date"))
            if sd is None or sd > today:
                continue
            if (today - sd).days > 30:
                continue
            hist_rows.append(m)

    def _avg(field: str) -> float | None:
        vals = [m.get(field) for m in hist_rows if m.get(field) is not None]
        return sum(vals) / len(vals) if vals else None

    avg_2s10s = _avg("spread_2s10s")
    avg_3m10y = _avg("spread_3m10y")
    avg_dgs10 = _avg("yield_DGS10")
    avg_dgs2 = _avg("yield_DGS2")

    return {
        "ok": True,
        "snapshot_date": md_cur.get("snapshot_date"),
        "scraped_at": md_cur.get("scraped_at"),
        "scraped_age_days": _scraped_age(md_cur.get("scraped_at")),
        "dgs10": md_cur.get("yield_DGS10"),
        "dgs2": md_cur.get("yield_DGS2"),
        "spread_2s10s": md_cur.get("spread_2s10s"),
        "spread_3m10y": md_cur.get("spread_3m10y"),
        "is_inverted": md_cur.get("is_inverted"),
        "avg_30d": {
            "spread_2s10s": avg_2s10s,
            "spread_3m10y": avg_3m10y,
            "dgs10": avg_dgs10,
            "dgs2": avg_dgs2,
        },
        "hist_n": len(hist_rows),
    }


# ─── FedWatch ──────────────────────────────────────────────────────────

def get_fedwatch_summary(n_meetings: int = 4) -> dict[str, Any]:
    """Next N FOMC meetings, sorted by meeting date."""
    db = _client()
    # Read the whole dedicated collection — do NOT filter by a hardcoded source tag.
    # The CSV→API migration changed source ("CME_FedWatch_CSV"→"CME_FedWatch_API"), and
    # the stale filter silently matched nothing → "empty" in the alert while history
    # (which uses get_all_documents) kept working. The collection is FedWatch-only and
    # wiped+rebuilt each ingest, so reading all docs is correct + migration-proof.
    res = db.get_all_documents("cme_fedwatch_current")
    if not res or not res.get("metadatas"):
        return {"ok": False, "error": "cme_fedwatch_current empty"}

    rows = []
    for md in res["metadatas"]:
        meeting_str = md.get("meeting_date")
        if not meeting_str:
            continue
        # CME meeting_date format is M/D/YYYY
        try:
            m_dt = datetime.strptime(meeting_str, "%m/%d/%Y").date()
        except ValueError:
            continue
        if m_dt < date.today():
            continue
        rows.append({
            "meeting_date": m_dt,
            "meeting_str": meeting_str,
            "cut": md.get("cut_probability"),
            "hold": md.get("hold_probability"),
            "hike": md.get("hike_probability"),
            "prior_cut": md.get("prior_cut"),
            "prior_hold": md.get("prior_hold"),
            "prior_hike": md.get("prior_hike"),
            "most_likely": md.get("most_likely_action"),
            "scraped_at": md.get("scraped_at"),
        })

    rows.sort(key=lambda r: r["meeting_date"])
    rows = rows[:n_meetings]

    latest_scrape = max((r["scraped_at"] for r in rows if r["scraped_at"]), default=None)
    return {
        "ok": True,
        "meetings": rows,
        "scraped_age_days": _scraped_age(latest_scrape),
    }


def get_fedwatch_trajectory(n_meetings: int = 4, lookback_days: int = 14) -> dict[str, Any]:
    """Repricing VELOCITY per upcoming FOMC meeting, from cme_fedwatch_history.

    get_fedwatch_summary shows only the day-over-day shift vs the single prior
    upload. This reads the accumulating ChromaDB time series and measures how far
    cut/hold/hike probabilities have moved over ~`lookback_days` — the multi-week
    repricing trajectory that is the frontrunning / regime-fragility tell (e.g.
    the 16%→34% hike-odds move over a week in May 2026).

    Manual-upload cadence is ~2x/week (median 3-day gap), so a 14-day window
    typically spans ~4-5 snapshots. The actual span + snapshot count are returned
    so the reader knows whether a "trajectory" rests on enough points.

    Phase 0 of the FedWatch integration (project_fedwatch_integration.md):
    pure context, gates nothing. Phase 1 will pre-reg whether velocity predicts
    sector/vertical outcomes.
    """
    from collections import defaultdict

    db = _client()
    hist = db.get_all_documents("cme_fedwatch_history")
    if not hist or not hist.get("metadatas"):
        return {"ok": False, "error": "cme_fedwatch_history empty"}

    # Key by PARSED meeting date, not the raw string: cme_fedwatch_history has
    # accumulated two formats for the same meeting over time ("6/17/2026" and
    # zero-padded "06/17/2026"), which would otherwise split one meeting into two
    # groups. (cme_fedwatch_current never shows this — it's wiped each upload.)
    by_meeting: dict[date, dict] = defaultdict(dict)  # meeting_date -> {scrape_date: row}
    for m in hist["metadatas"]:
        meeting_str = m.get("meeting_date")
        sd = _parse_iso_to_date(m.get("scrape_date"))
        if not meeting_str or sd is None:
            continue
        try:
            mtg = datetime.strptime(meeting_str, "%m/%d/%Y").date()
        except ValueError:
            continue
        if mtg < date.today():
            continue
        # dedupe to one row per scrape_date (last write wins)
        by_meeting[mtg][sd] = {
            "scrape_date": sd, "meeting_date": mtg,
            "cut": m.get("cut_probability"),
            "hold": m.get("hold_probability"),
            "hike": m.get("hike_probability"),
        }

    if not by_meeting:
        return {"ok": False, "error": "no future meetings in history"}

    meetings_sorted = sorted(by_meeting.keys())[:n_meetings]

    rows = []
    overall_latest: date | None = None
    for mtg in meetings_sorted:
        series = [by_meeting[mtg][k] for k in sorted(by_meeting[mtg])]
        latest = series[-1]
        overall_latest = (max(overall_latest, latest["scrape_date"])
                          if overall_latest else latest["scrape_date"])
        target = latest["scrape_date"] - timedelta(days=lookback_days)
        # baseline = snapshot whose scrape_date is closest to the target window
        # start (never after latest). If history is younger than lookback, this
        # naturally falls back to the earliest snapshot and span < lookback_days.
        baseline = min(series, key=lambda r: abs((r["scrape_date"] - target).days))
        span = (latest["scrape_date"] - baseline["scrape_date"]).days
        n_snaps = sum(1 for r in series
                      if baseline["scrape_date"] <= r["scrape_date"] <= latest["scrape_date"])

        def _d(k):
            a, b = latest.get(k), baseline.get(k)
            return (a - b) if (a is not None and b is not None) else None

        d_cut, d_hold, d_hike = _d("cut"), _d("hold"), _d("hike")
        mags = [abs(x) for x in (d_cut, d_hike) if x is not None]
        rows.append({
            "meeting_str": f"{mtg.month}/{mtg.day}/{mtg.year}",  # canonical, un-padded
            "meeting_date": mtg,
            "span_days": span,
            "n_snaps": n_snaps,
            "single_point": span == 0,
            "cut": latest.get("cut"), "hold": latest.get("hold"), "hike": latest.get("hike"),
            "d_cut": d_cut, "d_hold": d_hold, "d_hike": d_hike,
            "magnitude": max(mags) if mags else 0.0,
        })

    movers = [r for r in rows if not r["single_point"]]
    headline = max(movers, key=lambda r: r["magnitude"]) if movers else None
    return {
        "ok": True,
        "lookback_days": lookback_days,
        "rows": rows,
        "headline": headline,
        "scraped_age_days": _scraped_age(
            overall_latest.isoformat() if overall_latest else None),
    }


# ─── Fed News ──────────────────────────────────────────────────────────

def get_recent_fed_news(days_back: int = 3, max_items: int = 5) -> dict[str, Any]:
    """Recent Fed RSS items within the last N days."""
    db = _client()
    res = db.query_by_metadata("fed_news", {"source": "Federal_Reserve_RSS"})
    if not res:
        return {"ok": False, "error": "fed_news empty"}

    cutoff = date.today() - timedelta(days=days_back)
    items = []
    for md in res["metadatas"]:
        pub = _parse_iso_to_date(md.get("pub_date_iso"))
        if pub is None or pub < cutoff:
            continue
        items.append({
            "pub_date": pub,
            "category": md.get("feed_category", "?"),
            "title": md.get("title", "?"),
        })

    items.sort(key=lambda i: i["pub_date"], reverse=True)
    items = items[:max_items]

    latest_scrape = None
    if res.get("metadatas"):
        scrapes = [m.get("scraped_at") for m in res["metadatas"] if m.get("scraped_at")]
        if scrapes:
            latest_scrape = max(scrapes)
    return {
        "ok": True,
        "items": items,
        "days_back": days_back,
        "scraped_age_days": _scraped_age(latest_scrape),
    }


# ─── Geopolitical (BlackRock BII) ──────────────────────────────────────

def _new_month_since(scraped_at: str | None) -> bool:
    """True if today is in a later calendar month than `scraped_at` — i.e. the
    monthly BII dashboard publication has likely refreshed since the last scrape."""
    d = _parse_iso_to_date(scraped_at)
    if d is None:
        return True
    t = date.today()
    return (t.year, t.month) > (d.year, d.month)


def get_geopolitical_summary(weekly_stale_days: int = 7) -> dict[str, Any]:
    """Latest BlackRock Investment Institute geopolitical dashboard (monthly) +
    weekly market commentary, from the blackrock_bii collection. Both are MANUAL
    scrapes — refresh_due flags drive the 'time to refresh' nudge in the alert.

    NB: publish_date in this collection is unreliable (best-effort page parse);
    staleness is computed from scraped_at, like every other section here.
    """
    db = _client()
    res = db.get_all_documents("blackrock_bii")
    if not res or not res.get("metadatas"):
        return {"ok": False, "error": "blackrock_bii empty"}

    geo, wk = [], []
    for m in res["metadatas"]:
        dt = (m or {}).get("doc_type")
        if dt == "geopolitical_dashboard":
            geo.append(m)
        elif dt == "weekly_commentary":
            wk.append(m)

    def _latest(rows):
        rows = [r for r in rows if r.get("scraped_at")]
        return max(rows, key=lambda r: r["scraped_at"]) if rows else None

    def _pipe(s):
        return [x.strip() for x in (s or "").split("|") if x.strip()]

    g, w = _latest(geo), _latest(wk)
    out: dict[str, Any] = {"ok": True, "geo": None, "weekly": None}
    if g:
        g_age = _scraped_age(g.get("scraped_at"))
        out["geo"] = {
            "high_risks": _pipe(g.get("high_risks")),
            "medium_risks": _pipe(g.get("medium_risks")),
            "scraped_age_days": g_age,
            "refresh_due": _new_month_since(g.get("scraped_at")),  # monthly cadence
        }
    if w:
        w_age = _scraped_age(w.get("scraped_at"))
        out["weekly"] = {
            "title": w.get("title"),
            "scraped_age_days": w_age,
            "refresh_due": (w_age is None) or (w_age >= weekly_stale_days),
        }
    return out


# ─── Compose + render ──────────────────────────────────────────────────

def build_macro_brief() -> dict[str, Any]:
    """Build the full brief structure. Each section returns ok/error
    independently so partial failures don't break the whole brief."""
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "curve": get_curve_summary(),
        "fedwatch": get_fedwatch_summary(n_meetings=4),
        "fedwatch_trajectory": get_fedwatch_trajectory(n_meetings=4, lookback_days=14),
        "recession": recession_panel.build_recession_panel(),
        "news": get_recent_fed_news(days_back=3, max_items=5),
        "geopolitical": get_geopolitical_summary(),
    }


def _staleness_note(age_days: int | None) -> str:
    if age_days is None:
        return " [stale: unknown]"
    if age_days == 0:
        return ""
    if age_days == 1:
        return " [1d old]"
    return f" [STALE: {age_days}d old]"


def render_text(brief: dict[str, Any]) -> str:
    lines = ["", "═══ DAILY MACRO BRIEF ═══════════════════════════════════════"]

    # Curve
    c = brief["curve"]
    if not c.get("ok"):
        lines.append(f"  CURVE — unavailable: {c.get('error')}")
    else:
        avg = c["avg_30d"]
        s = c["spread_2s10s"]
        a = avg["spread_2s10s"]
        d2s = (s - a) if (s is not None and a is not None) else None
        d2s_note = f" ({d2s:+.2f} vs 30d avg)" if d2s is not None else ""
        s3 = c["spread_3m10y"]
        a3 = avg["spread_3m10y"]
        d3m = (s3 - a3) if (s3 is not None and a3 is not None) else None
        d3m_note = f" ({d3m:+.2f} vs 30d avg)" if d3m is not None else ""
        inv = "INVERTED" if c.get("is_inverted") else "normal"
        stale = _staleness_note(c.get("scraped_age_days"))
        lines.append(f"  CURVE {c['snapshot_date']}{stale}  — {inv}, N_hist={c['hist_n']}")
        lines.append(f"    DGS10={c['dgs10']:.2f}%   DGS2={c['dgs2']:.2f}%")
        lines.append(f"    2s10s spread {s:+.2f}%{d2s_note}")
        lines.append(f"    3m10y spread {s3:+.2f}%{d3m_note}")

    lines.append("")
    # FedWatch
    fw = brief["fedwatch"]
    if not fw.get("ok"):
        lines.append(f"  FEDWATCH — unavailable: {fw.get('error')}")
    else:
        stale = _staleness_note(fw.get("scraped_age_days"))
        lines.append(f"  FEDWATCH — next {len(fw['meetings'])} FOMC meetings{stale}")
        for m in fw["meetings"]:
            dc = (m["cut"] - m["prior_cut"]) if (m["cut"] is not None and m["prior_cut"] is not None) else 0
            dh = (m["hold"] - m["prior_hold"]) if (m["hold"] is not None and m["prior_hold"] is not None) else 0
            dk = (m["hike"] - m["prior_hike"]) if (m["hike"] is not None and m["prior_hike"] is not None) else 0
            lines.append(
                f"    {m['meeting_str']:11s}  cut {m['cut']:>5.1f}% ({dc:+.1f})"
                f"   hold {m['hold']:>5.1f}% ({dh:+.1f})"
                f"   hike {m['hike']:>5.1f}% ({dk:+.1f})  → {m['most_likely']}"
            )

    # FedWatch repricing trajectory (cme_fedwatch_history — the multi-week tell)
    tj = brief.get("fedwatch_trajectory")
    if tj and tj.get("ok"):
        lines.append(f"    ── repricing (~{tj['lookback_days']}d trajectory) ──")
        for r in sorted(tj["rows"], key=lambda x: x["magnitude"], reverse=True):
            if r["single_point"]:
                lines.append(f"    {r['meeting_str']:11s}  (1 snapshot — no trajectory yet)")
                continue
            dc = f"{r['d_cut']:+.1f}" if r["d_cut"] is not None else " n/a"
            dk = f"{r['d_hike']:+.1f}" if r["d_hike"] is not None else " n/a"
            tag = ("  ← fastest repricing"
                   if (tj["headline"] and r["meeting_str"] == tj["headline"]["meeting_str"])
                   else "")
            lines.append(
                f"    {r['meeting_str']:11s}  cut {dc}pp  hike {dk}pp"
                f"   over {r['span_days']}d/{r['n_snaps']} snaps{tag}"
            )
    elif tj and not tj.get("ok"):
        lines.append(f"    ── repricing — unavailable: {tj.get('error')}")

    lines.append("")
    # Recession panel
    rp = brief.get("recession")
    if rp:
        lines.append(recession_panel.render_text(rp))

    lines.append("")
    # News
    n = brief["news"]
    if not n.get("ok"):
        lines.append(f"  FED NEWS — unavailable: {n.get('error')}")
    elif not n["items"]:
        stale = _staleness_note(n.get("scraped_age_days"))
        lines.append(f"  FED NEWS — no items in last {n['days_back']}d{stale}")
    else:
        stale = _staleness_note(n.get("scraped_age_days"))
        lines.append(f"  FED NEWS — last {n['days_back']}d ({len(n['items'])} items){stale}")
        for it in n["items"]:
            lines.append(f"    [{it['pub_date']}] {it['category']:18s} {it['title'][:75]}")

    lines.append("")
    # Geopolitical (BlackRock BII) — context for the advisor + manual-refresh nudge
    g = brief.get("geopolitical")
    if g and g.get("ok"):
        geo, wk = g.get("geo"), g.get("weekly")
        lines.append("  GEOPOLITICAL (BlackRock BII)")
        if geo:
            stale = _staleness_note(geo.get("scraped_age_days"))
            if geo["high_risks"]:
                lines.append(f"    HIGH:   {', '.join(geo['high_risks'])}")
            if geo["medium_risks"]:
                lines.append(f"    MEDIUM: {', '.join(geo['medium_risks'])}")
            lines.append(f"    dashboard (monthly){stale}")
            if geo.get("refresh_due"):
                lines.append("    ⚠ REFRESH the BII GEOPOLITICAL DASHBOARD (manual, monthly) — "
                             "new month's edition likely out; re-scrape in Agent_Project")
        if wk:
            stale = _staleness_note(wk.get("scraped_age_days"))
            lines.append(f"    weekly commentary: \"{(wk.get('title') or '?')[:60]}\"{stale}")
            if wk.get("refresh_due"):
                lines.append("    ⚠ REFRESH the BII WEEKLY COMMENTARY (manual, weekly) — "
                             "last pull ≥7d ago; re-scrape in Agent_Project")
    elif g and not g.get("ok"):
        lines.append(f"  GEOPOLITICAL (BII) — unavailable: {g.get('error')}")

    return "\n".join(lines)


def render_html(brief: dict[str, Any]) -> str:
    """Compact HTML rendering. Inline styles for email-client compat."""
    parts = ['<div style="font-family:Menlo,Consolas,monospace;font-size:13px;background:#f5f5f5;border-left:4px solid #2c5282;padding:10px 14px;margin:12px 0;border-radius:4px">']
    parts.append('<div style="font-weight:bold;color:#2c5282;font-size:14px;margin-bottom:6px">DAILY MACRO BRIEF</div>')

    # Curve
    c = brief["curve"]
    parts.append('<div style="margin-top:6px"><b>CURVE</b>')
    if not c.get("ok"):
        parts.append(f' <span style="color:#a00">unavailable: {c.get("error")}</span>')
    else:
        avg = c["avg_30d"]
        s, a = c["spread_2s10s"], avg["spread_2s10s"]
        s3, a3 = c["spread_3m10y"], avg["spread_3m10y"]
        d2s = (s - a) if (s is not None and a is not None) else None
        d3m = (s3 - a3) if (s3 is not None and a3 is not None) else None
        inv = "INVERTED" if c.get("is_inverted") else "normal"
        stale = _staleness_note(c.get("scraped_age_days"))
        parts.append(f' — {c["snapshot_date"]} ({inv}){stale}<br/>')
        parts.append(f'&nbsp;&nbsp;DGS10={c["dgs10"]:.2f}%, DGS2={c["dgs2"]:.2f}%<br/>')
        parts.append(f'&nbsp;&nbsp;2s10s {s:+.2f}% ({d2s:+.2f} vs 30d avg), 3m10y {s3:+.2f}% ({d3m:+.2f} vs 30d avg)')
    parts.append("</div>")

    # FedWatch
    fw = brief["fedwatch"]
    parts.append('<div style="margin-top:8px"><b>FEDWATCH</b>')
    if not fw.get("ok"):
        parts.append(f' <span style="color:#a00">unavailable: {fw.get("error")}</span>')
    else:
        stale = _staleness_note(fw.get("scraped_age_days"))
        parts.append(f' — next {len(fw["meetings"])} meetings{stale}')
        parts.append('<table style="border-collapse:collapse;margin-top:4px;font-size:12px"><tr style="background:#e1e4e8"><th style="padding:2px 8px;text-align:left">meeting</th><th style="padding:2px 8px">cut</th><th style="padding:2px 8px">hold</th><th style="padding:2px 8px">hike</th><th style="padding:2px 8px">→</th></tr>')
        for m in fw["meetings"]:
            dc = (m["cut"] - m["prior_cut"]) if (m["cut"] is not None and m["prior_cut"] is not None) else 0
            dh = (m["hold"] - m["prior_hold"]) if (m["hold"] is not None and m["prior_hold"] is not None) else 0
            dk = (m["hike"] - m["prior_hike"]) if (m["hike"] is not None and m["prior_hike"] is not None) else 0
            parts.append(
                f'<tr><td style="padding:2px 8px">{m["meeting_str"]}</td>'
                f'<td style="padding:2px 8px;text-align:right">{m["cut"]:.1f}% ({dc:+.1f})</td>'
                f'<td style="padding:2px 8px;text-align:right">{m["hold"]:.1f}% ({dh:+.1f})</td>'
                f'<td style="padding:2px 8px;text-align:right">{m["hike"]:.1f}% ({dk:+.1f})</td>'
                f'<td style="padding:2px 8px"><b>{m["most_likely"]}</b></td></tr>'
            )
        parts.append("</table>")
        # Repricing trajectory (cme_fedwatch_history)
        tj = brief.get("fedwatch_trajectory")
        if tj and tj.get("ok"):
            parts.append(f'<div style="margin-top:4px;font-size:12px;color:#444">'
                         f'<b>repricing (~{tj["lookback_days"]}d):</b></div>'
                         f'<ul style="margin:2px 0 0 18px;padding:0;font-size:12px">')
            for r in sorted(tj["rows"], key=lambda x: x["magnitude"], reverse=True):
                if r["single_point"]:
                    parts.append(f'<li>{r["meeting_str"]} — 1 snapshot, no trajectory yet</li>')
                    continue
                dc = f"{r['d_cut']:+.1f}" if r["d_cut"] is not None else "n/a"
                dk = f"{r['d_hike']:+.1f}" if r["d_hike"] is not None else "n/a"
                is_head = tj["headline"] and r["meeting_str"] == tj["headline"]["meeting_str"]
                tag = ' <b style="color:#a00">← fastest repricing</b>' if is_head else ""
                parts.append(
                    f'<li>{r["meeting_str"]}: cut {dc}pp, hike {dk}pp '
                    f'<span style="color:#888">({r["span_days"]}d/{r["n_snaps"]} snaps)</span>{tag}</li>'
                )
            parts.append("</ul>")
        elif tj and not tj.get("ok"):
            parts.append(f'<div style="font-size:12px;color:#a00">repricing — unavailable: {tj.get("error")}</div>')
    parts.append("</div>")

    # Recession panel
    rp = brief.get("recession")
    if rp:
        parts.append(recession_panel.render_html(rp))

    # News
    n = brief["news"]
    parts.append('<div style="margin-top:8px"><b>FED NEWS</b>')
    if not n.get("ok"):
        parts.append(f' <span style="color:#a00">unavailable: {n.get("error")}</span>')
    elif not n["items"]:
        stale = _staleness_note(n.get("scraped_age_days"))
        parts.append(f' — no items in last {n["days_back"]}d{stale}')
    else:
        stale = _staleness_note(n.get("scraped_age_days"))
        parts.append(f' — last {n["days_back"]}d{stale}<ul style="margin:4px 0 0 18px;padding:0">')
        for it in n["items"]:
            parts.append(f'<li>[{it["pub_date"]}] <i>{it["category"]}</i> — {it["title"]}</li>')
        parts.append("</ul>")
    parts.append("</div>")

    # Geopolitical (BlackRock BII)
    g = brief.get("geopolitical")
    if g and g.get("ok"):
        geo, wk = g.get("geo"), g.get("weekly")
        parts.append('<div style="margin-top:8px"><b>GEOPOLITICAL (BlackRock BII)</b>')
        if geo:
            stale = _staleness_note(geo.get("scraped_age_days"))
            if geo["high_risks"]:
                parts.append(f'<br/>&nbsp;&nbsp;<b style="color:#a00">HIGH:</b> {", ".join(geo["high_risks"])}')
            if geo["medium_risks"]:
                parts.append(f'<br/>&nbsp;&nbsp;<b style="color:#b58900">MEDIUM:</b> {", ".join(geo["medium_risks"])}')
            parts.append(f'<br/>&nbsp;&nbsp;<span style="color:#888">dashboard (monthly){stale}</span>')
            if geo.get("refresh_due"):
                parts.append('<div style="margin-top:4px;padding:4px 8px;background:#fdecea;'
                             'border-left:3px solid #a00;color:#a00;font-size:12px">'
                             '⚠ REFRESH the BII <b>geopolitical dashboard</b> (manual, monthly) — '
                             'new month\'s edition likely out; re-scrape in Agent_Project</div>')
        if wk:
            stale = _staleness_note(wk.get("scraped_age_days"))
            parts.append(f'<br/>&nbsp;&nbsp;weekly: "<i>{(wk.get("title") or "?")[:60]}</i>"{stale}')
            if wk.get("refresh_due"):
                parts.append('<div style="margin-top:4px;padding:4px 8px;background:#fdecea;'
                             'border-left:3px solid #a00;color:#a00;font-size:12px">'
                             '⚠ REFRESH the BII <b>weekly commentary</b> (manual, weekly) — '
                             'last pull ≥7d ago; re-scrape in Agent_Project</div>')
        parts.append("</div>")
    elif g and not g.get("ok"):
        parts.append(f'<div style="margin-top:8px"><b>GEOPOLITICAL (BII)</b> '
                     f'<span style="color:#a00">unavailable: {g.get("error")}</span></div>')

    parts.append("</div>")
    return "".join(parts)


if __name__ == "__main__":
    brief = build_macro_brief()
    print(render_text(brief))
