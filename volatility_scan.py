"""
Real-time volatility scanner — ranks all stocks by ATR% using live 5-min data.

Usage
-----
    python volatility_scan.py
    python volatility_scan.py --top 20
"""

import argparse
import logging
import sys
from datetime import datetime

import pytz

import config
import data_fetcher
from strategies.ema_crossover import _atr

logging.getLogger("tvDatafeed").setLevel(logging.CRITICAL)
logging.basicConfig(level=logging.WARNING)

IST = pytz.timezone(config.TIMEZONE)


def run(top_n: int) -> None:
    now = datetime.now(IST).strftime("%d-%b-%Y  %H:%M IST")
    print(f"\n  Fetching data for {len(config.NIFTY50_STOCKS)} stocks...")

    data = data_fetcher.fetch_all(config.NIFTY50_STOCKS, max_workers=4)

    rows = []
    for ticker, df in data.items():
        if len(df) < config.ATR_LEN + 5:
            continue
        price       = round(float(df["Close"].iloc[-2]), 2)
        atr_val     = round(float(_atr(df, config.ATR_LEN).iloc[-2]), 2)
        atr_pct     = round(atr_val / price * 100, 2)
        candle_time = df.index[-2]
        try:
            ct = candle_time.tz_localize(IST) if candle_time.tzinfo is None else candle_time.tz_convert(IST)
            ct_str = ct.strftime("%H:%M IST")
        except Exception:
            ct_str = str(candle_time)[:16]
        rows.append((ticker, price, atr_val, atr_pct, ct_str))

    rows.sort(key=lambda x: x[3], reverse=True)
    if top_n:
        rows = rows[:top_n]

    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

    print(f"\n  {BOLD}NSE Volatility Ranking  —  Data as of {now}{RESET}")
    print(f"  {'Rank':<5} {'Stock':<14} {'Price':>10} {'ATR (Rs)':>10} {'ATR %':>8}  {'Candle'}")
    print(f"  {'-'*5} {'-'*14} {'-'*10} {'-'*10} {'-'*8}  {'-'*10}")

    for i, (ticker, price, atr, atr_pct, ct_str) in enumerate(rows, 1):
        if atr_pct >= 1.0:
            color = RED
        elif atr_pct >= 0.5:
            color = YELLOW
        else:
            color = GREEN
        print(f"  {color}{i:<5} {ticker:<14} {price:>10,.2f} {atr:>10.2f} {atr_pct:>7.2f}%  {ct_str}{RESET}")

    print(f"\n  {RED}RED{RESET}    ATR >= 1.0%  — High volatility")
    print(f"  {YELLOW}YELLOW{RESET} ATR >= 0.5%  — Medium volatility")
    print(f"  {GREEN}GREEN{RESET}  ATR <  0.5%  — Low volatility\n")


def main():
    parser = argparse.ArgumentParser(description="NSE real-time volatility ranking")
    parser.add_argument("--top", type=int, default=0, help="Show only top N stocks (default: all)")
    args = parser.parse_args()
    run(args.top)


if __name__ == "__main__":
    main()
