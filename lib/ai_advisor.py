"""AI advisor wrapper for the Post-Mortem page.

Loads SOUL.md as the system prompt (with prompt caching — ~12KB stable
prefix), sends a data bundle as the user message, returns Claude's
analysis. Caches responses by (opex, bundle_hash) in SQLite so flipping
back to a cycle doesn't re-charge the API.

Model: claude-opus-4-7 with adaptive thinking + effort=high.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path.home() / "MaxPain_Project"
SOUL_PATH = ROOT / "config" / "SOUL.md"
API_KEYS_ENV = ROOT / "config" / "api_keys.env"
DB_PATH = Path.home() / "Metal_Project/data/shared/metal_project.db"

MODEL = "claude-opus-4-7"
MAX_TOKENS = 16000


def _load_api_key() -> str:
    """Read ANTHROPIC_API_KEY from config/api_keys.env (fall back to env)."""
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


def _bundle_hash(opex: str, bundle_text: str) -> str:
    h = hashlib.sha256()
    h.update(opex.encode())
    h.update(b"\x00")
    h.update(bundle_text.encode())
    return h.hexdigest()[:16]


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_advisor_cache (
            opex TEXT NOT NULL,
            bundle_hash TEXT NOT NULL,
            response_text TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_creation_tokens INTEGER,
            elapsed_seconds REAL,
            generated_at TEXT NOT NULL,
            model TEXT NOT NULL,
            PRIMARY KEY (opex, bundle_hash)
        )
    """)


def _cache_get(opex: str, bundle_hash: str) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    _ensure_cache_table(conn)
    row = conn.execute(
        "SELECT * FROM ai_advisor_cache WHERE opex = ? AND bundle_hash = ?",
        (opex, bundle_hash),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _cache_put(opex: str, bundle_hash: str, response_text: str,
               usage: dict, elapsed: float) -> None:
    conn = sqlite3.connect(str(DB_PATH))
    _ensure_cache_table(conn)
    conn.execute("""
        INSERT OR REPLACE INTO ai_advisor_cache
            (opex, bundle_hash, response_text, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, elapsed_seconds,
             generated_at, model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (opex, bundle_hash, response_text,
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


def generate_postmortem(opex: str, bundle_text: str,
                        force_refresh: bool = False) -> AdvisorResult:
    """Run the post-mortem analysis for a given OpEx + bundle.

    Returns cached result on identical (opex, bundle_hash) unless
    force_refresh=True. Cache is keyed by hash of the bundle, so any
    change to the underlying data composes a new cache entry.
    """
    bundle_hash = _bundle_hash(opex, bundle_text)

    if not force_refresh:
        cached = _cache_get(opex, bundle_hash)
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

    user_prompt = (
        f"# Post-mortem analysis request — OpEx {opex}\n\n"
        f"Below is the assembled data bundle for the {opex} cycle. Read it through "
        f"the SOUL.md framework you've been given. Produce an interpretive synthesis "
        f"that helps the trader execute the existing framework more consistently in the "
        f"next cycle.\n\n"
        f"Cite every claim using the citation formats in SOUL §Quantitative Evidence "
        f"Anchoring. Declare adequacy on every claim involving the live ledger. End "
        f"with one of: HYPOTHESIS / NO HYPOTHESIS / NEXT QUESTION (per SOUL §Tone).\n\n"
        f"---\n\n"
        f"{bundle_text}\n"
    )

    t0 = time.monotonic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[
            {
                "type": "text",
                "text": soul,
                "cache_control": {"type": "ephemeral"},
            },
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
    _cache_put(opex, bundle_hash, response_text, usage, elapsed)

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
