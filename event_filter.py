"""
Fetches today's corporate events from NSE (results, board meetings, dividends, splits).
Used by scanner to skip stocks with scheduled events — price behavior is unpredictable
on event days and increases SL hit probability.

Falls back to empty set on any network/parse error so trading is never blocked.
"""

import logging
from datetime import date

import requests

import config

logger = logging.getLogger(__name__)

_NSE_BASE    = "https://www.nseindia.com"
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}

_event_tickers: set[str] = set()


def load_today() -> set[str]:
    """
    Fetch NSE corporate actions for today and return set of tickers to skip.
    Result is cached for the session — call once on startup.
    """
    global _event_tickers
    today_str = date.today().strftime("%d-%m-%Y")

    session = requests.Session()
    try:
        # Seed NSE cookies (required before hitting the API)
        session.get(_NSE_BASE, headers=_NSE_HEADERS, timeout=10)

        resp = session.get(
            f"{_NSE_BASE}/api/corporate-announcements"
            f"?index=equities&from_date={today_str}&to_date={today_str}",
            headers=_NSE_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json()

        watchlist = set(config.NIFTY50_STOCKS)
        _event_tickers = {
            item["symbol"]
            for item in events
            if isinstance(item, dict) and item.get("symbol") in watchlist
        }

        if _event_tickers:
            logger.info("[events] Skipping today (corporate event): %s",
                        ", ".join(sorted(_event_tickers)))
        else:
            logger.info("[events] No corporate events today for watchlist stocks.")

    except Exception as exc:
        logger.warning("[events] Could not fetch NSE corporate events: %s — skipping filter.", exc)
        _event_tickers = set()

    return _event_tickers


def has_event(ticker: str) -> bool:
    """Return True if ticker has a corporate event today."""
    return ticker in _event_tickers
