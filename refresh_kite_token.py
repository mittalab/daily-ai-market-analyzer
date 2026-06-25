"""
Daily Kite token refresher — run this once before the 10 PM pipeline.

Usage:
    python refresh_kite_token.py

Flow:
    1. Opens the Zerodha login page in your browser
    2. You log in with Zerodha credentials + TOTP
    3. Zerodha redirects your browser to http://127.0.0.1:5000/
    4. This script captures the request_token, exchanges it for an access_token
    5. Token is stored in Supabase kite_tokens table
    6. Script exits — tonight's pipeline can now authenticate with Kite

The main FastAPI app (port 8000) does not need to be running for this.
"""
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

_CALLBACK_PORT = 5000
_result: dict = {"ok": False, "error": None}

_SUCCESS_HTML = b"""<!DOCTYPE html>
<html><head><title>Kite Auth</title>
<style>
  body{font-family:sans-serif;display:flex;align-items:center;justify-content:center;
       height:100vh;margin:0;background:#0f172a}
  .card{background:#1e293b;border-radius:12px;padding:2rem 3rem;
        text-align:center;color:#f1f5f9;max-width:420px}
  .icon{font-size:3rem;color:#22c55e}
  h1{color:#22c55e;margin:.5rem 0}
  p{color:#94a3b8;margin:0}
</style></head>
<body><div class="card">
  <div class="icon">&#10003;</div>
  <h1>Token Stored</h1>
  <p>Kite access token saved to database.<br>Tonight's pipeline is ready.<br>
     You can close this tab.</p>
</div></body></html>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        request_token = params.get("request_token", [None])[0]
        status        = params.get("status",        [""]  )[0]

        if status != "success" or not request_token:
            err = f"Zerodha returned status='{status}' without a request_token."
            _result["error"] = err
            body = f"<h1>Login failed</h1><p>{err}</p>".encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            from new_data_ingestion.kite_oauth import exchange_request_token
            exchange_request_token(request_token)
            _result["ok"] = True
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_SUCCESS_HTML)
        except Exception as exc:
            _result["error"] = str(exc)
            body = f"<h1>Exchange failed</h1><p>{exc}</p>".encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # suppress default HTTP log spam


def main():
    from new_data_ingestion.kite_oauth import get_login_url

    print()
    print("=" * 52)
    print("  Kite Token Refresher")
    print("=" * 52)

    login_url = get_login_url()
    print("\nOpening Zerodha login in your browser...")
    print("If the browser does not open, go to:\n")
    print(f"  {login_url}\n")

    webbrowser.open(login_url)

    server = HTTPServer(("127.0.0.1", _CALLBACK_PORT), _CallbackHandler)
    print(f"Waiting for Zerodha callback on http://127.0.0.1:{_CALLBACK_PORT}/ ...")
    print("(Complete the Zerodha login in your browser)\n")

    # handle_request() blocks until exactly ONE request is received (the OAuth callback)
    server.handle_request()
    server.server_close()

    if _result["ok"]:
        print("Token stored successfully.")
        print("Tonight's 10 PM pipeline is ready to authenticate with Kite.")
    else:
        print(f"FAILED: {_result['error']}")
        sys.exit(1)

    print()


if __name__ == "__main__":
    main()
