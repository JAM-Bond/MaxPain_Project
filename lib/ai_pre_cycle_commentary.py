"""AI Pre-Cycle Commentary — Phase 2 of the AI advisory layer.

Reads the pre-cycle data bundle (macro + regime + verdict grid + open
positions) through SOUL.md as Claude Opus 4.7's system prompt. Emits a
5-section narrative annotating today's GO / DOWNSIZE verdicts with macro
context and concentration / correlation flags. AI does NOT change
verdicts, generate new tickers, or predict prices — per
project_ai_advisory_layer_plan.md.

Pattern mirrors lib/ai_macro_brief.py and lib/ai_advisor.py:
  - SOUL.md sent as system with cache_control: ephemeral (prompt caching)
  - Response cached by (run_date, bundle_hash) in SQLite
  - User prompt body lives in prompts/pre_cycle_commentary/v1.md
    (bump filename → repoint VERSION constant to iterate without code edit)

Usage:
    from lib.ai_pre_cycle_commentary import generate_pre_cycle_commentary
    result = generate_pre_cycle_commentary()  # uses today's bundle
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

VERSION = "v1"
PROMPT_PATH = ROOT / "prompts" / "pre_cycle_commentary" / f"{VERSION}.md"

MODEL = "claude-opus-4-7"
MAX_TOKENS = 8000


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


def _load_prompt_template() -> str:
    if not PROMPT_PATH.exists():
        raise RuntimeError(f"Prompt template not found at {PROMPT_PATH}")
    raw = PROMPT_PATH.read_text()
    # The template file wraps the actual prompt between sentinel markers so
    # the file itself can contain documentation. Extract between markers.
    start = raw.find("## TEMPLATE START")
    end = raw.find("## TEMPLATE END")
    if start == -1 or end == -1:
        raise RuntimeError(f"Prompt template missing TEMPLATE START/END markers: {PROMPT_PATH}")
    body = raw[start + len("## TEMPLATE START"):end].strip()
    return body


def _bundle_hash(run_date: str, bundle_text: str) -> str:
    h = hashlib.sha256()
    h.update(run_date.encode())
    h.update(b"\x00")
    h.update(VERSION.encode())  # bump prompt version → bust cache
    h.update(b"\x00")
    h.update(bundle_text.encode())
    return h.hexdigest()[:16]


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_pre_cycle_cache (
            run_date TEXT NOT NULL,
            bundle_hash TEXT NOT NULL,
            response_text TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_creation_tokens INTEGER,
            elapsed_seconds REAL,
            generated_at TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            PRIMARY KEY (run_date, bundle_hash)
        )
    """)


def _cache_get(run_date: str, bundle_hash: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_cache_table(conn)
    row = conn.execute(
        "SELECT * FROM ai_pre_cycle_cache WHERE run_date = ? AND bundle_hash = ?",
        (run_date, bundle_hash),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _cache_put(run_date: str, bundle_hash: str, response_text: str,
               usage: dict, elapsed: float) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_cache_table(conn)
    conn.execute("""
        INSERT OR REPLACE INTO ai_pre_cycle_cache
            (run_date, bundle_hash, response_text, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, elapsed_seconds,
             generated_at, model, prompt_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_date, bundle_hash, response_text,
          usage.get("input_tokens", 0),
          usage.get("output_tokens", 0),
          usage.get("cache_read_input_tokens", 0),
          usage.get("cache_creation_input_tokens", 0),
          elapsed,
          datetime.now().isoformat(timespec="seconds"),
          MODEL,
          VERSION))
    conn.commit()
    conn.close()


def get_latest_cached(run_date: str | None = None) -> dict | None:
    """Return the most-recent cached commentary for `run_date`, or None.
    If run_date is None, returns the latest cached row across all dates.
    Used by the dashboard page (no Generate button) and the daily-alert
    annotation surface.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_cache_table(conn)
    if run_date is None:
        row = conn.execute("""
            SELECT * FROM ai_pre_cycle_cache
            ORDER BY run_date DESC, generated_at DESC
            LIMIT 1
        """).fetchone()
    else:
        row = conn.execute("""
            SELECT * FROM ai_pre_cycle_cache
            WHERE run_date = ?
            ORDER BY generated_at DESC
            LIMIT 1
        """, (run_date,)).fetchone()
    conn.close()
    return dict(row) if row else None


@dataclass
class CommentaryResult:
    response_text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    elapsed_seconds: float
    generated_at: str
    model: str
    prompt_version: str
    cached: bool


def _build_user_prompt(bundle_text: str, run_date: str) -> str:
    template = _load_prompt_template()
    # Template uses {bundle_text} and {run_date} placeholders.
    return template.replace("{bundle_text}", bundle_text).replace("{run_date}", run_date)


def generate_pre_cycle_commentary(bundle_text: str | None = None,
                                   run_date: str | None = None,
                                   force_refresh: bool = False) -> CommentaryResult:
    """Run the pre-cycle commentary for today (or specified run_date).

    If bundle_text is None, composes it fresh from
    `dashboard.queries.pre_cycle_bundle.compose_bundle(run_date)`.

    Returns cached result on identical (run_date, bundle_hash) unless
    force_refresh=True. Hash includes the prompt VERSION, so bumping the
    prompt file automatically busts the cache.
    """
    if run_date is None:
        run_date = date.today().isoformat()
    if bundle_text is None:
        import sys
        sys.path.insert(0, str(ROOT))
        from dashboard.queries.pre_cycle_bundle import compose_bundle  # noqa: E402
        bundle_text = compose_bundle(run_date)

    bhash = _bundle_hash(run_date, bundle_text)

    if not force_refresh:
        cached = _cache_get(run_date, bhash)
        if cached is not None:
            return CommentaryResult(
                response_text=cached["response_text"],
                input_tokens=cached["input_tokens"] or 0,
                output_tokens=cached["output_tokens"] or 0,
                cache_read_tokens=cached["cache_read_tokens"] or 0,
                cache_creation_tokens=cached["cache_creation_tokens"] or 0,
                elapsed_seconds=cached["elapsed_seconds"] or 0.0,
                generated_at=cached["generated_at"],
                model=cached["model"],
                prompt_version=cached.get("prompt_version") or VERSION,
                cached=True,
            )

    import anthropic

    client = anthropic.Anthropic(api_key=_load_api_key())
    soul = _load_soul()
    user_prompt = _build_user_prompt(bundle_text, run_date)

    t0 = time.monotonic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
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
    _cache_put(run_date, bhash, response_text, usage, elapsed)

    return CommentaryResult(
        response_text=response_text,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_read_tokens=usage["cache_read_input_tokens"],
        cache_creation_tokens=usage["cache_creation_input_tokens"],
        elapsed_seconds=elapsed,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        model=MODEL,
        prompt_version=VERSION,
        cached=False,
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", default=None)
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="Build prompt + bundle, print sizes, do NOT call API")
    args = ap.parse_args()

    if args.dry_run:
        import sys
        sys.path.insert(0, str(ROOT))
        from dashboard.queries.pre_cycle_bundle import compose_bundle  # noqa: E402
        rd = args.run_date or date.today().isoformat()
        bundle = compose_bundle(rd)
        prompt = _build_user_prompt(bundle, rd)
        soul = _load_soul()
        print(f"DRY RUN — run_date={rd}, prompt_version={VERSION}")
        print(f"  SOUL.md chars:        {len(soul):,}")
        print(f"  Bundle chars:         {len(bundle):,}")
        print(f"  User prompt chars:    {len(prompt):,}")
        print(f"  Cache key (bhash):    {_bundle_hash(rd, bundle)}")
        print()
        print("─── First 60 lines of composed user prompt ───")
        print("\n".join(prompt.splitlines()[:60]))
        print("…")
    else:
        res = generate_pre_cycle_commentary(run_date=args.run_date,
                                             force_refresh=args.force_refresh)
        print(f"--- Pre-Cycle Commentary (cached={res.cached}, prompt={res.prompt_version}) ---")
        print(f"Model: {res.model}  in={res.input_tokens}  out={res.output_tokens}  "
              f"cache_read={res.cache_read_tokens}  elapsed={res.elapsed_seconds:.1f}s")
        print()
        print(res.response_text)
