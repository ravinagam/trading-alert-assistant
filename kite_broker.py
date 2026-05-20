"""
Kite Connect order placement for intraday signals.

Flow per signal:
  1. Check no existing open position for this ticker.
  2. Place MARKET MIS entry — wait for fill price.
  3. Recalculate SL and target from actual fill price (not signal price).
  4. Place SL-M + LIMIT target orders.
  5. Start a background monitor that cancels the orphan order when the
     position closes and records how it closed in the trade log.

Requires:
  - kiteconnect:  pip install kiteconnect
  - KITE_API_KEY, KITE_API_SECRET in .env
  - .kite_session generated today:  python kite_auth.py
  - KITE_ENABLED=true in .env

SELL signals: MIS short selling is only permitted for F&O-enabled stocks.
If Kite rejects a SELL order, that signal is skipped automatically.
"""

import logging
import threading
import time
from typing import Optional

from kiteconnect import KiteConnect

import requests

import config
import kite_session
import trade_log
from strategies.base import Signal

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECS = 30


def _send_urgent_alert(message: str) -> None:
    """Send a plain Telegram message for critical broker failures."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": f"URGENT: {message}"},
            timeout=6,
        )
    except Exception:
        pass


def _get_kite() -> Optional[KiteConnect]:
    return kite_session.get()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tick_round(price: float) -> float:
    """Round to nearest 0.10 — valid for NSE tick sizes 0.05 and 0.10."""
    return round(round(price / 0.10) * 0.10, 2)


def _calc_qty(signal: Signal) -> int:
    if config.KITE_FIXED_QTY > 0:
        return config.KITE_FIXED_QTY
    capital = config.PRIORITY_CAPITAL if signal.priority else config.NORMAL_CAPITAL
    return max(1, int(capital / signal.entry))


def _wait_for_fill(kite: KiteConnect, order_id: str, timeout: int = 20) -> Optional[float]:
    """Poll until order is COMPLETE. Returns fill price or None."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for order in kite.orders():
                if order["order_id"] == order_id:
                    if order["status"] == "COMPLETE":
                        return float(order["average_price"])
                    if order["status"] in ("REJECTED", "CANCELLED"):
                        logger.error("[broker] Entry order %s %s", order_id, order["status"])
                        return None
        except Exception as exc:
            logger.warning("[broker] Poll error for %s: %s", order_id, exc)
        time.sleep(1)
    logger.warning("[broker] Entry order %s not filled within %ds", order_id, timeout)
    return None


def _has_open_position(kite: KiteConnect, ticker: str) -> bool:
    try:
        for pos in kite.positions().get("net", []):
            if pos["tradingsymbol"] == ticker and abs(pos["quantity"]) > 0:
                return True
    except Exception as exc:
        logger.warning("[broker] Could not check positions for %s: %s", ticker, exc)
    return False


def _cancel_open_orders(kite: KiteConnect, ticker: str) -> None:
    try:
        for order in kite.orders():
            if (order["tradingsymbol"] == ticker
                    and order["status"] in ("OPEN", "TRIGGER PENDING")):
                try:
                    kite.cancel_order(variety=order["variety"], order_id=order["order_id"])
                    logger.info("[broker] Cancelled orphan order %s for %s",
                                order["order_id"], ticker)
                except Exception as exc:
                    logger.warning("[broker] Could not cancel %s: %s", order["order_id"], exc)
    except Exception as exc:
        logger.error("[broker] Error fetching orders for %s: %s", ticker, exc)


def _get_exit_fill(kite: KiteConnect, order_id: str) -> Optional[float]:
    """Return fill price for a completed order."""
    try:
        for order in kite.orders():
            if order["order_id"] == order_id and order["status"] == "COMPLETE":
                return float(order["average_price"])
    except Exception:
        pass
    return None


def _last_exit_price_from_trades(kite: KiteConnect, ticker: str) -> Optional[float]:
    """Fallback: get the most recent fill price for this ticker from today's trades."""
    try:
        trades = [t for t in kite.trades() if t["tradingsymbol"] == ticker]
        if trades:
            return float(trades[-1]["price"])
    except Exception:
        pass
    return None


# ── Position monitor (OCO + trade log update) ─────────────────────────────────

def _monitor_position(
    kite:            KiteConnect,
    ticker:          str,
    direction:       str,
    fill_price:      float,
    qty:             int,
    entry_order_id:  str,
    sl_order_id:     str,
    target_order_id: str,
) -> None:
    logger.info("[monitor] Watching %s", ticker)
    is_buy = direction == "BUY"

    while True:
        time.sleep(MONITOR_INTERVAL_SECS)
        try:
            positions = kite.positions()
            pos = next(
                (p for p in positions.get("net", []) if p["tradingsymbol"] == ticker),
                None,
            )
            if pos is not None and abs(pos["quantity"]) > 0:
                continue   # still open

            # Position closed — determine how
            sl_fill     = _get_exit_fill(kite, sl_order_id)
            target_fill = _get_exit_fill(kite, target_order_id)

            if target_fill is not None:
                status     = "TARGET_HIT"
                exit_price = target_fill
            elif sl_fill is not None:
                status     = "SL_HIT"
                exit_price = sl_fill
            else:
                status = "KITE_CLOSED"
                pos_exit = None
                if pos is not None:
                    if is_buy and float(pos.get("sell_price") or 0) > 0:
                        pos_exit = float(pos["sell_price"])
                    elif not is_buy and float(pos.get("buy_price") or 0) > 0:
                        pos_exit = float(pos["buy_price"])
                trades_exit = _last_exit_price_from_trades(kite, ticker)
                exit_price  = pos_exit or trades_exit or fill_price
                if pos_exit is None and trades_exit is None:
                    logger.warning("[monitor] %s — exit price not found, falling back to fill %.2f",
                                   ticker, fill_price)

            pnl = (exit_price - fill_price) * qty * (1 if is_buy else -1)

            logger.info("[monitor] %s closed  status=%s  exit=%.2f  P&L=%.2f",
                        ticker, status, exit_price, pnl)

            trade_log.record_exit(entry_order_id, status, exit_price, pnl)
            _cancel_open_orders(kite, ticker)
            break

        except Exception as exc:
            logger.warning("[monitor] Error checking %s: %s", ticker, exc)


# ── Forced square-off (3:10 PM) ───────────────────────────────────────────────

def square_off_all_open() -> None:
    """
    Exit every OPEN position at market before Zerodha's 3:20 PM auto square-off.
    Cancels pending SL/target orders first, then places a MARKET MIS exit.
    Records each exit as BOT_SQUAREDOFF in the trade log.
    """
    if not getattr(config, "KITE_ENABLED", False):
        return

    kite = _get_kite()
    if kite is None:
        logger.warning("[squareoff] No Kite session — cannot square off open positions.")
        _send_urgent_alert("3:10 PM square-off FAILED — no Kite session. Close open positions manually before 3:20 PM!")
        return

    open_trades = [t for t in trade_log.load_today() if t.get("status") == "OPEN"]
    if not open_trades:
        logger.info("[squareoff] No open positions at 3:10 PM — nothing to do.")
        return

    logger.info("[squareoff] Squaring off %d open position(s) to avoid auto square-off charges.",
                len(open_trades))

    for trade in open_trades:
        ticker    = trade["ticker"]
        direction = trade["direction"]
        qty       = trade["qty"]
        is_buy    = direction == "BUY"
        exit_side = kite.TRANSACTION_TYPE_SELL if is_buy else kite.TRANSACTION_TYPE_BUY

        # Cancel pending SL and target before exiting
        _cancel_open_orders(kite, ticker)

        try:
            order_id = kite.place_order(
                variety          = kite.VARIETY_REGULAR,
                exchange         = kite.EXCHANGE_NSE,
                tradingsymbol    = ticker,
                transaction_type = exit_side,
                quantity         = qty,
                product          = kite.PRODUCT_MIS,
                order_type       = kite.ORDER_TYPE_MARKET,
                tag              = "BOT-SQUAREOFF",
            )
            logger.info("[squareoff] Market exit placed  %s  order_id=%s", ticker, order_id)

            exit_price = _wait_for_fill(kite, order_id, timeout=20) or trade["fill_price"]
            pnl        = (exit_price - trade["fill_price"]) * qty * (1 if is_buy else -1)

            trade_log.record_exit(trade["entry_order_id"], "BOT_SQUAREDOFF", exit_price, round(pnl, 2))
            logger.info("[squareoff] %s  exit=%.2f  P&L=%.2f", ticker, exit_price, pnl)

        except Exception as exc:
            logger.error("[squareoff] FAILED to exit %s: %s", ticker, exc)
            _send_urgent_alert(
                f"SQUAREOFF FAILED for {ticker} ({direction} {qty})\n"
                f"Close manually before 3:20 PM!\nError: {exc}"
            )


# ── Main order placement ──────────────────────────────────────────────────────

def place_orders(signal: Signal) -> bool:
    """
    Place entry + SL + target for a signal and start the position monitor.
    Returns True if the entry order was placed successfully.
    """
    if not getattr(config, "KITE_ENABLED", False):
        return False

    kite = _get_kite()
    if kite is None:
        return False

    ticker    = signal.ticker
    direction = signal.direction
    qty       = _calc_qty(signal)
    is_buy    = direction == "BUY"
    entry_side = kite.TRANSACTION_TYPE_BUY  if is_buy else kite.TRANSACTION_TYPE_SELL
    exit_side  = kite.TRANSACTION_TYPE_SELL if is_buy else kite.TRANSACTION_TYPE_BUY

    if _has_open_position(kite, ticker):
        logger.info("[broker] Skipping %s — position already open", ticker)
        return False

    # Tag visible in Kite order book — differentiates bot orders from manual ones
    order_tag = f"BOT-{signal.strategy_name.upper()[:12]}"

    logger.info("[broker] %s  %s x %d | signal entry %.2f | tag=%s",
                direction, ticker, qty, signal.entry, order_tag)

    # ── 1. Entry — LIMIT MIS with 0.5% buffer (Kite blocks pure MARKET orders) ─
    limit_price = round(signal.entry * 1.002 if is_buy else signal.entry * 0.998, 1)
    try:
        entry_id = kite.place_order(
            variety          = kite.VARIETY_REGULAR,
            exchange         = kite.EXCHANGE_NSE,
            tradingsymbol    = ticker,
            transaction_type = entry_side,
            quantity         = qty,
            product          = kite.PRODUCT_MIS,
            order_type       = kite.ORDER_TYPE_LIMIT,
            price            = limit_price,
            tag              = order_tag,
        )
        logger.info("[broker] Entry placed  %s @ %.2f (limit)  order_id=%s",
                    ticker, limit_price, entry_id)
    except Exception as exc:
        logger.error("[broker] Entry FAILED for %s: %s", ticker, exc)
        return False

    # Wait for fill — use actual fill price for SL/target
    fill_price = _wait_for_fill(kite, entry_id, timeout=20) or signal.entry
    atr_val    = signal.atr

    if is_buy:
        sl_price     = _tick_round(fill_price - atr_val * 1.0)
        target_price = _tick_round(fill_price + atr_val * config.RR_RATIO)
    else:
        sl_price     = _tick_round(fill_price + atr_val * 1.0)
        target_price = _tick_round(fill_price - atr_val * config.RR_RATIO)

    logger.info("[broker] Fill %.2f | SL %.2f | Target %.2f", fill_price, sl_price, target_price)

    # ── 2. Stop Loss — SL (limit) with 0.5% buffer beyond trigger ───────────
    # SL-M is rejected by Kite API; SL (limit) requires trigger + limit price.
    # Buffer ensures fill even in fast-moving market after trigger fires.
    # SELL SL (exit long): limit below trigger  |  BUY SL (exit short): limit above trigger
    sl_limit = _tick_round(sl_price * 0.998 if is_buy else sl_price * 1.002)
    sl_id: Optional[str] = None
    try:
        sl_id = kite.place_order(
            variety          = kite.VARIETY_REGULAR,
            exchange         = kite.EXCHANGE_NSE,
            tradingsymbol    = ticker,
            transaction_type = exit_side,
            quantity         = qty,
            product          = kite.PRODUCT_MIS,
            order_type       = kite.ORDER_TYPE_SL,
            trigger_price    = sl_price,
            price            = sl_limit,
            tag              = order_tag,
        )
        logger.info("[broker] SL placed     %s trigger=%.2f limit=%.2f  order_id=%s",
                    ticker, sl_price, sl_limit, sl_id)
    except Exception as exc:
        logger.error("[broker] SL FAILED for %s: %s", ticker, exc)
        _send_urgent_alert(f"SL FAILED for {ticker} ({direction})\nManually place SL now!\nTrigger: {sl_price}  Limit: {sl_limit}\nError: {exc}")

    # ── 3. Target — LIMIT ────────────────────────────────────────────────────
    target_id: Optional[str] = None
    if sl_id is not None:
        try:
            target_id = kite.place_order(
                variety          = kite.VARIETY_REGULAR,
                exchange         = kite.EXCHANGE_NSE,
                tradingsymbol    = ticker,
                transaction_type = exit_side,
                quantity         = qty,
                product          = kite.PRODUCT_MIS,
                order_type       = kite.ORDER_TYPE_LIMIT,
                price            = target_price,
                tag              = order_tag,
            )
            logger.info("[broker] Target placed %s @ %.2f  order_id=%s",
                        ticker, target_price, target_id)
        except Exception as exc:
            logger.error("[broker] Target FAILED for %s: %s", ticker, exc)
    else:
        logger.warning("[broker] No SL for %s — close position manually", ticker)

    # ── 4. Log the trade ─────────────────────────────────────────────────────
    trade_log.record_entry(
        ticker          = ticker,
        direction       = direction,
        strategy        = signal.strategy_name,
        signal_entry    = signal.entry,
        fill_price      = fill_price,
        sl_price        = sl_price,
        target_price    = target_price,
        qty             = qty,
        entry_order_id  = entry_id,
        sl_order_id     = sl_id,
        target_order_id = target_id,
    )

    # ── 5. Start monitor (OCO + exit logging) ────────────────────────────────
    if sl_id and target_id:
        threading.Thread(
            target  = _monitor_position,
            args    = (kite, ticker, direction, fill_price, qty,
                       entry_id, sl_id, target_id),
            daemon  = True,
            name    = f"monitor-{ticker}",
        ).start()


# ── Restart monitors after scanner restart ────────────────────────────────────

def restart_monitors() -> None:
    """
    On scanner startup, re-attach monitor threads to any OPEN positions
    that have SL + target orders still pending. Without this, a restart
    leaves orphan orders uncancelled when SL/target fires.
    """
    if not getattr(config, "KITE_ENABLED", False):
        return

    kite = _get_kite()
    if kite is None:
        return

    open_trades = [t for t in trade_log.load_today()
                   if t.get("status") == "OPEN"
                   and t.get("sl_order_id")
                   and t.get("target_order_id")]

    if not open_trades:
        return

    # Only watch positions that are still live on Kite
    try:
        live = {p["tradingsymbol"] for p in kite.positions().get("net", [])
                if abs(p["quantity"]) > 0}
    except Exception as exc:
        logger.warning("[restart_monitors] Could not fetch positions: %s", exc)
        return

    for t in open_trades:
        ticker = t["ticker"]
        if ticker not in live:
            continue
        thread_name = f"monitor-{ticker}"
        if any(th.name == thread_name for th in threading.enumerate()):
            continue  # monitor already running

        logger.info("[restart_monitors] Restarting monitor for %s", ticker)
        threading.Thread(
            target  = _monitor_position,
            args    = (kite, ticker, t["direction"], t["fill_price"], t["qty"],
                       t["entry_order_id"], t["sl_order_id"], t["target_order_id"]),
            daemon  = True,
            name    = thread_name,
        ).start()

    return True
