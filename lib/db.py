"""Single source of truth for the shared SQLite database path.

All consumers read the path from here so future moves/renames are a
one-line change in this module rather than a sweep across every caller.

Usage:
    from lib.db import DB_PATH, connect
    conn = connect()          # hardened for concurrent scheduled jobs
"""

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / "MaxPain_Project/data/shared/maxpain.db"


def connect(path=DB_PATH, *, timeout: float = 30.0, **kwargs) -> sqlite3.Connection:
    """Open a maxpain.db connection hardened for concurrent scheduled jobs.

    Python's sqlite3 defaults to a 5s busy-wait; under the morning (09:20–09:30)
    and close (16:16–16:25) job clusters — and especially a launchd catch-up
    burst after downtime, when several missed jobs fire at once — that can be
    too short and surface as 'database is locked'. A 30s wait lets writers queue
    patiently instead. The DB is already in WAL mode (readers never block the
    single writer); we re-assert it so a freshly-created DB inherits it too.

    Drop-in for `sqlite3.connect(DB_PATH)`: `connect()`.
    """
    conn = sqlite3.connect(path, timeout=timeout, **kwargs)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
