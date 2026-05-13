"""
EMA 9 × SMA 20 Crossover strategy — 9:30 AM to 15:15 PM IST.

Signal condition:
  BUY  — EMA(9) crosses above SMA(20) AND price closes above EMA(9)
         AND volume > SMA(volume) AND volume >= min_volume AND ADX >= min_adx
  SELL — EMA(9) crosses below SMA(20) AND price closes below EMA(9)
         AND volume > SMA(volume) AND volume >= min_volume AND ADX >= min_adx

SL/Target are ATR-based with configurable sl_mult and rr_ratio.

All parameters are read from strategies_config.json params block.
"""

import logging
from typing import Any, Optional

import pandas as pd

import config
from strategies.base import BaseStrategy, Signal
from strategies.ema_crossover import _ema, _atr, _adx, _crossover, _crossunder

logger = logging.getLogger(__name__)


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


class EMA9SMA20Strategy(BaseStrategy):
    name        = "ema9_sma20"
    description = "EMA(9) x SMA(20) crossover · volume + ADX filters · price confirmation"

    _DEFAULTS: dict[str, Any] = {
        "fast_ema":   9,
        "slow_sma":   20,
        "atr_len":    14,
        "rr_ratio":   3.0,
        "sl_mult":    1.0,
        "volume_sma": 20,
        "min_volume": 50000,
        "min_adx":    20,
    }

    def _p(self, key: str, default=None):
        return self.params.get(key, self._DEFAULTS.get(key, default))

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Optional[Signal]:
        fast_ema   = self._p("fast_ema")
        slow_sma   = self._p("slow_sma")
        atr_len    = self._p("atr_len")
        rr_ratio   = self._p("rr_ratio")
        sl_mult    = self._p("sl_mult")
        volume_sma = self._p("volume_sma")
        min_volume = self._p("min_volume")
        min_adx    = self._p("min_adx")

        min_candles = slow_sma + atr_len + 10
        if len(df) < min_candles:
            logger.debug("%s: not enough candles (%d)", ticker, len(df))
            return None

        close      = df["Close"]
        volume     = df["Volume"]
        ema9       = _ema(close, fast_ema)
        sma20      = _sma(close, slow_sma)
        atr_series = _atr(df, atr_len)
        adx_series = _adx(df, atr_len)
        vol_sma    = volume.rolling(volume_sma).mean()

        i = -2  # last completed candle

        cross_up   = _crossover(ema9,  sma20, i)
        cross_down = _crossunder(ema9, sma20, i)

        if not cross_up and not cross_down:
            return None

        price    = float(close.iloc[i])
        ema9_v   = float(ema9.iloc[i])
        sma20_v  = float(sma20.iloc[i])
        atr_val  = float(atr_series.iloc[i])
        adx_val  = float(adx_series.iloc[i])
        vol_cur  = float(volume.iloc[i])
        vol_avg  = float(vol_sma.iloc[i])
        candle_t = df.index[i]

        # Price confirmation: must close on the correct side of EMA(9)
        if cross_up   and price <= ema9_v:
            logger.debug("%s: BUY filtered — price not above EMA%d", ticker, fast_ema)
            return None
        if cross_down and price >= ema9_v:
            logger.debug("%s: SELL filtered — price not below EMA%d", ticker, fast_ema)
            return None

        # Volume filter
        if vol_cur <= vol_avg:
            logger.debug("%s: filtered — volume below average (%.0f <= %.0f)", ticker, vol_cur, vol_avg)
            return None
        if vol_cur < min_volume:
            logger.debug("%s: filtered — volume below minimum (%.0f)", ticker, vol_cur)
            return None

        # ADX filter — only trade when trend is strong enough
        if adx_val < min_adx:
            logger.debug("%s: filtered — ADX %.1f below min %d", ticker, adx_val, min_adx)
            return None

        direction = "BUY" if cross_up else "SELL"

        if direction == "BUY":
            entry      = price
            sl         = round(entry - atr_val * sl_mult, 2)
            target     = round(entry + atr_val * rr_ratio, 2)
            risk_pct   = round((entry - sl)     / entry * 100, 2)
            reward_pct = round((target - entry) / entry * 100, 2)
        else:
            entry      = price
            sl         = round(entry + atr_val * sl_mult, 2)
            target     = round(entry - atr_val * rr_ratio, 2)
            risk_pct   = round((sl - entry)     / entry * 100, 2)
            reward_pct = round((entry - target) / entry * 100, 2)

        logger.info(
            "  %-12s | %s | Entry: %.2f | EMA%d: %.2f | SMA%d: %.2f | ADX: %.1f | Vol: %.0f | SL: %.2f | Target: %.2f",
            ticker, direction, entry, fast_ema, ema9_v, slow_sma, sma20_v, adx_val, vol_cur, sl, target,
        )

        return Signal(
            ticker        = ticker,
            direction     = direction,
            entry         = round(entry, 2),
            stop_loss     = sl,
            target        = target,
            atr           = round(atr_val, 2),
            candle_time   = candle_t,
            risk_pct      = risk_pct,
            reward_pct    = reward_pct,
            priority      = ticker in config.HIGH_ATR_STOCKS,
            strategy_name = self.name,
            footer        = f"EMA{fast_ema} x SMA{slow_sma} · Vol + ADX({min_adx}) filters · price {'above' if cross_up else 'below'} EMA{fast_ema}",
        )
