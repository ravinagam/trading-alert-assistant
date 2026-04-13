"""
Run this once to confirm Telegram credentials work.
    python test_telegram.py
"""
import requests
from dotenv import load_dotenv
import config

load_dotenv()

def test_telegram():
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env")
        return

    url  = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "parse_mode": "HTML",
        "text": (
            "✅ <b>Trading Alert Scanner — Connected!</b>\n\n"
            "Your Nifty 50 signal alerts will appear here.\n"
            "<i>EMA 9/21 crossover · EMA 50 trend · ATR stop loss</i>"
        ),
    }, timeout=10)

    if resp.ok:
        print("SUCCESS — check your Telegram, the test message was delivered.")
    else:
        print(f"FAILED — {resp.status_code}: {resp.json()}")

if __name__ == "__main__":
    test_telegram()
