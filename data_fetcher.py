"""
Fetches 5-minute OHLCV data directly from TradingView via tvDatafeed.
Same data source the user already sees in their TradingView charts.

Rate-limit handling
-------------------
TradingView throttles anonymous sessions aggressively when many parallel
WebSocket connections are opened. We use:
  - max_workers=2  (low parallelism)
  - per-request retry with exponential backoff on 429 / connection errors
  - authenticated login (TV_USERNAME/TV_PASSWORD) for higher limits

50 stocks × ~2 s each / 2 workers ≈ 50 s total — well within a 5-min scan.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
from tvDatafeed import TvDatafeed, Interval

import config

logger = logging.getLogger(__name__)

_COL_MAP = {
    "open":   "Open",
    "high":   "High",
    "low":    "Low",
    "close":  "Close",
    "volume": "Volume",
}

MAX_RETRIES  = 3
RETRY_DELAYS = [2, 5, 10]   # seconds between retries


def _make_tv() -> TvDatafeed:
    """Authenticated session if credentials are set, anonymous otherwise."""
    if config.TV_USERNAME and config.TV_PASSWORD:
        return TvDatafeed(username=config.TV_USERNAME, password=config.TV_PASSWORD)
    return TvDatafeed()


def _fetch_one(symbol: str) -> tuple[str, pd.DataFrame]:
    """Fetch 5-min history for one NSE symbol with retry on failure."""
    for attempt in range(MAX_RETRIES):
        try:
            tv = _make_tv()
            df = tv.get_hist(
                symbol=symbol,
                exchange=config.NSE_EXCHANGE,
                interval=Interval.in_5_minute,
                n_bars=config.FETCH_BARS,
            )

            if df is None or df.empty:
                logger.debug("No data for %s (attempt %d)", symbol, attempt + 1)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                return symbol, pd.DataFrame()

            df = df.drop(columns=["symbol"], errors="ignore")
            df = df.rename(columns=_COL_MAP)
            df = df.dropna(subset=["Close", "Volume"])
            return symbol, df

        except Exception as exc:
            err = str(exc)
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                logger.debug("Retry %d for %s in %ds: %s", attempt + 1, symbol, wait, err)
                time.sleep(wait)
            else:
                logger.warning("Failed to fetch %s after %d attempts: %s", symbol, MAX_RETRIES, err)

    return symbol, pd.DataFrame()


def fetch_all(symbols: list[str], max_workers: int = 2) -> dict[str, pd.DataFrame]:
    """
    Fetch 5-min OHLCV for all symbols.
    max_workers=2 keeps us well below TradingView's anonymous rate limit.
    With TV credentials this can safely be raised to 4-5.
    """
    results: dict[str, pd.DataFrame] = {}
    workers = max_workers if not (config.TV_USERNAME and config.TV_PASSWORD) else 4

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in symbols}
        for future in as_completed(futures):
            symbol, df = future.result()
            if not df.empty:
                results[symbol] = df
    return results
