"""
Main scanner — runs every 5 minutes during NSE market hours (9:15–15:30 IST).

Usage
-----
    python scanner.py

Signals fire when any enabled strategy triggers on a Nifty 50 stock.
Each unique signal (stock + strategy + candle timestamp) is sent only once via Telegram.
"""

import logging
import subprocess
import time
from datetime import datetime, time as dtime, timedelta

import pytz
import schedule

import config
import data_fetcher
import strategies
import alerter
import kite_broker
import trade_log
import event_filter

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

# Tracks the last candle timestamp for which a signal was sent per (ticker, strategy).
# Structure: {(ticker, strategy_name): candle_timestamp_str}
_sent: dict[tuple[str, str], str] = {}

# Tracks the last alert time per (ticker, strategy) for 30-min cooldown.
# Structure: {(ticker, strategy_name): datetime}
_last_alert_time: dict[tuple[str, str], datetime] = {}

COOLDOWN_MINUTES = 15


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


def _already_sent(sig: strategies.Signal) -> bool:
    key = (sig.ticker, sig.strategy_name)
    return _sent.get(key) == str(sig.candle_time)


def _in_cooldown(sig: strategies.Signal) -> bool:
    last = _last_alert_time.get((sig.ticker, sig.strategy_name))
    if last is None:
        return False
    return (_now_ist() - last) < timedelta(minutes=COOLDOWN_MINUTES)


def _mark_sent(sig: strategies.Signal) -> None:
    key = (sig.ticker, sig.strategy_name)
    _sent[key]            = str(sig.candle_time)
    _last_alert_time[key] = _now_ist()


def _already_traded_today(ticker: str) -> bool:
    """Return True if this ticker already has an entry in today's trade log."""
    return any(t["ticker"] == ticker for t in trade_log.load_today())


# ── Core scan ────────────────────────────────────────────────────────────────

def run_scan() -> None:
    if not _is_market_open():
        logger.info("Market closed — skipping scan.")
        return

    active_strategies = strategies.get_active_strategies()
    if not active_strategies:
        logger.warning("No strategies enabled — skipping scan. Use manage_strategies.py to enable one.")
        return

    strategy_names = ", ".join(s.name for s in active_strategies)
    logger.info("─── Scanning %d stocks | Strategies: %s ───", len(config.NIFTY50_STOCKS), strategy_names)
    logger.info("  %-12s | %-4s | %-10s | %s", "STOCK", "SIDE", "PRICE", "Trend | Vol | ATR(%) | ADX | MACD")
    tick = _now_ist().strftime("%H:%M")

    data = data_fetcher.fetch_all(config.NIFTY50_STOCKS, max_workers=4)
    logger.info("Data fetched for %d/%d stocks", len(data), len(config.NIFTY50_STOCKS))

    now_mins = _now_ist().hour * 60 + _now_ist().minute

    signals_found = 0
    for ticker, df in data.items():
        for strat in active_strategies:
            # Per-strategy session window from params
            start_str = strat._p("session_start", "09:30")
            end_str   = strat._p("session_end",   "15:00")
            sh, sm    = map(int, start_str.split(":"))
            eh, em    = map(int, end_str.split(":"))
            if now_mins < sh * 60 + sm:
                logger.info("  [%s] Before session start (%s) — skipping", strat.name, start_str)
                continue
            if now_mins >= eh * 60 + em:
                logger.info("  [%s] After session end (%s) — skipping", strat.name, end_str)
                continue

            sig = strat.generate_signal(ticker, df)
            if sig is None:
                continue

            signals_found += 1

            if _already_sent(sig):
                logger.info("  [skip-dup] %s %s @ %s [%s]",
                            sig.direction, sig.ticker, sig.candle_time, sig.strategy_name)
                continue

            if _in_cooldown(sig):
                logger.info("  [cooldown] %s %s — within 30 min of last alert [%s]",
                            sig.direction, sig.ticker, sig.strategy_name)
                continue

            if _already_traded_today(sig.ticker):
                logger.info("  [daily-limit] %s — already traded today, skipping", sig.ticker)
                continue

            if event_filter.has_event(sig.ticker):
                logger.info("  [event-skip] %s — corporate event today, skipping", sig.ticker)
                continue

            logger.info(
                "  [SIGNAL] %s %s | Entry: %.2f | SL: %.2f | Target: %.2f | Strategy: %s",
                sig.direction, sig.ticker, sig.entry, sig.stop_loss, sig.target, sig.strategy_name,
            )
            alerter.send_alert(sig)
            _mark_sent(sig)
            kite_broker.place_orders(sig)

    if signals_found == 0:
        logger.info("  No signals this scan (%s IST)", tick)
    logger.info("─── Scan complete ───")


# ── Entry point ──────────────────────────────────────────────────────────────

def _prevent_sleep() -> None:
    """Disable Windows sleep and monitor timeout so bot runs uninterrupted."""
    try:
        subprocess.run(["powercfg", "/change", "standby-timeout-ac",  "0"], check=True)
        subprocess.run(["powercfg", "/change", "monitor-timeout-ac",  "0"], check=True)
        logger.info("System sleep disabled — bot will run without interruption.")
    except Exception as exc:
        logger.warning("Could not disable sleep (run as Administrator if needed): %s", exc)


def _square_off_all() -> None:
    """Triggered at 15:10 — exit all open positions before Zerodha's 3:20 auto square-off."""
    logger.info("─── 3:10 PM square-off triggered ───")
    kite_broker.square_off_all_open()
    logger.info("─── Square-off complete ───")


def _load_event_filter() -> None:
    event_filter.load_today()


def _reconcile_open_positions() -> None:
    """On startup, mark any trade-log OPEN positions not found in Kite as KITE_CLOSED."""
    import kite_session
    kite = kite_session.get()
    if kite is None:
        return
    try:
        positions = kite.positions().get("net", [])
        live = {p["tradingsymbol"] for p in positions if abs(p["quantity"]) > 0}
        # Build exit-price lookup from closed positions (quantity == 0)
        closed_pos = {p["tradingsymbol"]: p for p in positions if abs(p["quantity"]) == 0}
    except Exception:
        return

    try:
        today_trades = kite.trades()
    except Exception:
        today_trades = []

    for t in trade_log.load_today():
        if t.get("status") != "OPEN" or t["ticker"] in live:
            continue

        ticker   = t["ticker"]
        is_buy   = t["direction"] == "BUY"
        qty      = t["qty"]

        # Try to get actual exit price: closed position sell/buy price first
        exit_price = None
        cp = closed_pos.get(ticker)
        if cp is not None:
            if is_buy and float(cp.get("sell_price") or 0) > 0:
                exit_price = float(cp["sell_price"])
            elif not is_buy and float(cp.get("buy_price") or 0) > 0:
                exit_price = float(cp["buy_price"])

        # Fallback: most recent trade fill for this ticker
        if exit_price is None:
            fills = [tr["price"] for tr in today_trades if tr["tradingsymbol"] == ticker]
            if fills:
                exit_price = float(fills[-1])

        exit_price = exit_price or t["fill_price"]
        pnl = round((exit_price - t["fill_price"]) * qty * (1 if is_buy else -1), 2)

        trade_log.record_exit(t["entry_order_id"], "KITE_CLOSED", exit_price, pnl)
        logger.info("[reconcile] %s closed on Kite — exit=%.2f  P&L=%.2f", ticker, exit_price, pnl)


def main() -> None:
    _prevent_sleep()
    _load_event_filter()
    _reconcile_open_positions()
    active = strategies.get_active_strategies()
    logger.info("Trading Alert Scanner started.")
    logger.info("Stocks    : Nifty 50 (%d tickers)", len(config.NIFTY50_STOCKS))
    logger.info("Strategies: %d enabled — %s",
                len(active), ", ".join(s.name for s in active) or "none")
    tg = f"chat_id={config.TELEGRAM_CHAT_ID}" if config.TELEGRAM_CHAT_ID else "(not configured)"
    logger.info("Alerts  : Telegram %s", tg)
    logger.info("Press Ctrl+C to stop.\n")

    # Run immediately on start, then every 1 minute
    run_scan()
    schedule.every(1).minutes.do(run_scan)
    schedule.every().day.at("15:10").do(_square_off_all)

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Scanner stopped by user.")


if __name__ == "__main__":
    main()
