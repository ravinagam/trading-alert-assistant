"""
Shared Kite Connect session — loaded once per process, reused by
data_fetcher.py and kite_broker.py.

Both modules import kite_session.get() instead of managing their own
KiteConnect instances, so the .kite_session file is read only once.
"""

import json
import logging
import threading
from datetime import date
from typing import Optional

from kiteconnect import KiteConnect

import config

logger   = logging.getLogger(__name__)
_SESSION_FILE = ".kite_session"
_kite:  Optional[KiteConnect] = None
_lock   = threading.Lock()


def get() -> Optional[KiteConnect]:
    """Return a ready KiteConnect instance, or None if session is missing/stale."""
    global _kite
    with _lock:
        if _kite is not None:
            return _kite

        try:
            with open(_SESSION_FILE) as f:
                session = json.load(f)
        except FileNotFoundError:
            logger.error("No Kite session found. Run:  python kite_auth.py")
            return None

        if session.get("date") != str(date.today()):
            logger.error(
                "Kite session is stale (from %s). Run:  python kite_auth.py",
                session.get("date"),
            )
            return None

        kite = KiteConnect(api_key=config.KITE_API_KEY)
        kite.set_access_token(session["access_token"])
        _kite = kite
        logger.info("Kite session ready for %s", date.today())
        return kite


def reset() -> None:
    """Force reload on next get() call — useful in tests."""
    global _kite
    with _lock:
        _kite = None
