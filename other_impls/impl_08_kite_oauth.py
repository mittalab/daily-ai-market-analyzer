"""
Kite Connect — OAuth Token Flow
=================================
Purpose : Generate and store a daily access token for Kite Connect API.
          Must be run once per day — token expires at midnight IST regardless
          of when it was generated.

CONFIRMED WORKING: 2026-05-23
  Login URL generated → browser opened → Kite redirected to localhost:5000
  request_token captured → access_token generated → saved to file
  Subsequent API calls (profile, instruments, historical data) all succeeded.

FULL OAUTH FLOW (in order):
  1. App calls kite.login_url() to get the Kite login page URL
  2. User opens that URL in a browser and logs in with Zerodha credentials
  3. After login, Kite redirects to your registered redirect URL with:
       http://127.0.0.1:5000/?request_token=<token>&action=login&status=success
  4. Flask catches the redirect, extracts request_token from query params
  5. App calls kite.generate_session(request_token, api_secret) to exchange it
  6. generate_session() returns a session dict containing access_token
  7. access_token is saved to kite_access_token.txt for use by all impl_* files

CREDENTIALS:
  API Key    : xm82on7jif6xgpay   (public — safe to store in code)
  API Secret : s90knzfxduc50gyvp706aixbw48auiq9  (private — treat like a password)

REDIRECT URL (must match exactly in Kite developer console):
  http://127.0.0.1:5000/
  Configured at: kite.zerodha.com/developers → your app → Redirect URL

TOKEN FILE:
  kite_access_token.txt — plain text, one line, the access token string
  All impl_* files read from this file via TOKEN_FILE = Path("kite_access_token.txt")

TOKEN LIFETIME:
  Expires at midnight IST every day (not 24h from generation).
  A token generated at 8 PM expires at midnight — only 4 hours of use.
  Best practice: run this script at 8–9 AM before market open.

request_token vs access_token:
  request_token — single-use, short-lived (minutes), returned in the redirect URL
  access_token  — multi-use until midnight IST, what you store and reuse
"""

import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, request as flask_request
from kiteconnect import KiteConnect

# ── Credentials ────────────────────────────────────────────────────────────

API_KEY    = "xm82on7jif6xgpay"
API_SECRET = "s90knzfxduc50gyvp706aixbw48auiq9"
TOKEN_FILE = Path("kite_access_token.txt")

# ── Token utilities ─────────────────────────────────────────────────────────

def load_token() -> str | None:
    """Read stored access token. Returns None if file doesn't exist."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip() or None
    return None


def save_token(token: str) -> None:
    """Save access token to file, overwriting any previous token."""
    TOKEN_FILE.write_text(token)


def validate_token(token: str) -> bool:
    """
    Check if a token is still valid by calling kite.profile().
    Returns True if valid, False if expired or invalid.

    Use this at the start of your pipeline to detect stale tokens early
    rather than discovering a failure mid-run.
    """
    try:
        kite = KiteConnect(api_key=API_KEY)
        kite.set_access_token(token)
        kite.profile()   # Lightweight call — just verifies the token
        return True
    except Exception:
        return False


def get_valid_kite() -> KiteConnect:
    """
    Return an authenticated KiteConnect instance using the stored token.
    Raises RuntimeError if no token is stored or if the token has expired.

    Call this at the top of every pipeline run — fail fast before doing work.
    """
    token = load_token()
    if not token:
        raise RuntimeError(
            "No access token found. Run impl_08_kite_oauth.py to generate one."
        )
    if not validate_token(token):
        raise RuntimeError(
            "Access token has expired (tokens expire at midnight IST). "
            "Run impl_08_kite_oauth.py to get a fresh token."
        )
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(token)
    return kite


# ── OAuth server ────────────────────────────────────────────────────────────

app  = Flask(__name__)
kite = KiteConnect(api_key=API_KEY)


@app.route("/")
def oauth_callback():
    """
    Kite redirects here after the user logs in.
    URL will be: http://127.0.0.1:5000/?request_token=XXX&action=login&status=success

    Exchanges the request_token for an access_token and saves it to file.
    """
    request_token = flask_request.args.get("request_token")
    status        = flask_request.args.get("status")

    if status != "success" or not request_token:
        error = flask_request.args.get("message", "Unknown error")
        return f"<h2>Login failed</h2><p>{error}</p>", 400

    try:
        session      = kite.generate_session(request_token, api_secret=API_SECRET)
        access_token = session["access_token"]
        user_id      = session.get("user_id", "?")
        user_name    = session.get("user_name", "?")
        login_time   = session.get("login_time", datetime.now())

        save_token(access_token)

        print(f"\n  User      : {user_name}  ({user_id})")
        print(f"  Login at  : {login_time}")
        print(f"  Token     : {access_token[:8]}...  (saved to {TOKEN_FILE})")
        print(f"\n  Token valid until midnight IST today.")
        print(f"  You can now run the analysis pipeline.")

        return (
            f"<h2>Login successful</h2>"
            f"<p>Welcome, {user_name}. Token saved.</p>"
            f"<p>Access token: <code>{access_token}</code></p>"
            f"<p>You can close this tab.</p>"
        )

    except Exception as e:
        print(f"\n  ERROR generating session: {e}")
        return f"<h2>Session error</h2><p>{e}</p>", 500


def run_oauth_flow(port: int = 5000):
    """
    Full OAuth flow:
      1. Opens Kite login in the browser
      2. Starts Flask server to catch the redirect
      3. Token is saved automatically when login completes
      4. Press Ctrl+C to stop the server after login

    IMPORTANT: The redirect URL http://127.0.0.1:{port}/ must be registered
    in your Kite app settings at kite.zerodha.com/developers
    """
    login_url = kite.login_url()
    print(f"Opening Kite login URL ...")
    print(f"  {login_url}")
    print(f"\nWaiting for callback on http://127.0.0.1:{port}/")
    print(f"(Redirect URL in Kite app must be set to http://127.0.0.1:{port}/)")
    print(f"\nAfter browser login completes, press Ctrl+C to stop this server.\n")

    webbrowser.open(login_url)

    # Runs until Ctrl+C
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


# ── Daily refresh check ─────────────────────────────────────────────────────

def token_status() -> dict:
    """
    Check the current token status without triggering the OAuth flow.
    Returns a status dict — useful for logging at pipeline start.

    {
        "file_exists": bool,
        "token_present": bool,
        "token_valid": bool,
        "action_required": bool,
        "message": str,
    }
    """
    token = load_token()

    if not token:
        return {
            "file_exists":      TOKEN_FILE.exists(),
            "token_present":    False,
            "token_valid":      False,
            "action_required":  True,
            "message":          "No token found. Run OAuth flow.",
        }

    valid = validate_token(token)
    return {
        "file_exists":      True,
        "token_present":    True,
        "token_valid":      valid,
        "action_required":  not valid,
        "message": (
            "Token is valid. Ready for API calls."
            if valid else
            "Token expired (midnight IST). Re-run OAuth flow."
        ),
    }


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Check existing token first — skip OAuth if still valid
    status = token_status()
    print("Token status check:")
    for k, v in status.items():
        print(f"  {k:<20}: {v}")

    if not status["action_required"]:
        print("\nExisting token is valid — no OAuth needed.")
        print("To force a fresh token, delete kite_access_token.txt and re-run.")
    else:
        print("\nStarting OAuth flow ...")
        run_oauth_flow()
