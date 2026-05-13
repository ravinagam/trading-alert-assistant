"""
Fetches 5-minute OHLCV data for NSE stocks.

Toggle in .env (or config.py):
    DATA_SOURCE=kite   →  Zerodha Kite Historical API  (default)
    DATA_SOURCE=tv     →  TradingView via tvDatafeed

Kite advantages : already authenticated (no separate TV login), official
                  NSE data, no anonymous rate-limit issues.
TV  advantages  : works without a Kite subscription, useful fallback.

Both return the same DataFrame format so no downstream code changes.
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd

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
RETRY_DELAYS = [2, 5, 10]


# ── TradingView implementation ────────────────────────────────────────────────

def _make_tv():
    from tvDatafeed import TvDatafeed
    if config.TV_USERNAME and config.TV_PASSWORD:
        return TvDatafeed(username=config.TV_USERNAME, password=config.TV_PASSWORD)
    return TvDatafeed()


def _fetch_one_tv(symbol: str) -> tuple[str, pd.DataFrame]:
    from tvDatafeed import Interval
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
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                return symbol, pd.DataFrame()

            df = df.drop(columns=["symbol"], errors="ignore")
            df = df.rename(columns=_COL_MAP)
            df = df.dropna(subset=["Close", "Volume"])
            return symbol, df

        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                logger.debug("TV retry %d for %s in %ds: %s", attempt + 1, symbol, wait, exc)
                time.sleep(wait)
            else:
                logger.warning("TV fetch failed for %s: %s", symbol, exc)

    return symbol, pd.DataFrame()


def _fetch_all_tv(symbols: list[str], max_workers: int) -> dict[str, pd.DataFrame]:
    workers = 4 if (config.TV_USERNAME and config.TV_PASSWORD) else max_workers
    results: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one_tv, s): s for s in symbols}
        for future in as_completed(futures):
            symbol, df = future.result()
            if not df.empty:
                results[symbol] = df
    return results


# ── Kite Historical implementation ────────────────────────────────────────────

_token_cache: dict[str, int] = {}   # symbol → instrument_token (loaded once per session)


def _load_tokens() -> dict[str, int]:
    """Fetch NSE instrument token map from Kite (cached after first call)."""
    global _token_cache
    if _token_cache:
        return _token_cache

    import kite_session
    kite = kite_session.get()
    if kite is None:
        return {}

    try:
        instruments = kite.instruments("NSE")
        _token_cache = {
            i["tradingsymbol"]: i["instrument_token"]
            for i in instruments
            if i.get("instrument_type") == "EQ"
        }
        logger.info("Kite: loaded %d NSE instrument tokens", len(_token_cache))
    except Exception as exc:
        logger.error("Kite: failed to load instrument tokens: %s", exc)

    return _token_cache


KITE_MAX_CHUNK_DAYS = 60   # Kite 5-min API limit is 100 days; use 60 for safety


def _fetch_kite_chunk(kite, token: int, from_date: datetime, to_date: datetime) -> list:
    """Fetch one chunk of historical data from Kite with retries."""
    for attempt in range(MAX_RETRIES):
        try:
            return kite.historical_data(
                instrument_token = token,
                from_date        = from_date,
                to_date          = to_date,
                interval         = "5minute",
                continuous       = False,
            )
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAYS[attempt])
            else:
                raise exc
    return []


def _fetch_one_kite(symbol: str, token_map: dict[str, int]) -> tuple[str, pd.DataFrame]:
    import kite_session
    kite = kite_session.get()
    if kite is None:
        return symbol, pd.DataFrame()

    token = token_map.get(symbol)
    if token is None:
        logger.warning("Kite: no instrument token for '%s' — check symbol name", symbol)
        return symbol, pd.DataFrame()

    # Date range: convert FETCH_BARS (5-min bars) to calendar days with buffer
    trading_days = config.FETCH_BARS // 75 + 5
    total_days   = trading_days * 2   # ×2 covers weekends/holidays
    to_date      = datetime.now()
    from_date    = to_date - timedelta(days=total_days)

    try:
        # Paginate if range exceeds Kite's per-request limit
        all_data: list = []
        chunk_end = to_date
        while chunk_end > from_date:
            chunk_start = max(from_date, chunk_end - timedelta(days=KITE_MAX_CHUNK_DAYS))
            chunk = _fetch_kite_chunk(kite, token, chunk_start, chunk_end)
            all_data = chunk + all_data   # prepend older data
            chunk_end = chunk_start - timedelta(seconds=1)
            if chunk_start <= from_date:
                break
            time.sleep(0.4)   # stay within 3 req/sec limit

        if not all_data:
            return symbol, pd.DataFrame()

        df = pd.DataFrame(all_data)
        df = df.rename(columns={"date": "datetime", **_COL_MAP})
        df = df.set_index("datetime")
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        df = df[~df.index.duplicated(keep="last")]
        df = df.sort_index()
        df = df.tail(config.FETCH_BARS)   # cap to requested bars
        return symbol, df

    except Exception as exc:
        logger.warning("Kite fetch failed for %s: %s", symbol, exc)
        return symbol, pd.DataFrame()


def _fetch_all_kite(symbols: list[str], max_workers: int) -> dict[str, pd.DataFrame]:
    token_map = _load_tokens()
    if not token_map:
        logger.error("Kite: no instrument tokens — falling back to TV")
        return _fetch_all_tv(symbols, max_workers)

    results: dict[str, pd.DataFrame] = {}
    # Cap workers at 3 to stay within Kite's 3 req/sec historical API limit
    workers = min(max_workers, 3)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one_kite, s, token_map): s for s in symbols}
        for future in as_completed(futures):
            symbol, df = future.result()
            if not df.empty:
                results[symbol] = df
    return results


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_all(symbols: list[str], max_workers: int = 2) -> dict[str, pd.DataFrame]:
    """
    Fetch 5-min OHLCV for all symbols.

    Routes to Kite Historical or TradingView based on config.DATA_SOURCE:
        DATA_SOURCE=kite  →  Kite (default)
        DATA_SOURCE=tv    →  TradingView
    """
    source = getattr(config, "DATA_SOURCE", "kite").lower()

    if source == "kite":
        logger.debug("fetch_all: using Kite Historical API")
        return _fetch_all_kite(symbols, max_workers)

    logger.debug("fetch_all: using TradingView (tvDatafeed)")
    return _fetch_all_tv(symbols, max_workers)
