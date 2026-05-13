"""
SP EMA Crossover strategy — Python replication of SP_EMA_Crossover.pine.

Signal condition mirrors the Pine Script exactly:
  - Fast EMA crosses above/below Slow EMA
  - No additional filters (no volume, ATR, ADX, MACD, spike checks)
  - Trend EMA is computed for context only — not a signal gate

SL/Target use ATR (configurable RR ratio) to populate the alert message
since the Pine Script does not define risk management levels.

All parameters are read from strategies_config.json params block.
"""

import logging
from typing import Any, Optional

import pandas as pd

import config
from strategies.base import BaseStrategy, Signal
from strategies.ema_crossover import _ema, _atr, _crossover, _crossunder

logger = logging.getLogger(__name__)


class SPEMACrossoverStrategy(BaseStrategy):
    name        = "sp_ema_crossover"
    description = "SP EMA Crossover by Springpad (9/21 crossover, no extra filters)"

    _DEFAULTS: dict[str, Any] = {
        "fast_ema":  9,
        "slow_ema":  21,
        "trend_ema": 50,
        "atr_len":   14,
        "rr_ratio":  3.0,
    }

    def _p(self, key: str, default=None):
        return self.params.get(key, self._DEFAULTS.get(key, default))

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Optional[Signal]:
        fast_ema  = self._p("fast_ema")
        slow_ema  = self._p("slow_ema")
        trend_ema = self._p("trend_ema")
        atr_len   = self._p("atr_len")
        rr_ratio  = self._p("rr_ratio")

        min_candles = trend_ema + atr_len + 5
        if len(df) < min_candles:
            logger.debug("%s: not enough candles (%d)", ticker, len(df))
            return None

        close = df["Close"]

        ema_fast_s  = _ema(close, fast_ema)
        ema_slow_s  = _ema(close, slow_ema)
        ema_trend_s = _ema(close, trend_ema)
        atr_series  = _atr(df, atr_len)

        i = -2  # last completed candle

        cross_up   = _crossover(ema_fast_s,  ema_slow_s, i)
        cross_down = _crossunder(ema_fast_s, ema_slow_s, i)

        if not cross_up and not cross_down:
            return None

        price     = float(close.iloc[i])
        atr_val   = float(atr_series.iloc[i])
        ema_t     = float(ema_trend_s.iloc[i])
        candle_t  = df.index[i]
        direction = "BUY" if cross_up else "SELL"

        trend_side = "above" if price > ema_t else "below"
        logger.info(
            "  %-12s | %s | Price: %8.2f | EMA%d: %.2f (price %s trend)",
            ticker, direction, price, trend_ema, ema_t, trend_side,
        )

        if cross_up:
            entry      = price
            sl         = entry - atr_val
            target     = entry + atr_val * rr_ratio
            risk_pct   = round((entry - sl)     / entry * 100, 2)
            reward_pct = round((target - entry) / entry * 100, 2)
            logger.info(
                "  \033[1m\033[92m%-12s | ✔ BUY SIGNAL  — Entry: %.2f  SL: %.2f  Target: %.2f\033[0m",
                ticker, entry, sl, target)
        else:
            entry      = price
            sl         = entry + atr_val
            target     = entry - atr_val * rr_ratio
            risk_pct   = round((sl - entry)     / entry * 100, 2)
            reward_pct = round((entry - target) / entry * 100, 2)
            logger.info(
                "  \033[1m\033[91m%-12s | ✔ SELL SIGNAL — Entry: %.2f  SL: %.2f  Target: %.2f\033[0m",
                ticker, entry, sl, target)

        return Signal(
            ticker        = ticker,
            direction     = direction,
            entry         = round(entry, 2),
            stop_loss     = round(sl, 2),
            target        = round(target, 2),
            atr           = round(atr_val, 2),
            candle_time   = candle_t,
            risk_pct      = risk_pct,
            reward_pct    = reward_pct,
            priority      = ticker in config.HIGH_ATR_STOCKS,
            strategy_name = self.name,
            footer        = f"EMA {fast_ema}/{slow_ema} crossover · no additional filters",
        )
