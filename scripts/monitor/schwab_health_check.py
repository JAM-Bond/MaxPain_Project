#!/usr/bin/env python3.11
"""
Schwab token health check — runs at 8:00 AM ET daily for MaxPain, 7:55 AM ET
for Agent_Project, before each project's morning cron pipeline kicks off.

Each project owns its own auth + token; this script exercises whichever
project is named on the command line.

Catches both Schwab failure modes:
  1. Time-based expiry — refresh token TTL < TTL_WARN_DAYS days remaining
  2. Server-side invalidation — refresh succeeds locally but Schwab rejects
     it (the "token says 5d 23h left but Schwab returns 400" case)

Tests both by calling get_valid_token() AND (for maxpain only) fetching a
tiny test chain (SPY next monthly OpEx). For agent project the chain probe
is skipped — agent uses Schwab for orders + accounts, not chains — and we
rely on get_valid_token's refresh-grant call to exercise the refresh-token
lineage.

On any failure, sends an email via lib/email_alert.py. Subject + body
explain what to do (re-run the auth script from a terminal). Exit code is
0 on success, non-zero on failure so cron status reflects health.

Usage:
    python3.11 scripts/monitor/schwab_health_check.py                       # maxpain (default)
    python3.11 scripts/monitor/schwab_health_check.py --project agent       # agent_project
    python3.11 scripts/monitor/schwab_health_check.py --no-email            # dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lib.email_alert import send_html_alert  # noqa: E402

TTL_WARN_DAYS = 2
TEST_SYMBOL = "SPY"

PROJECT_DIRS = {
    "maxpain": Path.home() / "MaxPain_Project",
    "agent":   Path.home() / "Agent_Project",
}

REAUTH_COMMANDS = {
    "maxpain": "python3.11 ~/MaxPain_Project/Schwab/auth.py --force-reauth",
    "agent":   "python3.11 ~/Agent_Project/Schwab/auth.py --force-reauth",
}


def send_alert(subject: str, body: str) -> bool:
    """Compatibility wrapper for the historical send_alert(subject, body) API."""
    return send_html_alert(subject, body)


def reauth_block(why: str, reauth_command: str, urgency: str = "now") -> str:
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
        f"{reauth_command}\n"
        f"\n"
        f"{why}"
    )


def _load_project_auth(project: str):
    """Import the right project's Schwab.auth module.

    Each project owns its own auth module + env file. We evict any previously
    cached Schwab.* modules from sys.modules so an earlier import (e.g. from a
    test harness) can't shadow the project we're asked to check.
    """
    project_dir = PROJECT_DIRS[project]
    sys.path.insert(0, str(project_dir))
    for mod_name in list(sys.modules):
        if mod_name == "Schwab" or mod_name.startswith("Schwab."):
            del sys.modules[mod_name]
    from Schwab.auth import get_valid_token, load_token  # noqa: E402
    return get_valid_token, load_token


def days_until_refresh_token_expires(load_token) -> float | None:
    """Return remaining refresh-token TTL in days. None if no token persisted.

    Schwab refresh tokens last 7 days from ISSUE (browser re-auth). Anchor on
    refresh_token_issued_at, NOT received_at — received_at is bumped on every
    access-token refresh (~30 min) and would perpetually read ~7 days, masking the
    real expiry (the bug behind the silent 2026-06-09 failure). Falls back to
    received_at only for legacy tokens that predate the issued-at field.
    """
    token = load_token()
    if token is None:
        return None
    issued_at = token.get("refresh_token_issued_at") or token.get("received_at", 0)
    if not issued_at:
        return None
    seven_days = 7 * 24 * 3600
    buffer = 10 * 60
    expires_at = issued_at + seven_days - buffer
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


def main(no_email: bool = False, project: str = "maxpain") -> int:
    if project not in PROJECT_DIRS:
        raise ValueError(f"unknown project '{project}' (choices: {list(PROJECT_DIRS)})")

    label = project.capitalize()
    reauth_command = REAUTH_COMMANDS[project]
    get_valid_token, load_token = _load_project_auth(project)

    print(f"Schwab Health Check — {label}_Project")
    print("─" * 40)

    # Step 1: refresh-token TTL check (proactive)
    ttl_days = days_until_refresh_token_expires(load_token)
    if ttl_days is None:
        msg = reauth_block(
            f"No Schwab refresh token on disk for {label}_Project. Run the "
            "OAuth flow from an interactive terminal (the flow needs you to "
            "paste the redirect URL back).",
            reauth_command,
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert(f"Schwab Health Check [{label}] FAILED — no token on disk", msg)
        return 1

    print(f"  Refresh token TTL: {ttl_days:.2f} days remaining")
    if ttl_days < 0:
        msg = reauth_block(
            f"{label}_Project refresh token has already expired. Today's "
            "morning crons will fail until you re-auth.",
            reauth_command,
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert(f"Schwab Health Check [{label}] FAILED — token expired", msg)
        return 1

    if ttl_days < TTL_WARN_DAYS:
        msg = reauth_block(
            f"{label}_Project refresh token expires in {ttl_days:.2f} days "
            f"(< {TTL_WARN_DAYS}d threshold). Re-auth before then to avoid "
            "cron failures. Run the command from an interactive terminal — "
            "the flow needs you to paste the redirect URL back.",
            reauth_command,
            urgency=f"within {ttl_days:.1f} days",
        )
        print(f"  ⚠ {msg}")
        if not no_email:
            send_alert(
                f"Schwab token [{label}] expires in {ttl_days:.1f} days — re-auth soon",
                msg,
            )
        # Warning, not failure — continue to liveness check.

    # Step 2: live access-token refresh (catches server-side invalidation
    # that the TTL check can't see — the "token says 5d 23h left but Schwab
    # returns invalid_grant" case from 2026-05-12 Agent_Project failure).
    print("  Exercising refresh-token grant via get_valid_token()...")
    try:
        access_token = get_valid_token()
    except Exception as e:
        msg = reauth_block(
            f"{label}_Project Schwab access-token refresh failed: {e}\n\n"
            "Usually means the refresh token was invalidated server-side "
            "(credential rotation, app reset, etc.). Today's morning crons "
            "will fail until you re-auth.",
            reauth_command,
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert(f"Schwab Health Check [{label}] FAILED — refresh rejected", msg)
        return 1

    if not access_token:
        msg = reauth_block(
            "get_valid_token() returned empty access token. Token state is "
            "inconsistent on disk; re-auth from scratch.",
            reauth_command,
            urgency="now",
        )
        print(f"  ✗ {msg}")
        if not no_email:
            send_alert(f"Schwab Health Check [{label}] FAILED — empty access token", msg)
        return 1

    # Step 3: endpoint probe (maxpain only).
    # The chain endpoint requires market-data scope, which only MaxPain is
    # wired for here (lib/schwab_options uses MaxPain's own Schwab.auth).
    # Agent_Project uses Schwab for orders + accounts; its refresh-grant
    # success above is the lineage-health signal, no extra probe needed.
    if project == "maxpain":
        from lib.schwab_options import fetch_chain  # noqa: E402

        expiry = next_monthly_opex_iso()
        print(f"  Probing {TEST_SYMBOL} chain for OpEx {expiry}...")
        chain = fetch_chain(TEST_SYMBOL, expiry, contract_type="CALL")
        if chain is None:
            msg = reauth_block(
                f"Access token refresh succeeded but {TEST_SYMBOL} chain fetch "
                "returned None. Schwab API rejected the token at the endpoint "
                "level. Re-auth required.",
                reauth_command,
                urgency="now",
            )
            print(f"  ✗ {msg}")
            if not no_email:
                send_alert(f"Schwab Health Check [{label}] FAILED — chain probe failed", msg)
            return 1
        n_strikes = chain.get("numberOfContracts", 0)
        print(f"  ✓ {TEST_SYMBOL} chain returned {n_strikes} contracts")

    print(f"  ✓ {label}_Project token healthy. TTL {ttl_days:.2f}d remaining.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-email", action="store_true",
                        help="Skip email alerts (dry-run mode)")
    parser.add_argument("--project", choices=list(PROJECT_DIRS), default="maxpain",
                        help="Which project's token to check (default: maxpain)")
    args = parser.parse_args()
    sys.exit(main(no_email=args.no_email, project=args.project))
