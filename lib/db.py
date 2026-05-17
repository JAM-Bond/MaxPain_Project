"""Single source of truth for the shared SQLite database path.

All consumers read the path from here so future moves/renames are a
one-line change in this module rather than a sweep across every caller.

Usage:
    from lib.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
"""

from pathlib import Path

DB_PATH = Path.home() / "MaxPain_Project/data/shared/maxpain.db"
