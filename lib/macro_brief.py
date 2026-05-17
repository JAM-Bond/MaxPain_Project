"""Daily Macro Brief — reads Agent_Project ChromaDB and renders a compact
multi-section brief for the 4:45 PM ET daily alert.

Three sections:
  1. CURVE — latest yield-curve snapshot + spreads vs 30-day average
  2. FEDWATCH — next 4 FOMC meetings, current probabilities + day-over-day shifts
  3. FED NEWS — recent Fed RSS items (last N days)

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
    res = db.query_by_metadata("cme_fedwatch_current",
                               {"source": "CME_FedWatch_CSV"})
    if not res:
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


# ─── Compose + render ──────────────────────────────────────────────────

def build_macro_brief() -> dict[str, Any]:
    """Build the full brief structure. Each section returns ok/error
    independently so partial failures don't break the whole brief."""
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "curve": get_curve_summary(),
        "fedwatch": get_fedwatch_summary(n_meetings=4),
        "news": get_recent_fed_news(days_back=3, max_items=5),
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
    parts.append("</div>")

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

    parts.append("</div>")
    return "".join(parts)


if __name__ == "__main__":
    brief = build_macro_brief()
    print(render_text(brief))
