"""
Backtest any registered strategy on historical 5-min data.

Usage
-----
    python backtest.py                                    # SP EMA, TATAMOTORS, 60 days
    python backtest.py --strategy ema_crossover_pro       # RK PRO strategy
    python backtest.py --strategy orb                     # ORB strategy
    python backtest.py --strategy vwap                    # VWAP strategy
    python backtest.py --days 30                          # 30 days of data
    python backtest.py HDFCBANK TATAMOTORS                # multiple stocks
    python backtest.py HDFCBANK --strategy orb --days 90
"""

import sys
import argparse
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd
import pytz

import config
import data_fetcher
from strategies.ema_crossover import _ema, _atr, _adx, _macd, _crossover, _crossunder
from strategies.vwap import _compute_vwap
from strategies.registry import get_params

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("tvDatafeed").setLevel(logging.CRITICAL)

IST           = pytz.timezone("Asia/Kolkata")
BARS_PER_DAY  = 75   # ~75 five-min candles per trading day


# ── Signal functions — one per strategy ──────────────────────────────────────

def _make_sp_signal_fn(p: dict) -> Callable:
    """SP EMA Crossover — EMA crossover only, no extra filters."""
    def fn(i, ema_fast, ema_slow, ema_trend, vol_sma, atr_series, adx_series, macd_line, macd_sig, df):
        if _crossover(ema_fast, ema_slow, i):  return "BUY"
        if _crossunder(ema_fast, ema_slow, i): return "SELL"
        return None
    return fn


def _make_rk_signal_fn(p: dict) -> Callable:
    """RK EMA Crossover PRO — full filter suite using params from config."""
    min_atr_pct    = p.get("min_atr_pct",    0.2) / 100
    min_adx        = p.get("min_adx",        20)
    min_volume     = p.get("min_volume",      50_000)
    spike_atr_mult = p.get("spike_atr_mult",  2.5)

    def fn(i, ema_fast, ema_slow, ema_trend, vol_sma, atr_series, adx_series, macd_line, macd_sig, df):
        close  = df["Close"]
        volume = df["Volume"]
        price        = float(close.iloc[i])
        atr_val      = float(atr_series.iloc[i])
        adx_val      = float(adx_series.iloc[i])
        vol_cur      = float(volume.iloc[i])
        vol_avg      = float(vol_sma.iloc[i])
        ema_t        = float(ema_trend.iloc[i])
        macd_val     = float(macd_line.iloc[i])
        macd_s       = float(macd_sig.iloc[i])
        atr_pct      = atr_val / price * 100
        candle_range = float(df["High"].iloc[i] - df["Low"].iloc[i])

        cross_up   = _crossover(ema_fast,  ema_slow, i)
        cross_down = _crossunder(ema_fast, ema_slow, i)
        vol_ok     = vol_cur > vol_avg
        vol_min_ok = vol_cur >= min_volume
        atr_ok     = round(atr_pct, 2) >= round(min_atr_pct * 100, 2)
        adx_ok     = adx_val >= min_adx
        not_spike  = candle_range <= spike_atr_mult * atr_val
        macd_bull  = macd_val > macd_s
        macd_bear  = macd_val < macd_s

        if cross_up   and (price > ema_t) and vol_ok and vol_min_ok and macd_bull and atr_ok and adx_ok and not_spike:
            return "BUY"
        if cross_down and (price < ema_t) and vol_ok and vol_min_ok and macd_bear and atr_ok and adx_ok and not_spike:
            return "SELL"
        return None
    return fn


def _make_orb_signal_fn(p: dict) -> Callable:
    """ORB — breakout above/below first-N-min opening range with volume + EMA9>SMA20 confluence."""
    orb_minutes  = p.get("orb_minutes",  15)
    min_volume   = p.get("min_volume",   50_000)
    ema_confirm  = p.get("ema_confirm",  True)
    ema_slow_sma = p.get("ema_slow_sma", 20)
    _cache:     dict = {}   # df id → {date → (orb_high, orb_low)}
    _sma_cache: dict = {}   # df id → sma20 series

    def fn(i, ema_fast, ema_slow, ema_trend, vol_sma, atr_series, adx_series, macd_line, macd_sig, df):
        df_id      = id(df)
        orb_end_m  = 9 * 60 + 15 + orb_minutes

        # Build per-day ORB cache once per df
        if df_id not in _cache:
            day_orb: dict = {}
            for j in range(len(df)):
                ts = df.index[j]
                try:
                    ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
                except Exception:
                    continue
                t_m = ts.hour * 60 + ts.minute
                if 9 * 60 + 15 <= t_m < orb_end_m:
                    d = ts.date()
                    if d not in day_orb:
                        day_orb[d] = [float(df["High"].iloc[j])], [float(df["Low"].iloc[j])]
                    else:
                        day_orb[d][0].append(float(df["High"].iloc[j]))
                        day_orb[d][1].append(float(df["Low"].iloc[j]))
            _cache[df_id] = {d: (max(h), min(l)) for d, (h, l) in day_orb.items()}

        ct = df.index[i]
        try:
            ct = ct.tz_localize(IST) if ct.tzinfo is None else ct.tz_convert(IST)
        except Exception:
            return None

        ct_date = ct.date()
        if ct_date not in _cache[df_id]:
            return None

        orb_high, orb_low = _cache[df_id][ct_date]
        close   = float(df["Close"].iloc[i])
        volume  = float(df["Volume"].iloc[i])
        vol_avg = float(vol_sma.iloc[i])
        vol_ok  = volume > vol_avg and volume >= min_volume

        direction = None
        if   close > orb_high and vol_ok: direction = "BUY"
        elif close < orb_low  and vol_ok: direction = "SELL"
        if direction is None:
            return None

        if ema_confirm:
            if df_id not in _sma_cache:
                _sma_cache[df_id] = df["Close"].rolling(ema_slow_sma).mean()
            ema9_v = float(ema_fast.iloc[i])
            sma_v  = float(_sma_cache[df_id].iloc[i])
            if direction == "BUY"  and ema9_v <= sma_v: return None
            if direction == "SELL" and ema9_v >= sma_v: return None

        return direction

    return fn


def _make_vwap_signal_fn(p: dict) -> Callable:
    """VWAP Crossover — EMA(fast) crosses VWAP with optional trend filter + EMA9>SMA20 confluence."""
    trend_filter = p.get("trend_filter", True)
    ema_confirm  = p.get("ema_confirm",  True)
    ema_slow_sma = p.get("ema_slow_sma", 20)
    _vwap_cache: dict = {}   # df id → vwap_series
    _sma_cache:  dict = {}   # df id → sma20 series

    def fn(i, ema_fast, ema_slow, ema_trend, vol_sma, atr_series, adx_series, macd_line, macd_sig, df):
        df_id = id(df)
        if df_id not in _vwap_cache:
            _vwap_cache[df_id] = _compute_vwap(df)
        vwap = _vwap_cache[df_id]

        cross_up   = _crossover(ema_fast,  vwap, i)
        cross_down = _crossunder(ema_fast, vwap, i)

        if not cross_up and not cross_down:
            return None

        price = float(df["Close"].iloc[i])
        ema_t = float(ema_trend.iloc[i])

        if trend_filter:
            if cross_up   and price < ema_t: return None
            if cross_down and price > ema_t: return None

        direction = "BUY" if cross_up else "SELL"

        if ema_confirm:
            if df_id not in _sma_cache:
                _sma_cache[df_id] = df["Close"].rolling(ema_slow_sma).mean()
            ema9_v = float(ema_fast.iloc[i])
            sma_v  = float(_sma_cache[df_id].iloc[i])
            if direction == "BUY"  and ema9_v <= sma_v: return None
            if direction == "SELL" and ema9_v >= sma_v: return None

        return direction

    return fn


def _make_ema9_sma20_signal_fn(p: dict) -> Callable:
    """EMA(9) x SMA(20) crossover with volume + ADX filters and price confirmation."""
    slow_sma   = p.get("slow_sma",   20)
    min_volume = p.get("min_volume", 50_000)
    min_adx    = p.get("min_adx",    20)
    _cache: dict = {}  # df id → sma20 series

    def fn(i, ema_fast, ema_slow, ema_trend, vol_sma, atr_series, adx_series, macd_line, macd_sig, df):
        df_id = id(df)
        if df_id not in _cache:
            _cache[df_id] = df["Close"].rolling(slow_sma).mean()
        sma20 = _cache[df_id]

        cross_up   = _crossover(ema_fast,  sma20, i)
        cross_down = _crossunder(ema_fast, sma20, i)

        if not cross_up and not cross_down:
            return None

        price   = float(df["Close"].iloc[i])
        ema9_v  = float(ema_fast.iloc[i])
        vol_cur = float(df["Volume"].iloc[i])
        vol_avg = float(vol_sma.iloc[i])
        adx_val = float(adx_series.iloc[i])

        if cross_up   and price <= ema9_v:  return None
        if cross_down and price >= ema9_v:  return None
        if vol_cur <= vol_avg:              return None
        if vol_cur < min_volume:            return None
        if adx_val < min_adx:              return None

        return "BUY" if cross_up else "SELL"

    return fn


def _build_signal_fn(strategy_name: str) -> Callable:
    p = get_params(strategy_name)
    if strategy_name == "sp_ema_crossover":  return _make_sp_signal_fn(p)
    if strategy_name == "ema_crossover_pro": return _make_rk_signal_fn(p)
    if strategy_name == "orb":               return _make_orb_signal_fn(p)
    if strategy_name == "vwap":              return _make_vwap_signal_fn(p)
    if strategy_name == "ema9_sma20":        return _make_ema9_sma20_signal_fn(p)
    raise ValueError(f"Unknown strategy: {strategy_name}")


STRATEGY_SIGNAL_FN: dict[str, Callable] = {
    "sp_ema_crossover":  None,
    "ema_crossover_pro": None,
    "orb":               None,
    "vwap":              None,
    "ema9_sma20":        None,
}


# ── Core backtest ─────────────────────────────────────────────────────────────

@dataclass
class TradeResult:
    ticker:      str
    direction:   str
    entry:       float
    stop_loss:   float
    target:      float
    atr:         float
    signal_time: pd.Timestamp
    outcome:     str    # "TARGET", "SL", "OPEN"
    pnl:         float
    pnl_pct:     float


def _check_outcome(df, signal_idx, direction, entry, sl, target) -> tuple[str, float]:
    for j in range(signal_idx + 1, len(df)):
        high = df["High"].iloc[j]
        low  = df["Low"].iloc[j]
        if direction == "BUY":
            if low  <= sl:     return "SL",     round(sl     - entry, 2)
            if high >= target: return "TARGET",  round(target - entry, 2)
        else:
            if high >= sl:     return "SL",     round(entry - sl,     2)
            if low  <= target: return "TARGET",  round(entry - target, 2)
    return "OPEN", 0.0


def _parse_hhmm(t: str) -> int:
    """Convert 'HH:MM' string to total minutes since midnight."""
    h, m = map(int, t.split(":"))
    return h * 60 + m


def backtest_stock(ticker: str, df: pd.DataFrame,
                   signal_fn: Callable, params: dict) -> list[TradeResult]:
    fast_ema      = params.get("fast_ema",      config.FAST_EMA)
    slow_ema      = params.get("slow_ema",      config.SLOW_EMA)
    trend_ema     = params.get("trend_ema",     config.TREND_EMA)
    atr_len       = params.get("atr_len",       config.ATR_LEN)
    rr_ratio      = params.get("rr_ratio",      config.RR_RATIO)
    volume_sma    = params.get("volume_sma",    config.VOLUME_SMA)
    session_start = _parse_hhmm(params.get("session_start", "09:30"))
    session_end   = _parse_hhmm(params.get("session_end",   "15:00"))

    results = []
    min_candles = trend_ema + atr_len + 30
    if len(df) < min_candles:
        print(f"  {ticker}: Not enough data ({len(df)} bars)")
        return results

    close  = df["Close"]
    volume = df["Volume"]

    ema_fast_s  = _ema(close, fast_ema)
    ema_slow_s  = _ema(close, slow_ema)
    ema_trend_s = _ema(close, trend_ema)
    vol_sma_s   = volume.rolling(volume_sma).mean()
    atr_series  = _atr(df, atr_len)
    adx_series  = _adx(df, atr_len)
    macd_line, macd_sig = _macd(close)

    start = trend_ema + atr_len + 10
    for i in range(start, len(df) - 1):
        # Time filter — 9:30 AM to 3:00 PM IST
        ct = df.index[i]
        try:
            ct = ct.tz_localize(IST) if ct.tzinfo is None else ct.tz_convert(IST)
        except Exception:
            pass
        ct_mins = ct.hour * 60 + ct.minute
        if ct_mins < session_start or ct_mins >= session_end:
            continue

        direction = signal_fn(
            i, ema_fast_s, ema_slow_s, ema_trend_s,
            vol_sma_s, atr_series, adx_series,
            macd_line, macd_sig, df,
        )
        if direction is None:
            continue

        entry   = float(close.iloc[i])
        atr_val = float(atr_series.iloc[i])

        if direction == "BUY":
            sl     = round(entry - atr_val, 2)
            target = round(entry + atr_val * rr_ratio, 2)
        else:
            sl     = round(entry + atr_val, 2)
            target = round(entry - atr_val * rr_ratio, 2)

        outcome, pnl = _check_outcome(df, i, direction, entry, sl, target)
        pnl_pct      = round(pnl / entry * 100, 2)

        results.append(TradeResult(
            ticker      = ticker,
            direction   = direction,
            entry       = round(entry, 2),
            stop_loss   = sl,
            target      = target,
            atr         = round(atr_val, 2),
            signal_time = df.index[i],
            outcome     = outcome,
            pnl         = pnl,
            pnl_pct     = pnl_pct,
        ))

    return results


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(ticker: str, results: list[TradeResult], strategy_name: str, days: int) -> None:
    GREEN = "\033[92m"
    RED   = "\033[91m"
    BOLD  = "\033[1m"
    RESET = "\033[0m"
    SEP   = "=" * 65
    LINE  = "-" * 45

    if not results:
        print(f"\n  {ticker}: No signals found in this period.")
        return

    targets  = [r for r in results if r.outcome == "TARGET"]
    sls      = [r for r in results if r.outcome == "SL"]
    opens    = [r for r in results if r.outcome == "OPEN"]
    total    = len(results)
    win_pct  = round(len(targets) / total * 100, 1) if total else 0
    total_pnl = sum(r.pnl for r in results if r.outcome != "OPEN")

    print(f"\n{SEP}")
    print(f"  {BOLD}{ticker}  |  Strategy: {strategy_name}  |  Last {days} days{RESET}")
    print(f"{SEP}")
    print(f"  Total Signals  : {total}")
    print(f"  {GREEN}Target Hit     : {len(targets)}  ({win_pct}%){RESET}")
    print(f"  {RED}SL Hit         : {len(sls)}{RESET}")
    print(f"  Still Open     : {len(opens)}")
    print(f"  {LINE}")

    if total_pnl >= 0:
        print(f"  {GREEN}{BOLD}Net P&L/share  : Rs{total_pnl:.2f}  (profitable){RESET}")
    else:
        print(f"  {RED}{BOLD}Net P&L/share  : Rs{total_pnl:.2f}  (loss){RESET}")

    print(f"\n  {'Date/Time':<22} {'Dir':<5} {'Entry':>8} {'SL':>8} {'Target':>8} {'Outcome':<8} {'P&L':>8}")
    print(f"  {'-'*22} {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")

    for r in results:
        t_str   = str(r.signal_time)[:16]
        color   = GREEN if r.outcome == "TARGET" else (RED if r.outcome == "SL" else "")
        pnl_str = f"Rs{r.pnl:+.2f}" if r.outcome != "OPEN" else "open"
        print(f"  {color}{t_str:<22} {r.direction:<5} {r.entry:>8.2f} "
              f"{r.stop_loss:>8.2f} {r.target:>8.2f} {r.outcome:<8} {pnl_str:>8}{RESET}")

    print(f"\n  {BOLD}Profitability Check (1:{config.RR_RATIO:.0f} RR){RESET}")
    print(f"  Break-even win rate needed : 25%")
    status = f"{GREEN}Profitable{RESET}" if win_pct >= 25 else f"{RED}Review needed{RESET}"
    print(f"  Your win rate              : {win_pct}%  [{status}]")
    print(f"{SEP}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backtest a trading strategy")
    parser.add_argument("tickers", nargs="*", default=["TATAMOTORS"],
                        help="Stock ticker(s) to backtest")
    parser.add_argument("--strategy", default="sp_ema_crossover",
                        choices=list(STRATEGY_SIGNAL_FN.keys()),
                        help="Strategy to backtest (default: sp_ema_crossover; choices: sp_ema_crossover, ema_crossover_pro, orb, vwap)")
    parser.add_argument("--days", type=int, default=60,
                        help="Number of trading days of data (default: 60)")
    args = parser.parse_args()

    bars = args.days * BARS_PER_DAY
    params    = get_params(args.strategy)
    signal_fn = _build_signal_fn(args.strategy)

    print(f"\nStrategy  : {args.strategy}")
    print(f"Params    : {params}")
    print(f"Period    : Last {args.days} trading days (~{bars} bars)")
    print(f"Stocks    : {', '.join(args.tickers)}")
    print("Fetching data... please wait.\n")

    original_bars    = config.FETCH_BARS
    config.FETCH_BARS = bars
    data = data_fetcher.fetch_all(args.tickers, max_workers=1)
    config.FETCH_BARS = original_bars

    for ticker in args.tickers:
        if ticker not in data:
            print(f"  {ticker}: Failed to fetch data")
            continue
        results = backtest_stock(ticker, data[ticker], signal_fn, params)
        print_report(ticker, results, args.strategy, args.days)


if __name__ == "__main__":
    main()
