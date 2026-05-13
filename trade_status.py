"""
Live trade summary — run any time during or after market hours.

Usage:
    python trade_status.py              # today only
    python trade_status.py --days 5     # last 5 calendar days
    python trade_status.py --days 30    # last 30 days

Shows:
  - Open positions with live P&L (today only)
  - All trades grouped by day
  - Strategy performance table (win %, P&L per strategy)
  - Day-by-day P&L and overall totals
"""

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta

import kite_session
import trade_log

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"

SEP   = "=" * 90
LINE  = "-" * 90
SLINE = "-" * 50


def _color_pnl(pnl: float) -> str:
    s = f"{pnl:+.2f}"
    if pnl > 0:  return f"{GREEN}{BOLD}{s}{RESET}"
    if pnl < 0:  return f"{RED}{BOLD}{s}{RESET}"
    return s


def _status_label(status: str) -> str:
    if status == "TARGET_HIT":  return f"{GREEN}TARGET HIT {RESET}"
    if status == "SL_HIT":      return f"{RED}SL HIT     {RESET}"
    if status == "KITE_CLOSED": return f"{YELLOW}KITE CLOSED{RESET}"
    if status == "OPEN":        return f"{CYAN}OPEN       {RESET}"
    return f"{status:<11}"


def _fmt_date(yyyymmdd: str) -> str:
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%d-%b-%Y")
    except Exception:
        return yyyymmdd


# ── Kite live data ────────────────────────────────────────────────────────────

def _live_positions(kite) -> dict[str, dict]:
    try:
        return {p["tradingsymbol"]: p
                for p in kite.positions().get("net", [])
                if abs(p["quantity"]) > 0}
    except Exception:
        return {}


def _ltp(kite, tickers: list[str]) -> dict[str, float]:
    if not tickers or kite is None:
        return {}
    try:
        data = kite.ltp([f"NSE:{t}" for t in tickers])
        return {t: data[f"NSE:{t}"]["last_price"]
                for t in tickers if f"NSE:{t}" in data}
    except Exception:
        return {}


# ── Sections ─────────────────────────────────────────────────────────────────

def _print_open_positions(open_trades: list[dict], kite) -> float:
    print(f"\n  {BOLD}OPEN POSITIONS  ({len(open_trades)}){RESET}\n")

    if not open_trades:
        print(f"  {DIM}No open positions.{RESET}")
        return 0.0

    positions = _live_positions(kite) if kite else {}
    ltp_map   = _ltp(kite, [t["ticker"] for t in open_trades])

    print(f"  {'Stock':<12} {'Dir':<5} {'Qty':>3} {'Fill':>8} {'SL':>8} "
          f"{'Target':>8} {'LTP':>8} {'Unreal P&L':>11} {'Strategy':<12} Opened")
    print(f"  {'-'*12} {'-'*5} {'-'*3} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*11} {'-'*12} {'-'*15}")

    total_unreal = 0.0
    for t in open_trades:
        ticker = t["ticker"]
        ltp    = ltp_map.get(ticker)
        pos    = positions.get(ticker)

        if pos is not None:
            unreal = float(pos.get("pnl", 0))
        elif ltp is not None:
            mult   = 1 if t["direction"] == "BUY" else -1
            unreal = (ltp - t["fill_price"]) * t["qty"] * mult
        else:
            unreal = 0.0

        total_unreal += unreal
        ltp_str = f"{ltp:.2f}" if ltp else "   --   "

        print(f"  {ticker:<12} {t['direction']:<5} {t['qty']:>3} "
              f"{t['fill_price']:>8.2f} {t['sl_price']:>8.2f} {t['target_price']:>8.2f} "
              f"{ltp_str:>8} {_color_pnl(unreal):>20} "
              f"{t.get('strategy',''):12} {t.get('entry_time','')}")

    print(f"\n  Unrealised P&L : {_color_pnl(total_unreal)}")
    return total_unreal


def _print_closed_trades(closed_trades: list[dict], multi_day: bool) -> float:
    print(f"\n  {BOLD}CLOSED TRADES  ({len(closed_trades)}){RESET}\n")

    if not closed_trades:
        print(f"  {DIM}No closed trades.{RESET}")
        return 0.0

    # Group by log_date
    by_day: dict[str, list] = defaultdict(list)
    for t in closed_trades:
        by_day[t.get("log_date", str(date.today()))].append(t)

    total_realized = 0.0

    for day_key in sorted(by_day.keys(), reverse=True):
        day_trades = by_day[day_key]
        day_pnl    = sum(t.get("pnl") or 0 for t in day_trades)
        total_realized += day_pnl

        if multi_day:
            print(f"  {BOLD}{_fmt_date(day_key)}  "
                  f"({len(day_trades)} trades  |  day P&L: {_color_pnl(day_pnl)}){RESET}")

        print(f"  {'Stock':<12} {'Dir':<5} {'Qty':>3} {'Fill':>8} {'Exit':>8} "
              f"{'How':<13} {'P&L':>9} {'Strategy':<12} {'Opened':<16} Closed")
        print(f"  {'-'*12} {'-'*5} {'-'*3} {'-'*8} {'-'*8} {'-'*13} {'-'*9} {'-'*12} {'-'*16} {'-'*15}")

        for t in day_trades:
            pnl        = t.get("pnl") or 0.0
            exit_price = t.get("exit_price") or 0.0
            print(f"  {t['ticker']:<12} {t['direction']:<5} {t['qty']:>3} "
                  f"{t['fill_price']:>8.2f} {exit_price:>8.2f} "
                  f"{_status_label(t.get('status',''))}"
                  f"{_color_pnl(pnl):>18} "
                  f"{t.get('strategy',''):<12} "
                  f"{t.get('entry_time',''):<16} {t.get('exit_time','')}")
        print()

    return total_realized


def _print_strategy_performance(all_trades: list[dict], days: int) -> None:
    closed = [t for t in all_trades if t.get("status") != "OPEN"]
    if not closed:
        return

    # Aggregate per strategy
    stats: dict[str, dict] = defaultdict(lambda: {
        "trades": 0, "target": 0, "sl": 0, "kite": 0, "pnl": 0.0
    })

    for t in closed:
        strat  = t.get("strategy", "unknown")
        status = t.get("status", "")
        pnl    = t.get("pnl") or 0.0
        s      = stats[strat]
        s["trades"] += 1
        s["pnl"]    += pnl
        if status == "TARGET_HIT":  s["target"] += 1
        elif status == "SL_HIT":    s["sl"]     += 1
        else:                       s["kite"]   += 1

    period = "today" if days == 1 else f"last {days} days"
    print(f"\n  {BOLD}STRATEGY PERFORMANCE  ({period}){RESET}\n")
    print(f"  {'Strategy':<16} {'Trades':>7} {'Target':>7} {'SL Hit':>7} "
          f"{'Kite':>6} {'Win %':>7} {'Total P&L':>11}  Verdict")
    print(f"  {'-'*16} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*7} {'-'*11}  {'-'*10}")

    totals = {"trades": 0, "target": 0, "sl": 0, "kite": 0, "pnl": 0.0}

    for strat, s in sorted(stats.items()):
        closed_n = s["target"] + s["sl"] + s["kite"]
        win_pct  = round(s["target"] / closed_n * 100, 1) if closed_n else 0.0
        verdict  = (f"{GREEN}GOOD{RESET}"    if win_pct >= 35 else
                    f"{CYAN}OK{RESET}"       if win_pct >= 25 else
                    f"{RED}REVIEW{RESET}")

        print(f"  {strat:<16} {s['trades']:>7} {s['target']:>7} {s['sl']:>7} "
              f"{s['kite']:>6} {win_pct:>6.1f}% {_color_pnl(s['pnl']):>20}  {verdict}")

        for k in totals:
            totals[k] += s[k]

    # Totals row
    closed_total = totals["target"] + totals["sl"] + totals["kite"]
    win_total    = round(totals["target"] / closed_total * 100, 1) if closed_total else 0.0
    verdict_all  = (f"{GREEN}GOOD{RESET}"  if win_total >= 35 else
                    f"{CYAN}OK{RESET}"     if win_total >= 25 else
                    f"{RED}REVIEW{RESET}")

    print(f"  {SLINE}")
    print(f"  {'TOTAL':<16} {totals['trades']:>7} {totals['target']:>7} {totals['sl']:>7} "
          f"{totals['kite']:>6} {win_total:>6.1f}% {_color_pnl(totals['pnl']):>20}  {verdict_all}")

    print(f"\n  Verdict key:  {GREEN}GOOD{RESET} >= 35%  |  "
          f"{CYAN}OK{RESET} >= 25% (break-even for 1:3 RR)  |  "
          f"{RED}REVIEW{RESET} < 25%")


def _print_day_pnl(all_trades: list[dict]) -> None:
    by_day: dict[str, float] = defaultdict(float)
    for t in all_trades:
        if t.get("status") != "OPEN" and t.get("pnl") is not None:
            by_day[t.get("log_date", str(date.today()))] += t["pnl"]

    if len(by_day) < 2:
        return

    print(f"\n  {BOLD}DAY-BY-DAY P&L{RESET}\n")
    for day_key in sorted(by_day.keys(), reverse=True):
        bar_pnl = by_day[day_key]
        bar     = ("+" * min(int(abs(bar_pnl) / 5), 20)) or "."
        color   = GREEN if bar_pnl >= 0 else RED
        print(f"  {_fmt_date(day_key)}  {color}{bar:<22}{RESET}  {_color_pnl(bar_pnl)}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Trade summary")
    parser.add_argument("--days", type=int, default=1,
                        help="Number of calendar days to show (default: 1 = today)")
    args  = parser.parse_args()
    days  = max(1, args.days)
    today = date.today()
    start = today - timedelta(days=days - 1)

    kite = kite_session.get()
    if kite is None and days == 1:
        print(f"\n{YELLOW}Kite session unavailable — live P&L not shown. "
              f"Run: python kite_auth.py{RESET}")

    all_trades = trade_log.load_days(days)

    print(f"\n{SEP}")
    if days == 1:
        print(f"  {BOLD}Trade Summary  —  {today.strftime('%d-%b-%Y')}{RESET}")
    else:
        print(f"  {BOLD}Trade Summary  —  "
              f"{start.strftime('%d-%b-%Y')} to {today.strftime('%d-%b-%Y')}  "
              f"({days} days){RESET}")
    print(SEP)

    if not all_trades:
        print(f"\n  No trades found in this period.\n{SEP}\n")
        return

    open_trades   = [t for t in all_trades
                     if t.get("status") == "OPEN"
                     and t.get("log_date") == str(today)]
    closed_trades = [t for t in all_trades if t.get("status") != "OPEN"]
    multi_day     = days > 1

    # ── Open positions (today only) ──────────────────────────────────────────
    unreal_pnl = _print_open_positions(open_trades, kite)

    # ── Closed trades ────────────────────────────────────────────────────────
    print(f"\n{LINE}")
    realized_pnl = _print_closed_trades(closed_trades, multi_day)

    # ── Strategy performance ─────────────────────────────────────────────────
    print(LINE)
    _print_strategy_performance(all_trades, days)

    # ── Day-by-day bar chart (multi-day only) ────────────────────────────────
    if multi_day:
        print(LINE)
        _print_day_pnl(all_trades)

    # ── Overall totals ───────────────────────────────────────────────────────
    total_trades  = len(all_trades)
    target_count  = sum(1 for t in closed_trades if t.get("status") == "TARGET_HIT")
    sl_count      = sum(1 for t in closed_trades if t.get("status") == "SL_HIT")
    kite_count    = sum(1 for t in closed_trades if t.get("status") == "KITE_CLOSED")
    closed_n      = len(closed_trades)
    win_pct       = round(target_count / closed_n * 100, 1) if closed_n else 0.0
    total_pnl     = realized_pnl + unreal_pnl

    print(f"\n{SEP}")
    print(f"\n  {BOLD}OVERALL SUMMARY{RESET}\n")
    print(f"  Total trades    : {total_trades}  "
          f"(open: {len(open_trades)}  |  closed: {closed_n})")
    if closed_n:
        print(f"    Target hit    : {GREEN}{target_count}{RESET}   "
              f"SL hit: {RED}{sl_count}{RESET}   "
              f"Kite/manual: {YELLOW}{kite_count}{RESET}")
        print(f"    Win rate      : {BOLD}{win_pct}%{RESET}  "
              f"(break-even = 25% for 1:3 RR)")
    print(f"\n  Realised P&L    : {_color_pnl(realized_pnl)}")
    if open_trades:
        print(f"  Unrealised P&L  : {_color_pnl(unreal_pnl)}")
    print(f"  {BOLD}Total P&L       : {_color_pnl(total_pnl)}{RESET}")
    print(f"\n{SEP}\n")


if __name__ == "__main__":
    main()
