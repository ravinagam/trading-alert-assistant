"""
Backward-compatibility shim — re-exports everything from the strategies package.
backtest.py imports helpers directly from this module; this shim preserves that.
"""

from strategies.base import Signal
from strategies.ema_crossover import (
    EMACrossoverStrategy,
    _ema, _atr, _atr as atr,
    _crossover, _crossunder,
    _macd, _adx,
    MIN_ATR_PCT, MIN_ADX, MIN_VOLUME, SPIKE_ATR_MULT,
    MIN_CANDLES_REQUIRED,
)

_strategy_instance = EMACrossoverStrategy()


def generate_signal(ticker, df):
    return _strategy_instance.generate_signal(ticker, df)


__all__ = [
    "Signal", "generate_signal",
    "_ema", "_atr", "_crossover", "_crossunder", "_macd", "_adx",
    "MIN_ATR_PCT", "MIN_ADX", "MIN_VOLUME", "SPIKE_ATR_MULT", "MIN_CANDLES_REQUIRED",
]
