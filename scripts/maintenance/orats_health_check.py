"""ORATS data-freshness health check — daily early warning.

Checks the by_ticker store for staleness:
  - Reads the latest parquet date in data/orats/parquet/year=YYYY/month=MM/
  - Compares against "expected latest" = today minus 1 business day
  - If actual < expected, sends an email alert
  - If healthy, logs and exits silently (no spam)

Designed to catch the failure mode where SFTP delivery silently breaks
(as happened 2026-05-21 → 2026-05-26 when ORATS migrated us4.hostedftp.com
without notice). The auto-promotion pipeline kept "working" on stale
data for 5 days before discovery.

Cron: 0 10 * * 1-5  (10:00 ET weekdays — after overnight delivery window,
                      before market-open distraction).

Output:
  - Exit 0 if healthy
  - Exit 1 if stale (and email sent)
  - Exit 2 if check itself failed (no files found, etc.)

NOTE: v1 does NOT handle US market holidays. If you get false-positive
alerts the morning after a holiday Monday (e.g., Memorial Day, July 4),
add the holiday to US_MARKET_HOLIDAYS_2026 below or ignore the email
that day.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path.home() / "MaxPain_Project"
sys.path.insert(0, str(ROOT))

from lib.email_alert import send_html_alert  # noqa: E402

PARQUET_ROOT = ROOT / "data/orats/parquet"

# 2026 US market holidays (NYSE) — early warning will false-alarm the
# morning AFTER these dates without this list. Update annually.
US_MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),    # New Year's Day
    date(2026, 1, 19),   # MLK Day
    date(2026, 2, 16),   # Presidents Day
    date(2026, 4, 3),    # Good Friday
    date(2026, 5, 25),   # Memorial Day
    date(2026, 6, 19),   # Juneteenth
    date(2026, 7, 3),    # July 4 observed (Fri)
    date(2026, 9, 7),    # Labor Day
    date(2026, 11, 26),  # Thanksgiving
    date(2026, 12, 25),  # Christmas
}


def previous_business_day(d: date) -> date:
    """Return the most recent business day strictly before `d`, skipping
    weekends and US market holidays."""
    cur = d - timedelta(days=1)
    while cur.weekday() >= 5 or cur in US_MARKET_HOLIDAYS_2026:
        cur -= timedelta(days=1)
    return cur


def latest_parquet_date() -> date | None:
    """Scan data/orats/parquet/year=YYYY/month=MM/ for the most recent
    daily file. Returns the date parsed from the filename, or None if no
    files are found."""
    files = list(PARQUET_ROOT.glob("year=*/month=*/*.parquet"))
    if not files:
        return None
    # Filenames are like 2026-05-22.parquet
    dates = []
    for f in files:
        try:
            d = datetime.strptime(f.stem, "%Y-%m-%d").date()
            dates.append(d)
        except ValueError:
            continue
    return max(dates) if dates else None


def main() -> int:
    today = date.today()
    if today.weekday() >= 5:
        # Saturday/Sunday — don't expect new data; exit clean
        print(f"{today.isoformat()}: weekend, skipping check")
        return 0
    if today in US_MARKET_HOLIDAYS_2026:
        print(f"{today.isoformat()}: market holiday, skipping check")
        return 0

    latest = latest_parquet_date()
    if latest is None:
        msg = f"ORATS health check FAILED on {today.isoformat()}: no parquet files found in {PARQUET_ROOT}"
        print(msg)
        send_html_alert(
            subject=f"MaxPain ORATS Health — CRITICAL ({today.isoformat()})",
            text_body=msg,
        )
        return 2

    expected = previous_business_day(today)
    gap_days = (expected - latest).days

    if latest >= expected:
        print(f"{today.isoformat()}: HEALTHY — latest parquet {latest.isoformat()}, "
              f"expected ≥ {expected.isoformat()}")
        return 0

    # Stale — send alert
    text_body = (
        f"ORATS data is STALE as of {today.isoformat()}.\n\n"
        f"Latest parquet date: {latest.isoformat()}\n"
        f"Expected latest:     {expected.isoformat()} (previous business day)\n"
        f"Staleness gap:       {gap_days} business day(s)\n\n"
        f"Files location: {PARQUET_ROOT}\n\n"
        f"Most common causes:\n"
        f"  - ORATS SFTP delivery failed (auth, path, server change)\n"
        f"  - Subscription lapsed or account migrated\n"
        f"  - Local cron didn't run\n\n"
        f"To diagnose, check: ~/MaxPain_Project/logs/orats_daily_cron.log\n"
        f"To manually backfill: cd ~/MaxPain_Project && "
        f"python3.11 -m scripts.orats.ingest all --year {today.year} --cleanup"
    )
    html_body = (
        f"<pre style='font-family:Menlo,Consolas,monospace;font-size:13px'>"
        f"{text_body}</pre>"
    )

    print(f"{today.isoformat()}: STALE — gap of {gap_days} business day(s). "
          f"Latest: {latest.isoformat()}, expected: {expected.isoformat()}. "
          f"Sending alert email.")
    send_html_alert(
        subject=f"MaxPain ORATS Health — STALE {gap_days}d ({today.isoformat()})",
        text_body=text_body,
        html_body=html_body,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
