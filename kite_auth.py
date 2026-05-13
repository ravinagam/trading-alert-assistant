"""
Kite Connect daily authentication — run once each morning before starting the scanner.
Saves the access token to .kite_session for use by kite_broker.py.

Usage:
    python kite_auth.py

Steps:
  1. Browser opens the Zerodha login page.
  2. You log in with your Zerodha credentials + 2FA.
  3. Zerodha redirects to your configured redirect URL with ?request_token=XXX in it.
  4. Paste that full URL (or just the token value) here.
  5. Access token is saved to .kite_session and is valid until ~6 AM tomorrow.
"""

import json
import sys
import webbrowser
from datetime import date
from urllib.parse import parse_qs, urlparse

from kiteconnect import KiteConnect

import config


def _extract_token(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http"):
        parsed = urlparse(raw)
        params = parse_qs(parsed.query)
        tokens = params.get("request_token", [])
        if tokens:
            return tokens[0]
        raise ValueError("Could not find request_token in URL — paste the full redirect URL.")
    return raw


def main() -> None:
    api_key    = config.KITE_API_KEY
    api_secret = config.KITE_API_SECRET

    if not api_key or not api_secret:
        print("ERROR: KITE_API_KEY and KITE_API_SECRET must be set in your .env file.")
        sys.exit(1)

    kite      = KiteConnect(api_key=api_key)
    login_url = kite.login_url()

    print("\nKite Connect — Daily Authentication")
    print("=" * 45)
    print("\nStep 1: Opening Zerodha login in your browser...")
    webbrowser.open(login_url)
    print(f"        (If browser did not open, visit: {login_url})")
    print("\nStep 2: Log in with your Zerodha ID + password + 2FA pin.")
    print("\nStep 3: After login, Zerodha redirects to a URL like:")
    print("        https://127.0.0.1/?request_token=AbCdEf123&action=login&status=success")
    print("        Copy that full URL from your browser address bar.")
    print()

    raw = input("Paste the redirect URL (or just the request_token value): ").strip()

    try:
        request_token = _extract_token(raw)
    except ValueError as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)

    try:
        session      = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session["access_token"]
    except Exception as exc:
        print(f"\nERROR: Could not generate session — {exc}")
        sys.exit(1)

    session_data = {"access_token": access_token, "date": str(date.today())}
    with open(".kite_session", "w") as f:
        json.dump(session_data, f)

    print(f"\nAccess token saved for {date.today()}.")
    print("Kite broker is ready. Start the scanner:  python scanner.py\n")


if __name__ == "__main__":
    main()
