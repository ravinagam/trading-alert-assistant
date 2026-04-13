"""
Sends trading signal alerts via Telegram Bot API.
Uses HTML formatting for clean, readable mobile notifications.
"""

import logging

import requests

import config
from strategy import Signal

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _build_message(signal: Signal) -> str:
    candle_str = signal.candle_time.strftime("%d-%b-%Y  %H:%M IST")

    if signal.direction == "BUY":
        header  = "🟢 <b>BUY SIGNAL</b>"
        sl_icon = "🛑"
        tg_icon = "🎯"
    else:
        header  = "🔴 <b>SELL SIGNAL</b>"
        sl_icon = "🛑"
        tg_icon = "🎯"

    return (
        f"{header}  —  <b>{signal.ticker}</b> (NSE)\n"
        f"─────────────────────────\n"
        f"📅 <b>Candle</b>  :  {candle_str}  (5-min)\n"
        f"📊 <b>ATR</b>     :  ₹{signal.atr}\n\n"
        f"💰 <b>Entry</b>    :  ₹{signal.entry}\n"
        f"{tg_icon} <b>Target</b>   :  ₹{signal.target}  "
        f"<i>(+{signal.reward_pct}%)</i>\n"
        f"{sl_icon} <b>Stop Loss</b> :  ₹{signal.stop_loss}  "
        f"<i>(-{signal.risk_pct}%)</i>\n\n"
        f"⚖️ <b>R : R</b>   :  1 : {config.RR_RATIO}\n"
        f"─────────────────────────\n"
        f"<i>EMA {config.FAST_EMA}/{config.SLOW_EMA} cross · "
        f"EMA {config.TREND_EMA} trend · Vol filter</i>"
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

    try:
        resp = requests.post(
            url,
            data={
                "chat_id":    config.TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Telegram alert sent: %s %s", signal.direction, signal.ticker)
        return True

    except requests.exceptions.HTTPError as exc:
        body = exc.response.json() if exc.response else {}
        logger.error(
            "Telegram API error for %s: %s — %s",
            signal.ticker, exc, body.get("description", "")
        )
    except requests.exceptions.RequestException as exc:
        logger.error("Network error sending Telegram alert for %s: %s", signal.ticker, exc)

    return False
