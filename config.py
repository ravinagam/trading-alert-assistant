import os
from dotenv import load_dotenv

load_dotenv()

# --- Nifty 100 symbols (plain NSE symbol — same as TradingView) ---
# Note: M&M is listed as 'MM' on TradingView/NSE
NIFTY50_STOCKS = [
    # Nifty 50
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT",
    "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
    "BPCL", "BHARTIARTL", "BRITANNIA", "CIPLA",
    "COALINDIA", "DIVISLAB", "DRREDDY", "EICHERMOT",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
    "ITC", "INDUSINDBK", "INFY", "JSWSTEEL",
    "KOTAKBANK", "LT", "LTIM", "MM",
    "MARUTI", "NTPC", "NESTLEIND", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TATAMOTORS",
    "TATASTEEL", "TECHM", "TITAN", "ULTRACEMCO",
    "UPL", "WIPRO",
    # Nifty 100 (additional 50)
    "ABB", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "BAJAJHFL", "BANKBARODA", "BEL", "BHEL",
    "BOSCHLTD", "CANBK", "CHOLAFIN", "CUMMINSIND",
    "DLF", "GAIL", "GODREJCP", "HAVELLS",
    "ICICIGI", "ICICIPRULI", "INDUSTOWER", "IOC",
    "IRCTC", "JINDALSTEL", "LICI", "MARICO",
    "MAXHEALTH", "MUTHOOTFIN", "NHPC", "NMDC",
    "OBEROIRLTY", "OFSS", "PAGEIND", "PERSISTENT",
    "PETRONET", "PFC", "PIDILITIND", "PNB",
    "RECLTD", "SAIL", "SHRIRAMFIN", "SIEMENS",
    "SRF", "TATAPOWER", "TORNTPHARM", "TRENT",
    "VEDL", "ZOMATO", "ZYDUSLIFE", "MCDOWELL-N",
]
NSE_EXCHANGE = "NSE"

# --- Strategy parameters (mirror your Pine Script) ---
FAST_EMA    = 9
SLOW_EMA    = 21
TREND_EMA   = 50
ATR_LEN     = 14
RR_RATIO    = 2.0
VOLUME_SMA  = 20

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

# --- Market timing (IST) ---
MARKET_OPEN_H,  MARKET_OPEN_M  = 9,  15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30
TIMEZONE = "Asia/Kolkata"

# Bars to fetch per stock (300 bars ≈ 4 trading days of 5-min data)
# Needed for EMA 50 warm-up: minimum ~55 bars, 300 gives stable values
FETCH_BARS = 300
