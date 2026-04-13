"""
Main scanner — runs every 5 minutes during NSE market hours (9:15–15:30 IST).

Usage
-----
    python scanner.py

Signals fire when the EMA-crossover strategy triggers on any Nifty 50 stock.
Each unique signal (stock + candle timestamp) is sent only once via Telegram.
"""

import logging
import time
from datetime import datetime, time as dtime

import pytz
import schedule

import config
import data_fetcher
import strategy
import alerter

# ── Logging setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suppress noisy internal logs from tvDatafeed library
logging.getLogger("tvDatafeed").setLevel(logging.CRITICAL)

IST = pytz.timezone(config.TIMEZONE)

# Tracks the last candle timestamp for which a signal was sent per ticker.
# Structure: {ticker: candle_timestamp_str}
_sent: dict[str, str] = {}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _is_market_open() -> bool:
    now = _now_ist()
    market_open  = dtime(config.MARKET_OPEN_H,  config.MARKET_OPEN_M)
    market_close = dtime(config.MARKET_CLOSE_H, config.MARKET_CLOSE_M)
    # Skip weekends
    if now.weekday() >= 5:
        return False
    return market_open <= now.time() <= market_close


def _already_sent(sig: strategy.Signal) -> bool:
    key = sig.ticker
    ts  = str(sig.candle_time)
    return _sent.get(key) == ts


def _mark_sent(sig: strategy.Signal) -> None:
    _sent[sig.ticker] = str(sig.candle_time)


# ── Core scan ────────────────────────────────────────────────────────────────

def run_scan() -> None:
    if not _is_market_open():
        logger.info("Market closed — skipping scan.")
        return

    logger.info("─── Scanning %d Nifty 50 stocks ───", len(config.NIFTY50_STOCKS))
    tick = _now_ist().strftime("%H:%M")

    data = data_fetcher.fetch_all(config.NIFTY50_STOCKS)
    logger.info("Data fetched for %d/%d stocks", len(data), len(config.NIFTY50_STOCKS))

    signals_found = 0
    for ticker, df in data.items():
        sig = strategy.generate_signal(ticker, df)
        if sig is None:
            continue

        signals_found += 1

        if _already_sent(sig):
            logger.info("  [skip-dup] %s %s @ %s", sig.direction, sig.ticker, sig.candle_time)
            continue

        logger.info(
            "  [SIGNAL] %s %s | Entry: %.2f | SL: %.2f | Target: %.2f",
            sig.direction, sig.ticker, sig.entry, sig.stop_loss, sig.target,
        )
        sent = alerter.send_alert(sig)
        if sent:
            _mark_sent(sig)

    if signals_found == 0:
        logger.info("  No signals this scan (%s IST)", tick)
    logger.info("─── Scan complete ───")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("Trading Alert Scanner started.")
    logger.info("Stocks  : Nifty 50 (%d tickers)", len(config.NIFTY50_STOCKS))
    logger.info("Strategy: EMA %d/%d/%d | ATR %d | RR %.1f",
                config.FAST_EMA, config.SLOW_EMA, config.TREND_EMA,
                config.ATR_LEN, config.RR_RATIO)
    tg = f"chat_id={config.TELEGRAM_CHAT_ID}" if config.TELEGRAM_CHAT_ID else "(not configured)"
    logger.info("Alerts  : Telegram %s", tg)
    logger.info("Press Ctrl+C to stop.\n")

    # Run immediately on start, then every 5 minutes
    run_scan()
    schedule.every(10).minutes.do(run_scan)

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Scanner stopped by user.")


if __name__ == "__main__":
    main()
