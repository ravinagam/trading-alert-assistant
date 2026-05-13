"""
Full Nifty 50 backtest — ORB + VWAP with EMA9>SMA20 confluence.
Fetches data once, runs both strategies, prints a ranked summary table.

Usage:
    python nifty50_backtest.py              # 6 months (130 trading days)
    python nifty50_backtest.py --days 60    # 60 days
"""

import argparse
import sys

import config
import data_fetcher
from backtest import backtest_stock, _build_signal_fn
from strategies.registry import get_params

BARS_PER_DAY = 75
BREAK_EVEN   = 25.0   # % win rate for RR 1:3
MIN_SIGNALS  = 5      # ignore stocks with too few signals

GREEN = "\033[92m"
RED   = "\033[91m"
BOLD  = "\033[1m"
CYAN  = "\033[96m"
RESET = "\033[0m"


def run_all(days: int) -> None:
    bars   = days * BARS_PER_DAY
    stocks = config.NIFTY50_STOCKS

    print(f"\n{BOLD}Nifty 50 Backtest  |  ORB + VWAP (EMA9>SMA20 confluence){RESET}")
    print(f"Period  : Last {days} trading days  (~{days//5} calendar weeks)")
    print(f"Stocks  : {len(stocks)}")
    print(f"SL/RR   : 1xATR SL  |  3xATR Target  |  Break-even {BREAK_EVEN}%")
    print(f"\nFetching data for {len(stocks)} stocks ... this takes 2-4 minutes.\n")

    original_bars    = config.FETCH_BARS
    config.FETCH_BARS = bars
    data = data_fetcher.fetch_all(stocks, max_workers=2)
    config.FETCH_BARS = original_bars

    failed = [s for s in stocks if s not in data]
    if failed:
        print(f"{RED}Failed to fetch ({len(failed)}): {', '.join(failed)}{RESET}\n")

    # ── run both strategies ───────────────────────────────────────────────────
    summary: dict[str, dict] = {}   # ticker -> {strat -> (win_pct, pnl, n_signals)}

    for strategy in ("orb", "vwap"):
        params    = get_params(strategy)
        signal_fn = _build_signal_fn(strategy)
        for ticker in stocks:
            if ticker not in data:
                continue
            trades = backtest_stock(ticker, data[ticker], signal_fn, params)
            total   = len(trades)
            targets = sum(1 for t in trades if t.outcome == "TARGET")
            pnl     = round(sum(t.pnl for t in trades if t.outcome != "OPEN"), 1)
            win_pct = round(targets / total * 100, 1) if total > 0 else 0.0
            summary.setdefault(ticker, {})[strategy] = (win_pct, pnl, total)

    # ── print summary table ───────────────────────────────────────────────────
    HDR = (f"{'Stock':<14}  "
           f"{'ORB Win%':>8}  {'ORB P&L':>8}  {'Sigs':>5}  {'':4}  "
           f"{'VWAP Win%':>9}  {'VWAP P&L':>8}  {'Sigs':>5}  {'':5}  Both")
    SEP = "-" * 88

    print(f"\n{BOLD}{HDR}{RESET}")
    print(SEP)

    orb_pass  = 0
    vwap_pass = 0
    both_pass = 0
    one_pass  = 0
    rows       = []

    for ticker in stocks:
        if ticker not in summary:
            rows.append((ticker, None, None))
            continue
        r = summary[ticker]
        orb_w,  orb_pnl,  orb_n  = r.get("orb",  (0.0, 0.0, 0))
        vwap_w, vwap_pnl, vwap_n = r.get("vwap", (0.0, 0.0, 0))

        orb_ok  = orb_w  >= BREAK_EVEN and orb_pnl  > 0 and orb_n  >= MIN_SIGNALS
        vwap_ok = vwap_w >= BREAK_EVEN and vwap_pnl > 0 and vwap_n >= MIN_SIGNALS

        if orb_ok:  orb_pass  += 1
        if vwap_ok: vwap_pass += 1
        if orb_ok and vwap_ok:
            both_pass += 1
            one_pass  += 1
            both_tag   = "BOTH"
        elif orb_ok or vwap_ok:
            one_pass  += 1
            both_tag   = "ONE "
        else:
            both_tag   = "NONE"

        rows.append((ticker, orb_ok, vwap_ok,
                     orb_w, orb_pnl, orb_n,
                     vwap_w, vwap_pnl, vwap_n,
                     both_tag))

    for row in rows:
        ticker = row[0]
        if row[1] is None:
            print(f"  {ticker:<13}  NO DATA")
            continue

        (_, orb_ok, vwap_ok,
         orb_w, orb_pnl, orb_n,
         vwap_w, vwap_pnl, vwap_n,
         both_tag) = row

        orb_mark  = f"{GREEN}PASS{RESET}" if orb_ok  else f"{RED}FAIL{RESET}"
        vwap_mark = f"{GREEN}PASS{RESET}" if vwap_ok else f"{RED}FAIL{RESET}"

        if both_tag == "BOTH":
            btag = f"{GREEN}{BOLD}BOTH{RESET}"
        elif both_tag == "ONE ":
            btag = f"{CYAN}ONE {RESET}"
        else:
            btag = f"{RED}NONE{RESET}"

        orb_c  = GREEN if orb_ok  else RED
        vwap_c = GREEN if vwap_ok else RED

        print(
            f"  {ticker:<13}  "
            f"{orb_c}{orb_w:>7.1f}%  {orb_pnl:>+8.1f}  {orb_n:>5}{RESET}  {orb_mark}  "
            f"{vwap_c}{vwap_w:>8.1f}%  {vwap_pnl:>+8.1f}  {vwap_n:>5}{RESET}  {vwap_mark}  {btag}"
        )

    # ── overall verdict ───────────────────────────────────────────────────────
    n = len([t for t in stocks if t in summary])
    overall_pct = round((orb_pass + vwap_pass) / (2 * n) * 100) if n else 0

    print(f"\n{SEP}")
    print(f"\n{BOLD}SUMMARY  ({days} trading days  |  {n} stocks fetched){RESET}\n")
    print(f"  ORB  pass  : {GREEN if orb_pass/n >= 0.6 else RED}{orb_pass:>2}/{n}  ({round(orb_pass/n*100)}%){RESET}")
    print(f"  VWAP pass  : {GREEN if vwap_pass/n >= 0.6 else RED}{vwap_pass:>2}/{n}  ({round(vwap_pass/n*100)}%){RESET}")
    print(f"  Both pass  : {both_pass:>2}/{n}  ({round(both_pass/n*100)}%)")
    print(f"  One+ pass  : {one_pass:>2}/{n}  ({round(one_pass/n*100)}%)")
    print(f"\n  Combined pass rate : {overall_pct}%  (both strategies together)")

    print(f"\n  {BOLD}VERDICT :{RESET}  ", end="")
    if overall_pct >= 60:
        print(f"{GREEN}{BOLD}GO LIVE  --  {overall_pct}% of stock-strategy combos are profitable.{RESET}")
        print(f"  Activate the scanner from tomorrow with ORB + VWAP on all {n} fetched stocks.")
    elif overall_pct >= 45:
        print(f"{CYAN}{BOLD}BORDERLINE  --  {overall_pct}% pass.{RESET}")
        print(f"  Consider enabling only stocks that pass at least one strategy (ONE or BOTH).")
    else:
        print(f"{RED}{BOLD}REVIEW NEEDED  --  {overall_pct}% pass. Too many stocks underperform.{RESET}")
        print(f"  Tighten filters or reduce the watch list before going live.")

    print(f"\n  Criterion: Win% >= {BREAK_EVEN}%  AND  Net P&L > 0  AND  Signals >= {MIN_SIGNALS}")
    print(f"{SEP}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Nifty 50 full backtest (ORB + VWAP)")
    parser.add_argument("--days", type=int, default=130,
                        help="Trading days to test (default 130 ~ 6 months)")
    args = parser.parse_args()
    run_all(args.days)


if __name__ == "__main__":
    main()
