"""
Sends trading signal alerts via Telegram Bot API.
Uses HTML formatting for clean, readable mobile notifications.
"""

import logging

import requests

import config
from strategies.base import Signal

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _calc_qty(signal: Signal) -> tuple[int, float]:
    if config.KITE_FIXED_QTY > 0:
        qty = config.KITE_FIXED_QTY
    else:
        capital_budget = config.PRIORITY_CAPITAL if signal.priority else config.NORMAL_CAPITAL
        qty = max(1, int(capital_budget / signal.entry))
    capital = round(qty * signal.entry, 2)
    return qty, capital


def _build_message(signal: Signal) -> str:
    candle_str   = signal.candle_time.strftime("%d-%b-%Y  %H:%M IST")
    qty, capital = _calc_qty(signal)
    max_loss     = round(qty * signal.atr, 2)
    max_profit   = round(qty * signal.atr * config.RR_RATIO, 2)

    header        = "BUY SIGNAL" if signal.direction == "BUY" else "SELL SIGNAL"
    priority_line = "⭐ <b>HIGH PRIORITY</b>  —  High ATR Stock\n" if signal.priority else ""

    return (
        f"{priority_line}"
        f"<b>{header}  —  {signal.ticker} (NSE)</b>\n"
        f"─────────────────────────\n"
        f"💰 <b>Entry</b>     :  ₹{signal.entry}\n"
        f"🎯 <b>Target</b>    :  ₹{signal.target}  <i>(+{signal.reward_pct}%)</i>\n"
        f"🛑 <b>Stop Loss</b>  :  ₹{signal.stop_loss}  <i>(-{signal.risk_pct}%)</i>\n"
        f"─────────────────────────\n"
        f"📦 <b>Qty</b>       :  {qty} shares\n"
        f"💵 <b>Capital</b>   :  ₹{capital:,.0f}\n"
        f"─────────────────────────\n"
        f"📉 <b>Max Loss</b>  :  ₹{max_loss:,.0f}\n"
        f"📈 <b>Max Profit</b>:  ₹{max_profit:,.0f}\n"
        f"─────────────────────────\n"
        f"📅 <b>Candle</b>    :  {candle_str}  (5-min)\n"
        f"📊 <b>ATR</b>       :  ₹{signal.atr}\n"
        f"⚖️ <b>R : R</b>    :  1 : {config.RR_RATIO}\n"
        f"─────────────────────────\n"
        f"<i>{signal.footer}</i>\n"
        f"<i>Strategy: {signal.strategy_name}</i>"
    )


def send_alert(signal: Signal) -> bool:
    """
    Send a Telegram message for the given signal.
    Returns True on success, False on failure.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error(
            "Telegram credentials not configured. "
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your .env file."
        )
        return False

    url  = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN)
    text = _build_message(signal)

    for attempt in range(2):
        try:
            resp = requests.post(
                url,
                data={
                    "chat_id":    config.TELEGRAM_CHAT_ID,
                    "text":       text,
                    "parse_mode": "HTML",
                },
                timeout=6,
            )
            resp.raise_for_status()
            logger.info("Telegram alert sent: %s %s", signal.direction, signal.ticker)
            return True

        except requests.exceptions.HTTPError as exc:
            body = exc.response.json() if exc.response else {}
            logger.error("Telegram API error for %s: %s — %s",
                         signal.ticker, exc, body.get("description", ""))
            break  # HTTP errors won't improve on retry
        except requests.exceptions.RequestException as exc:
            logger.warning("Telegram attempt %d failed for %s: %s",
                           attempt + 1, signal.ticker, exc)

    return False
