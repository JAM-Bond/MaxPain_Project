#!/usr/bin/env python3.11
"""
Schwab token health check — runs at 8:00 AM ET daily, before the morning
cron pipeline kicks off at 9:15-9:25 AM.

Lifted from Metal_Project/scripts/monitor/schwab_health_check.py 2026-05-04
as part of the Metal phase-out. Auth itself still routes through
Metal_Project/Schwab/ (Tranche 4 deferred); chain probe + email send now
use MaxPain_Project's own lib helpers.

Catches both Schwab failure modes:
  1. Time-based expiry — refresh token TTL < TTL_WARN_DAYS days remaining
  2. Server-side invalidation — refresh succeeds locally but Schwab rejects
     it (the "token says 5d 23h left but Schwab returns 400" case)

Tests both by calling get_valid_token() AND fetching a tiny test chain
(SPY next monthly OpEx).

On any failure, sends an email via lib/email_alert.py. Subject + body
explain what to do (re-run the auth script from a terminal). Exit code is
0 on success, non-zero on failure so cron status reflects health.

Usage:
    python3.11 scripts/monitor/schwab_health_check.py
    python3.11 scripts/monitor/schwab_health_check.py --no-email   # dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Schwab auth still lives in Metal_Project for now (Tranche 4 deferred).
sys.path.insert(0, str(Path.home() / "Metal_Project"))

from Schwab.auth import get_valid_token, load_token  # noqa: E402
from lib.schwab_options import fetch_chain  # noqa: E402
from lib.email_alert import send_html_alert  # noqa: E402

TTL_WARN_DAYS = 2
TEST_SYMBOL = "SPY"

# Re-auth command — kept on its own line, no leading whitespace, so the
# email body can be triple-clicked or selected-all on the line and pasted
# directly into Terminal without trimming spaces. Email previews on mobile
# also show this as the first body line.
REAUTH_COMMAND = "python3.11 ~/Metal_Project/Schwab/auth.py --force-reauth"


def send_alert(subject: str, body: str) -> bool:
    """Compatibility wrapper for the historical send_alert(subject, body) API."""
    return send_html_alert(subject, body)


def reauth_block(why: str, urgency: str = "now") -> str:
    """Build a copy-paste-ready instruction block for the alert body.

    Layout:
      Line 1: COPY THIS COMMAND label
      Line 2: the command, no indent
      Blank line
      Reason / context
    """
    return (
        f"COPY THIS COMMAND INTO TERMINAL ({urgency}):\n"
        f"\n"
        f"{REAUTH_COMMAND}\n"
        f"\n"
        f"{why}"
    )


def days_until_refresh_token_expires() -> float | None:
    """Return remaining refresh-token TTL in days. None if no token persisted.

    Schwab refresh tokens last 7 days from issue. We compute remaining time
    against received_at + 7 days minus a small buffer.
    """
    token = load_token()
    if token is None:
        return None
    received_at = token.get("received_at", 0)
    if not received_at:
        return None
    seven_days = 7 * 24 * 3600
    buffer = 10 * 60
    expires_at = received_at + seven_days - buffer
    remaining = expires_at - time.time()
    return remaining / 86400


def next_monthly_opex_iso() -> str:
    """Find the next monthly OpEx (3rd Friday) in YYYY-MM-DD form.

    Used as a generic forward expiry for the health-check chain probe.
    """
    from datetime import date, timedelta

    today = date.today()
    for m_offset in (0, 1, 2):
        year = today.year + (today.month + m_offset - 1) // 12
        month = ((today.month + m_offset - 1) % 12) + 1
        first = date(year, month, 1)
        first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
        third_friday = first_friday + timedelta(weeks=2)
        if third_friday >= today:
            return third_friday.isoformat()
    # Defensive — should never reach
    return (today + timedelta(days=30)).isoformat()


def main(no_email: bool = False) -> int:
    print("Schwab Health Check")
    print("─" * 40)

    # Step 1: refresh-token TTL check (proactive)
    ttl_days = days_until_refresh_token_expires()
    if ttl_days is None:
        msg = reauth_block(
            "No Schwab refresh token on disk. Run the OAuth flow from an "
            "interactive terminal (the flow needs you to paste the redirect "
            "URL back).",
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert("Schwab Health Check FAILED — no token on disk", msg)
        return 1

    print(f"  Refresh token TTL: {ttl_days:.2f} days remaining")
    if ttl_days < 0:
        msg = reauth_block(
            "Refresh token has already expired. Today's morning crons will "
            "fail until you re-auth.",
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert("Schwab Health Check FAILED — token expired", msg)
        return 1

    if ttl_days < TTL_WARN_DAYS:
        msg = reauth_block(
            f"Refresh token expires in {ttl_days:.2f} days "
            f"(< {TTL_WARN_DAYS}d threshold). Re-auth before then to avoid "
            "cron failures. Run the command from an interactive terminal — "
            "the flow needs you to paste the redirect URL back.",
            urgency=f"within {ttl_days:.1f} days",
        )
        print(f"  ⚠ {msg}")
        if not no_email:
            send_alert(
                f"Schwab token expires in {ttl_days:.1f} days — re-auth soon",
                msg,
            )
        # Warning, not failure — continue to liveness check.

    # Step 2: live access-token refresh + chain fetch (catches server-side
    # invalidation that the TTL check can't see).
    print(f"  Refreshing access token + probing {TEST_SYMBOL} chain...")
    try:
        access_token = get_valid_token()
    except Exception as e:
        msg = reauth_block(
            f"Schwab access-token refresh failed: {e}\n\n"
            "Usually means the refresh token was invalidated server-side "
            "(credential rotation, app reset, etc.). Today's morning crons "
            "will fail until you re-auth.",
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert("Schwab Health Check FAILED — refresh rejected", msg)
        return 1

    if not access_token:
        msg = reauth_block(
            "get_valid_token() returned empty access token. Token state is "
            "inconsistent on disk; re-auth from scratch.",
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert("Schwab Health Check FAILED — empty access token", msg)
        return 1

    # Probe a real chain to confirm the access token actually works
    expiry = next_monthly_opex_iso()
    print(f"  Probing {TEST_SYMBOL} chain for OpEx {expiry}...")
    chain = fetch_chain(TEST_SYMBOL, expiry, contract_type="CALL")
    if chain is None:
        msg = reauth_block(
            f"Access token refresh succeeded but {TEST_SYMBOL} chain fetch "
            "returned None. Schwab API rejected the token at the endpoint "
            "level. Re-auth required.",
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert("Schwab Health Check FAILED — chain probe failed", msg)
        return 1

    n_strikes = chain.get("numberOfContracts", 0)
    print(f"  ✓ {TEST_SYMBOL} chain returned {n_strikes} contracts")
    print(f"  ✓ Token healthy. TTL {ttl_days:.2f}d remaining.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-email", action="store_true",
                        help="Skip email alerts (dry-run mode)")
    args = parser.parse_args()
    sys.exit(main(no_email=args.no_email))
