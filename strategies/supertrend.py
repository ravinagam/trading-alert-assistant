"""
Supertrend strategy — 10:00 AM to 11:30 AM IST.

Supertrend is an ATR-based dynamic support/resistance line that flips direction
when price crosses it. Signal fires on the flip candle with volume and
EMA9 > SMA20 confluence confirmation.

SL is set to the Supertrend line itself (adaptive, tighter than fixed ATR SL).
Target = entry ± (entry - sl) × rr_ratio.

All parameters are read from strategies_config.json params block.
"""

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

import config
from strategies.base import BaseStrategy, Signal
from strategies.ema_crossover import _ema, _atr

logger = logging.getLogger(__name__)


def _supertrend(df: pd.DataFrame, atr_len: int, multiplier: float):
    """
    Compute Supertrend line and direction.

    Returns
    -------
    st    : pd.Series  — Supertrend line value (support when bullish, resistance when bearish)
    trend : pd.Series  — 1 = bullish, -1 = bearish
    """
    close  = df["Close"].values
    high   = df["High"].values
    low    = df["Low"].values
    hl2    = (high + low) / 2.0
    atr    = _atr(df, atr_len).values

    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    n     = len(close)
    st    = np.zeros(n)
    trend = np.zeros(n, dtype=int)

    st[0]    = upper[0]
    trend[0] = -1

    for i in range(1, n):
        # Ratchet bands — only tighten, never widen mid-trend
        final_upper = upper[i] if upper[i] < st[i - 1] or close[i - 1] > st[i - 1] else st[i - 1]
        final_lower = lower[i] if lower[i] > st[i - 1] or close[i - 1] < st[i - 1] else st[i - 1]

        if trend[i - 1] == -1 and close[i] > final_upper:
            trend[i] = 1
            st[i]    = final_lower
        elif trend[i - 1] == 1 and close[i] < final_lower:
            trend[i] = -1
            st[i]    = final_upper
        else:
            trend[i] = trend[i - 1]
            st[i]    = final_lower if trend[i] == 1 else final_upper

    return pd.Series(st, index=df.index), pd.Series(trend, index=df.index)


class SupertrendStrategy(BaseStrategy):
    name        = "supertrend"
    description = "Supertrend — ATR-based trend flip with volume + EMA9>SMA20 confirmation"

    _DEFAULTS: dict[str, Any] = {
        "atr_len":      10,
        "multiplier":   3.0,
        "rr_ratio":     3.0,
        "min_volume":   50_000,
        "volume_sma":   20,
        "ema_confirm":  True,
        "ema_fast":     9,
        "ema_slow_sma": 20,
    }

    def _p(self, key: str, default=None):
        return self.params.get(key, self._DEFAULTS.get(key, default))

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Optional[Signal]:
        atr_len      = self._p("atr_len")
        multiplier   = self._p("multiplier")
        rr_ratio     = self._p("rr_ratio")
        min_volume   = self._p("min_volume")
        volume_sma   = self._p("volume_sma")
        ema_confirm  = self._p("ema_confirm")
        ema_fast     = self._p("ema_fast")
        ema_slow_sma = self._p("ema_slow_sma")

        min_candles = atr_len + volume_sma + 10
        if len(df) < min_candles:
            logger.debug("%s: not enough candles (%d)", ticker, len(df))
            return None

        st_line, trend = _supertrend(df, atr_len, multiplier)

        i = -2  # last completed candle

        # Signal only fires on the flip candle
        if trend.iloc[i] == trend.iloc[i - 1]:
            return None

        direction = "BUY" if trend.iloc[i] == 1 else "SELL"

        volume   = float(df["Volume"].iloc[i])
        vol_sma  = float(df["Volume"].rolling(volume_sma).mean().iloc[i])
        vol_ok   = volume > vol_sma and volume >= min_volume

        if not vol_ok:
            logger.debug("%s: ST %s filtered — volume too low (%.0f vs avg %.0f)",
                         ticker, direction, volume, vol_sma)
            return None

        if ema_confirm:
            ema9  = float(_ema(df["Close"], ema_fast).iloc[i])
            sma20 = float(df["Close"].rolling(ema_slow_sma).mean().iloc[i])
            if direction == "BUY"  and ema9 <= sma20:
                logger.debug("%s: ST BUY filtered — EMA%d (%.2f) not above SMA%d (%.2f)",
                             ticker, ema_fast, ema9, ema_slow_sma, sma20)
                return None
            if direction == "SELL" and ema9 >= sma20:
                logger.debug("%s: ST SELL filtered — EMA%d (%.2f) not below SMA%d (%.2f)",
                             ticker, ema_fast, ema9, ema_slow_sma, sma20)
                return None

        entry    = float(df["Close"].iloc[i])
        sl_price = round(float(st_line.iloc[i]), 2)
        candle_t = df.index[i]

        sl_dist = abs(entry - sl_price)
        if sl_dist < 0.01:
            logger.debug("%s: ST %s filtered — SL distance negligible", ticker, direction)
            return None

        if direction == "BUY":
            target     = round(entry + sl_dist * rr_ratio, 2)
            risk_pct   = round((entry - sl_price) / entry * 100, 2)
            reward_pct = round((target - entry)   / entry * 100, 2)
        else:
            target     = round(entry - sl_dist * rr_ratio, 2)
            risk_pct   = round((sl_price - entry) / entry * 100, 2)
            reward_pct = round((entry - target)   / entry * 100, 2)

        logger.info(
            "  %-12s | %s | Entry: %.2f | ST Line: %.2f | SL: %.2f | Target: %.2f",
            ticker, direction, entry, float(st_line.iloc[i]), sl_price, target,
        )

        return Signal(
            ticker        = ticker,
            direction     = direction,
            entry         = round(entry, 2),
            stop_loss     = sl_price,
            target        = target,
            atr           = round(sl_dist, 2),
            candle_time   = candle_t,
            risk_pct      = risk_pct,
            reward_pct    = reward_pct,
            priority      = ticker in config.HIGH_ATR_STOCKS,
            strategy_name = self.name,
            footer        = f"Supertrend({atr_len},{multiplier}) flip · Vol + EMA{ema_fast}>SMA{ema_slow_sma} confirm",
        )
