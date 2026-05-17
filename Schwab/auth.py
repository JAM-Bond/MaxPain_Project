"""
MaxPain Project — Schwab OAuth2 Authentication
File: Schwab/auth.py

Manages token acquisition, storage, and automatic refresh.
First run: opens browser for manual authorization.
Subsequent runs: refreshes automatically using stored refresh token.

Token storage: config/api_keys.env — SCHWAB_TOKEN key (JSON, auto-managed)
Callback URL:  https://127.0.0.1 (must match developer.schwab.com registration)

PROJECT_ROOT resolves via __file__ so this module is self-contained
and cannot drift against a sibling project's env file.
"""

import os
import sys
import json
import time
import base64
import fcntl
import tempfile
import urllib.parse
import urllib.request
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

# ── Paths ──────────────────────────────────────────────────────────────────────
# Locate env file relative to this file's project root so the script stays
# self-contained and can't drift against a sibling project's env file.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH     = PROJECT_ROOT / "config" / "api_keys.env"
LOCK_PATH    = PROJECT_ROOT / "config" / ".api_keys.env.lock"

# Max seconds to wait for the refresh lock before giving up. Schwab refresh
# rountrips usually complete in <2s; 30s is generous slack for retries.
REFRESH_LOCK_TIMEOUT = 30.0


@contextmanager
def _token_refresh_lock(timeout: float = REFRESH_LOCK_TIMEOUT):
    """Serialize Schwab refresh-grant calls across concurrent processes.

    Schwab rotates the refresh_token on each grant. Two near-simultaneous
    refreshes by different processes (Streamlit dashboard + cron, smoke
    test + scheduled job, etc.) race against the rotation: the second
    request holds a now-stale refresh_token in memory and Schwab returns
    refresh_token_authentication_error. The lineage is then dead and only
    `--force-reauth` recovers. See `reference_schwab_reauth.md` for the
    full rotation-race diagnosis on the 2026-05-10 failure.

    Implementation: advisory fcntl.flock on a dedicated lock file
    (`config/.api_keys.env.lock`). The env file itself is NOT locked —
    keeping lock semantics decoupled from file content avoids any
    accidental coupling with reader paths that don't acquire the lock.
    Polls non-blocking with a short sleep so deadline enforcement is
    honored; releases on context exit (including exceptions).
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.time() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() >= deadline:
                    raise RuntimeError(
                        f"Could not acquire Schwab refresh lock within "
                        f"{timeout}s — another process appears stuck. "
                        f"Check {LOCK_PATH} and retry."
                    )
                time.sleep(0.25)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

# ── Schwab OAuth endpoints ─────────────────────────────────────────────────────
AUTH_URL    = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL   = "https://api.schwabapi.com/v1/oauth/token"
REDIRECT_URI = "https://127.0.0.1"

# ── Scopes ─────────────────────────────────────────────────────────────────────
# Both products selected: Accounts & Trading + Market Data
SCOPE = "readonly"


# ══════════════════════════════════════════════════════════════════════════════
# ENV FILE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def load_env() -> dict:
    """Read all key=value pairs from api_keys.env into a dict."""
    env = {}
    if not ENV_PATH.exists():
        return env
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip()
    return env


def save_env(env: dict):
    """Write the env dict back to api_keys.env, preserving comments.

    Atomic via tmpfile-in-same-dir + os.replace, so a concurrent reader
    cannot see a half-written file. Same-dir tmpfile is required because
    os.replace is atomic only within a single filesystem.
    """
    lines_out = []
    existing_keys = set()

    # Preserve existing lines, updating values in place
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    lines_out.append(line.rstrip())
                    continue
                key = stripped.split("=", 1)[0].strip()
                existing_keys.add(key)
                if key in env:
                    lines_out.append(f"{key}={env[key]}")
                else:
                    lines_out.append(line.rstrip())

    # Append any new keys not already in the file
    for key, val in env.items():
        if key not in existing_keys:
            lines_out.append(f"{key}={val}")

    payload = "\n".join(lines_out) + "\n"
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(ENV_PATH.parent), prefix=".api_keys.env.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, ENV_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_token() -> dict | None:
    """Load SCHWAB_TOKEN from env. Returns parsed dict or None."""
    env = load_env()
    raw = env.get("SCHWAB_TOKEN", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def save_token(token: dict):
    """Persist SCHWAB_TOKEN back to api_keys.env."""
    env = load_env()
    env["SCHWAB_TOKEN"] = json.dumps(token)
    save_env(env)
    print(f"  ✓ Token saved to {ENV_PATH}")

def _basic_auth_header(client_id: str, client_secret: str) -> str:
    """Build the Basic Authorization header value."""
    creds = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(creds.encode()).decode()
    return f"Basic {encoded}"


def _post_token(params: dict, client_id: str, client_secret: str) -> dict:
    """POST to Schwab token endpoint. Returns parsed JSON response."""
    import gzip
    data = urllib.parse.urlencode(params).encode()
    req  = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Authorization", _basic_auth_header(client_id, client_secret))
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept-Encoding", "identity")  # request uncompressed

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if raw[:2] == b'\x1f\x8b':
                raw = gzip.decompress(raw)
            body  = raw.decode("utf-8")
            token = json.loads(body)
            token["received_at"] = time.time()
            return token
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            import gzip as _gz
            if raw[:2] == b'\x1f\x8b':
                raw = _gz.decompress(raw)
            body = raw.decode("utf-8")
        except Exception:
            body = repr(raw[:200])
        raise RuntimeError(f"Token request failed ({e.code}): {body}")


def refresh_access_token(client_id: str, client_secret: str, token: dict) -> dict:
    """Use the refresh token to get a new access token."""
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh token available — re-run browser authorization.")

    print("  Refreshing access token...")
    new_token = _post_token(
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
        client_id, client_secret
    )
    # Preserve refresh token if the new response doesn't include one
    if "refresh_token" not in new_token:
        new_token["refresh_token"] = refresh_token
    return new_token


def is_access_token_expired(token: dict, buffer_seconds: int = 60) -> bool:
    """True if the access token is expired or within buffer_seconds of expiry."""
    received_at  = token.get("received_at", 0)
    expires_in   = token.get("expires_in", 1800)   # Schwab default: 30 min
    expires_at   = received_at + expires_in
    return time.time() >= (expires_at - buffer_seconds)


def is_refresh_token_expired(token: dict) -> bool:
    """
    Schwab refresh tokens expire after 7 days.
    We check against received_at + 7 days with a 10-minute buffer.
    If received_at is missing or zero, treat as expired — forces re-auth
    rather than attempting a refresh that will fail with a cryptic error.
    """
    received_at = token.get("received_at", 0)
    if not received_at:
        print("  WARNING: Token has no received_at timestamp — treating as expired.")
        return True
    seven_days  = 7 * 24 * 3600
    buffer      = 10 * 60
    expires_at  = received_at + seven_days - buffer
    remaining   = expires_at - time.time()
    return time.time() >= expires_at


# ══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTHORIZATION FLOW
# ══════════════════════════════════════════════════════════════════════════════

def browser_authorization_flow(client_id: str, client_secret: str) -> dict:
    """
    Full OAuth2 authorization code flow.
    Opens browser → user logs in → user pastes redirect URL back → exchange for tokens.

    Refuses to run in a non-interactive context (no tty on stdin). The flow
    requires a human to paste the redirect URL back, and calling
    webbrowser.open() from a daemon/subprocess just spams tabs without any
    way to complete the handshake. Launchd, Streamlit subprocesses, and cron
    all hit this guard — they must surface the error to the user and wait
    for a manual `python3.11 Schwab/auth.py --force-reauth` from a terminal.
    """
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Schwab re-authorization requires an interactive terminal. "
            "Run `python3.11 Schwab/auth.py --force-reauth` from a shell "
            "to paste the redirect URL back."
        )

    # Build authorization URL
    params = {
        "response_type": "code",
        "client_id":     client_id,
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPE,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("\n" + "═" * 60)
    print("  SCHWAB OAUTH2 — BROWSER AUTHORIZATION")
    print("═" * 60)
    print("\n  Step 1: Your browser will open the Schwab login page.")
    print("  Step 2: Log in and approve access.")
    print("  Step 3: You will be redirected to https://127.0.0.1")
    print("          The page will show an error — that is NORMAL.")
    print("  Step 4: Copy the FULL URL from your browser address bar")
    print("          and paste it here.\n")

    print(f"  Opening: {auth_url}\n")
    webbrowser.open(auth_url)
    time.sleep(2)

    redirect_url = input("  Paste the full redirect URL here: ").strip()

    # Extract authorization code from redirect URL
    parsed = urllib.parse.urlparse(redirect_url)
    qs     = urllib.parse.parse_qs(parsed.query)

    if "code" not in qs:
        raise RuntimeError(
            f"No authorization code found in URL. Got: {redirect_url}\n"
            "Make sure you copied the full URL including ?code=..."
        )

    auth_code = qs["code"][0]
    print(f"\n  ✓ Authorization code received ({auth_code[:8]}...)")

    # Exchange code for tokens
    print("  Exchanging code for tokens...")
    token = _post_token(
        {
            "grant_type":   "authorization_code",
            "code":         auth_code,
            "redirect_uri": REDIRECT_URI,
        },
        client_id, client_secret
    )

    print("  ✓ Access token received")
    print("  ✓ Refresh token received")
    return token


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def get_valid_token() -> str:
    """
    Return a valid access token string, refreshing or re-authorizing as needed.
    This is the main function called by chain/quote fetchers and qualifier.
    """
    env           = load_env()
    client_id     = env.get("SCHWAB_CLIENT_ID", "")
    client_secret = env.get("SCHWAB_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise RuntimeError(
            "SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET not found in config/api_keys.env\n"
            "Add them and re-run."
        )

    token = load_token()

    # No token at all — full browser flow
    if token is None:
        print("  No token found — starting browser authorization flow.")
        token = browser_authorization_flow(client_id, client_secret)
        save_token(token)
        return token["access_token"]

    # Refresh token expired — need full re-auth
    if is_refresh_token_expired(token):
        print("  Refresh token expired (>7 days) — re-authorization required.")
        token = browser_authorization_flow(client_id, client_secret)
        save_token(token)
        return token["access_token"]

    # Access token still valid — return it without acquiring the lock.
    # Avoids serializing every read path through the refresh lock; the
    # common case (token fresh, just need to use it) is contention-free.
    if not is_access_token_expired(token):
        return token["access_token"]

    # Access token expired — serialize the refresh through _token_refresh_lock
    # so concurrent processes don't both call Schwab with the same stale
    # refresh_token (which would kill the lineage; see the 2026-05-10
    # rotation-race diagnosis). Inside the lock we re-read the token: if a
    # concurrent process already refreshed, the access token will be valid
    # and we can skip the network call entirely (double-checked locking).
    refresh_failed = False
    with _token_refresh_lock():
        token = load_token()
        if token is None or is_refresh_token_expired(token):
            # Lineage lost while we waited — fall through to browser flow
            # outside the lock (interactive, can take minutes).
            refresh_failed = True
        elif not is_access_token_expired(token):
            # A concurrent process beat us to the refresh — use the new token.
            return token["access_token"]
        else:
            try:
                token = refresh_access_token(client_id, client_secret, token)
                save_token(token)
            except RuntimeError as e:
                print(f"  Refresh failed: {e}")
                refresh_failed = True

    if refresh_failed:
        print("  Falling back to browser authorization flow.")
        token = browser_authorization_flow(client_id, client_secret)
        save_token(token)
    return token["access_token"]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Schwab OAuth2 authentication")
    parser.add_argument("--force-reauth", action="store_true",
                        help="Force browser authorization flow regardless of token state")
    args = parser.parse_args()

    print("\nMaxPain — Schwab Auth")
    print("─" * 40)
    try:
        if args.force_reauth:
            print("  --force-reauth: clearing token and starting browser flow.")
            env = load_env()
            client_id     = env.get("SCHWAB_CLIENT_ID", "")
            client_secret = env.get("SCHWAB_CLIENT_SECRET", "")
            if not client_id or not client_secret:
                raise RuntimeError(
                    "SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET not found in config/api_keys.env"
                )
            token = browser_authorization_flow(client_id, client_secret)
            save_token(token)
            access_token = token["access_token"]
        else:
            access_token = get_valid_token()

        print(f"\n  ✓ Valid access token: {access_token[:12]}...")
        print("  Authorization complete.")
    except Exception as e:
        print(f"\n  ✗ Error: {e}")
        sys.exit(1)
