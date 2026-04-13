"""
Python replication of the Pine Script RK-EMA Crossover PRO strategy.

Key design decisions
--------------------
* We always evaluate the LAST COMPLETED candle (index -2), never the live
  forming candle (index -1).  This prevents look-ahead / repainting.
* ATR uses Wilder's smoothing (RMA) — identical to Pine Script's ta.atr().
* EMA uses standard exponential smoothing (alpha = 2/(span+1)) — identical
  to Pine Script's ta.ema().
"""

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config

logger = logging.getLogger(__name__)

MIN_CANDLES_REQUIRED = config.TREND_EMA + config.ATR_LEN + 5


@dataclass
class Signal:
    ticker: str
    direction: str          # "BUY" or "SELL"
    entry: float
    stop_loss: float
    target: float
    atr: float
    candle_time: pd.Timestamp
    risk_pct: float         # SL distance as % of entry
    reward_pct: float       # Target distance as % of entry


# ── Internal helpers ────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    """Standard EMA — matches Pine Script ta.ema()."""
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """
    ATR with Wilder's smoothing (RMA) — matches Pine Script ta.atr().
    RMA: alpha = 1/period  (not 2/(period+1) like EMA)
    """
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat(
        [(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _crossover(a: pd.Series, b: pd.Series, idx: int) -> bool:
    """a crosses above b at candle idx (ta.crossover equivalent)."""
    return (a.iloc[idx] > b.iloc[idx]) and (a.iloc[idx - 1] <= b.iloc[idx - 1])


def _crossunder(a: pd.Series, b: pd.Series, idx: int) -> bool:
    """a crosses below b at candle idx (ta.crossunder equivalent)."""
    return (a.iloc[idx] < b.iloc[idx]) and (a.iloc[idx - 1] >= b.iloc[idx - 1])


# ── Public API ───────────────────────────────────────────────────────────────

def generate_signal(ticker: str, df: pd.DataFrame) -> Optional[Signal]:
    """
    Run the strategy on df and return a Signal if one fires on the last
    completed candle, else None.
    """
    if len(df) < MIN_CANDLES_REQUIRED:
        logger.debug("%s: not enough candles (%d)", ticker, len(df))
        return None

    close  = df["Close"]
    volume = df["Volume"]

    ema_fast  = _ema(close, config.FAST_EMA)
    ema_slow  = _ema(close, config.SLOW_EMA)
    ema_trend = _ema(close, config.TREND_EMA)
    vol_sma   = volume.rolling(config.VOLUME_SMA).mean()
    atr_series = _atr(df, config.ATR_LEN)

    # Use last COMPLETED candle — index -2 avoids the live forming bar
    i = -2

    vol_ok = volume.iloc[i] > vol_sma.iloc[i]

    buy_signal = (
        _crossover(ema_fast, ema_slow, i)
        and close.iloc[i] > ema_trend.iloc[i]
        and vol_ok
    )

    sell_signal = (
        _crossunder(ema_fast, ema_slow, i)
        and close.iloc[i] < ema_trend.iloc[i]
        and vol_ok
    )

    if not buy_signal and not sell_signal:
        return None

    entry    = float(close.iloc[i])
    atr_val  = float(atr_series.iloc[i])
    candle_t = df.index[i]

    if buy_signal:
        sl     = entry - atr_val
        target = entry + atr_val * config.RR_RATIO
        risk_pct   = round((entry - sl) / entry * 100, 2)
        reward_pct = round((target - entry) / entry * 100, 2)
        direction  = "BUY"
    else:
        sl     = entry + atr_val
        target = entry - atr_val * config.RR_RATIO
        risk_pct   = round((sl - entry) / entry * 100, 2)
        reward_pct = round((entry - target) / entry * 100, 2)
        direction  = "SELL"

    return Signal(
        ticker      = ticker,
        direction   = direction,
        entry       = round(entry, 2),
        stop_loss   = round(sl, 2),
        target      = round(target, 2),
        atr         = round(atr_val, 2),
        candle_time = candle_t,
        risk_pct    = risk_pct,
        reward_pct  = reward_pct,
    )
