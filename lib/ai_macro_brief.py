"""AI Macro Brief — Phase 1 of the AI advisory layer.

Wraps the Daily Macro Brief data (curve + FedWatch + Fed news from
Agent_Project ChromaDB) with a Claude narrative synthesis. The data layer
in `lib/macro_brief.py` provides the inputs; this module formats them as
a user prompt, sends to Claude with SOUL.md as the system prompt (same
prompt-cache pattern as `lib/ai_advisor.py`), and caches the response
per day in SQLite.

Architecture (per project_ai_advisory_layer_plan.md):
  - AI is an ADVISOR, not an authority. The narrative interprets macro
    state — it does NOT generate trades or override mechanical gates.
  - Each output is cached by (date, brief_hash) so re-fetching during the
    same day with the same upstream data is free.

Usage:
    from lib.ai_macro_brief import generate_macro_brief_narrative
    result = generate_macro_brief_narrative()  # returns AdvisorResult
    print(result.response_text)
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .db import DB_PATH

ROOT = Path.home() / "MaxPain_Project"
SOUL_PATH = ROOT / "config" / "SOUL.md"
API_KEYS_ENV = ROOT / "config" / "api_keys.env"

MODEL = "claude-opus-4-7"
MAX_TOKENS = 4000  # macro narrative is shorter than post-mortem


def _load_api_key() -> str:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if not API_KEYS_ENV.exists():
        raise RuntimeError(f"API key not found: ANTHROPIC_API_KEY env var unset and {API_KEYS_ENV} missing")
    for line in API_KEYS_ENV.read_text().splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"ANTHROPIC_API_KEY= not found in {API_KEYS_ENV}")


def _load_soul() -> str:
    if not SOUL_PATH.exists():
        raise RuntimeError(f"SOUL.md not found at {SOUL_PATH}")
    return SOUL_PATH.read_text()


def _brief_hash(brief_date: str, brief_text: str) -> str:
    h = hashlib.sha256()
    h.update(brief_date.encode())
    h.update(b"\x00")
    h.update(brief_text.encode())
    return h.hexdigest()[:16]


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_macro_brief_cache (
            brief_date TEXT NOT NULL,
            brief_hash TEXT NOT NULL,
            response_text TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_creation_tokens INTEGER,
            elapsed_seconds REAL,
            generated_at TEXT NOT NULL,
            model TEXT NOT NULL,
            PRIMARY KEY (brief_date, brief_hash)
        )
    """)


def _cache_get(brief_date: str, brief_hash: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_cache_table(conn)
    row = conn.execute(
        "SELECT * FROM ai_macro_brief_cache WHERE brief_date = ? AND brief_hash = ?",
        (brief_date, brief_hash),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _cache_put(brief_date: str, brief_hash: str, response_text: str,
               usage: dict, elapsed: float) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_cache_table(conn)
    conn.execute("""
        INSERT OR REPLACE INTO ai_macro_brief_cache
            (brief_date, brief_hash, response_text, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, elapsed_seconds,
             generated_at, model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (brief_date, brief_hash, response_text,
          usage.get("input_tokens", 0),
          usage.get("output_tokens", 0),
          usage.get("cache_read_input_tokens", 0),
          usage.get("cache_creation_input_tokens", 0),
          elapsed,
          datetime.now().isoformat(timespec="seconds"),
          MODEL))
    conn.commit()
    conn.close()


@dataclass
class AdvisorResult:
    response_text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    elapsed_seconds: float
    generated_at: str
    model: str
    cached: bool


def _build_user_prompt(brief_text: str, brief_date: str) -> str:
    return (
        f"# Daily Macro Brief — {brief_date}\n\n"
        f"Below is today's macro data bundle from Agent_Project's bond intelligence "
        f"system: latest yield-curve snapshot with 30-day comparison, next four FOMC "
        f"meetings with day-over-day probability shifts, and recent Fed RSS items.\n\n"
        f"Read it through the SOUL.md framework. Produce a concise narrative (3-5 short "
        f"paragraphs) covering:\n\n"
        f"  1. **Curve state** — what the current shape and 30-day movement imply about "
        f"the bond market's expected path. Cite specific numbers.\n"
        f"  2. **FedWatch trajectory** — what the implied rate path says about Fed "
        f"expectations, and any meaningful shifts (≥2pp in any probability bucket) since "
        f"the prior scrape. Cite meeting dates.\n"
        f"  3. **Recent Fed communications** — what the latest speeches / minutes / "
        f"statements suggest about tone or stance. Cite source dates.\n"
        f"  4. **MaxPain-framework synthesis** — connect the macro picture to the "
        f"framework's live gates (H1 bear gate, contango/VRP bull-put gate, IF "
        f"term-inversion). DO NOT recommend trades. Note if macro state is "
        f"convergent or divergent with current gate states.\n\n"
        f"## Citation formats for this brief (extend SOUL §Quantitative Evidence Anchoring)\n\n"
        f"Macro data is not in the LEDGER/WALK/STUDY/CONFIG/DAILY/LIVE schema. For this "
        f"brief, use these three formats and treat them with the same discipline as the "
        f"SOUL citation formats:\n\n"
        f"  `[CURVE: snapshot_date · field=value]`  — e.g. "
        f"`[CURVE: 2026-05-17 · 2s10s=+0.47%, 30d avg=+0.51%]`\n"
        f"  `[FEDWATCH: meeting_date · field=value (delta vs prior)]`  — e.g. "
        f"`[FEDWATCH: 9/16/2026 · hike=10.5% (+2.2pp)]`\n"
        f"  `[FEDNEWS: pub_date · category · headline-fragment]`  — e.g. "
        f"`[FEDNEWS: 2026-05-14 · Speeches · Bowman opening remarks]`\n\n"
        f"This is ADVISORY context only — the mechanical qualifier remains the trade "
        f"authority. End with one of: HYPOTHESIS / NO HYPOTHESIS / NEXT QUESTION "
        f"(per SOUL §Tone).\n\n"
        f"---\n\n"
        f"{brief_text}\n"
    )


def generate_macro_brief_narrative(brief_text: str | None = None,
                                    brief_date: str | None = None,
                                    force_refresh: bool = False) -> AdvisorResult:
    """Run the AI narrative synthesis for today's macro brief.

    If brief_text is None, builds it fresh from `lib.macro_brief`.
    Caches by (date, hash) — re-calling with same upstream data is free.
    """
    if brief_text is None:
        from .macro_brief import build_macro_brief, render_text
        brief = build_macro_brief()
        brief_text = render_text(brief)
    if brief_date is None:
        brief_date = date.today().isoformat()

    bhash = _brief_hash(brief_date, brief_text)

    if not force_refresh:
        cached = _cache_get(brief_date, bhash)
        if cached is not None:
            return AdvisorResult(
                response_text=cached["response_text"],
                input_tokens=cached["input_tokens"] or 0,
                output_tokens=cached["output_tokens"] or 0,
                cache_read_tokens=cached["cache_read_tokens"] or 0,
                cache_creation_tokens=cached["cache_creation_tokens"] or 0,
                elapsed_seconds=cached["elapsed_seconds"] or 0.0,
                generated_at=cached["generated_at"],
                model=cached["model"],
                cached=True,
            )

    import anthropic

    client = anthropic.Anthropic(api_key=_load_api_key())
    soul = _load_soul()
    user_prompt = _build_user_prompt(brief_text, brief_date)

    t0 = time.monotonic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},  # lower than post-mortem; faster + cheaper
        system=[
            {"type": "text", "text": soul, "cache_control": {"type": "ephemeral"}},
        ],
        messages=[
            {"role": "user", "content": user_prompt},
        ],
    )
    elapsed = time.monotonic() - t0

    text_parts = [b.text for b in response.content if b.type == "text"]
    response_text = "\n\n".join(text_parts).strip()

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    _cache_put(brief_date, bhash, response_text, usage, elapsed)

    return AdvisorResult(
        response_text=response_text,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read_input_tokens"],
        cache_creation_tokens=usage["cache_creation_input_tokens"],
        elapsed_seconds=elapsed,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        model=MODEL,
        cached=False,
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-refresh", action="store_true")
    args = ap.parse_args()
    res = generate_macro_brief_narrative(force_refresh=args.force_refresh)
    print(f"--- AI Macro Brief Narrative (cached={res.cached}) ---")
    print(f"Model: {res.model}  in={res.input_tokens}  out={res.output_tokens}  "
          f"cache_read={res.cache_read_tokens}  elapsed={res.elapsed_seconds:.1f}s")
    print()
    print(res.response_text)
