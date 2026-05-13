import os
from dotenv import load_dotenv

load_dotenv()

# --- Watchlist: Nifty 50 core + backtest-validated additions (NSE tickers, as of May 2026) ---
# Removed: BAJAJ-AUTO, HINDALCO, KOTAKBANK, TATASTEEL (failed both strategy backtests)
# Added: BHEL, NHPC, SIEMENS, ZYDUSLIFE (both pass) + 13 single-strategy-pass stocks
NIFTY50_STOCKS = [
    # Nifty 50 core
    "ADANIENT", "ADANIPORTS", "ASIANPAINT", "AXISBANK",
    "BAJAJFINSV", "BAJFINANCE", "BEL", "BPCL",
    "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDUNILVR", "ICICIBANK", "INDUSINDBK", "INFY",
    "ITC", "LT", "M&M", "MARUTI",
    "NTPC", "ONGC", "POWERGRID", "SBIN",
    "SHRIRAMFIN", "TATACONSUM", "TMCV", "TMPV",
    "TECHM", "TRENT", "ULTRACEMCO", "WIPRO",
    # Backtest-validated additions (both strategies pass)
    "BHEL", "NHPC", "SIEMENS", "ZYDUSLIFE",
    # Backtest-validated additions (one strategy pass)
    "BRITANNIA", "CANBK", "DIVISLAB", "DMART",
    "HAL", "IOC", "MOTHERSON", "NESTLEIND",
    "PERSISTENT", "PFC", "RECLTD", "SAIL", "SUNPHARMA",
]
NSE_EXCHANGE = "NSE"

# --- High ATR stocks — priority flag in Telegram alert ---
HIGH_ATR_STOCKS = {
    # Banking & Finance
    "AXISBANK", "INDUSINDBK", "ICICIBANK", "HDFCBANK",
    "BAJFINANCE", "BAJAJFINSV", "SHRIRAMFIN",
    # IT
    "INFY", "TECHM", "HCLTECH", "WIPRO", "PERSISTENT",
    # Auto & Capital Goods
    "TMCV", "TMPV", "M&M", "EICHERMOT", "MOTHERSON",
    # Pharma
    "SUNPHARMA", "DIVISLAB", "ZYDUSLIFE",
    # Capital Goods & Infra
    "SIEMENS", "LT", "HAL",
    # Energy & Metals
    "ADANIENT", "ADANIPORTS",
}

# --- Strategy parameters (mirror your Pine Script) ---
FAST_EMA    = 9
SLOW_EMA    = 21
TREND_EMA   = 50
ATR_LEN     = 14
RR_RATIO    = 3.0
VOLUME_SMA  = 20

# --- Data source toggle ---
# "kite" → Zerodha Kite Historical API (requires kite_auth.py each morning)
# "tv"   → TradingView via tvDatafeed (no extra auth, anonymous)
DATA_SOURCE = os.getenv("DATA_SOURCE", "kite")

# --- Zerodha Kite Connect (data + auto order placement) ---
# Subscribe at developers.kite.trade (~Rs 2000/month)
# Set KITE_ENABLED=true in .env only after running: python kite_auth.py
KITE_ENABLED    = os.getenv("KITE_ENABLED",    "false").lower() == "true"
KITE_API_KEY    = os.getenv("KITE_API_KEY",    "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")

# Fixed order quantity (set to 0 to use capital-based sizing instead)
KITE_FIXED_QTY  = int(os.getenv("KITE_FIXED_QTY", "1"))

# --- TradingView login (optional but gives more data / no rate limits) ---
# Leave blank for anonymous access (slightly limited but works for Nifty 50)
TV_USERNAME = os.getenv("TV_USERNAME", "")
TV_PASSWORD = os.getenv("TV_PASSWORD", "")

# --- Telegram settings ---
# Get BOT_TOKEN from @BotFather in Telegram
# Get CHAT_ID by messaging your bot then visiting:
#   https://api.telegram.org/bot<TOKEN>/getUpdates
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Position sizing (fixed capital per trade for 1-week testing) ---
TOTAL_CAPITAL        = 1_000_000   # ₹10 Lakhs total
PRIORITY_CAPITAL     = 100_000     # ₹1.0L per HIGH PRIORITY trade
NORMAL_CAPITAL       = 50_000      # ₹0.5L per normal trade

# --- Market timing (IST) ---
MARKET_OPEN_H,  MARKET_OPEN_M  = 9,  15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30
TIMEZONE = "Asia/Kolkata"

# Bars to fetch per stock (300 bars ≈ 4 trading days of 5-min data)
# Needed for EMA 50 warm-up: minimum ~55 bars, 300 gives stable values
FETCH_BARS = 300
