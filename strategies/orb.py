"""
Opening Range Breakout (ORB) strategy — 9:30 AM to 11:30 AM IST.

Opening range is defined as the High/Low of the first `orb_minutes` candles
after market open (9:15 AM). Signal fires when price closes above/below that
range with volume confirmation.

All parameters are read from strategies_config.json params block.
"""

import logging
from typing import Any, Optional

import pandas as pd
import pytz

import config
from strategies.base import BaseStrategy, Signal
from strategies.ema_crossover import _ema, _atr

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def _localize(ts, tz):
    return ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)


class ORBStrategy(BaseStrategy):
    name        = "orb"
    description = "Opening Range Breakout — breakout above/below first-N-min high/low"

    _DEFAULTS: dict[str, Any] = {
        "orb_minutes":  15,     # minutes after 9:15 to define opening range
        "atr_len":      14,
        "rr_ratio":     2.0,
        "sl_mult":      1.0,    # SL = ATR × sl_mult
        "min_volume":   50000,
        "volume_sma":   20,
        "ema_confirm":  True,   # require EMA(9) > SMA(20) for BUY, < for SELL
        "ema_fast":     9,
        "ema_slow_sma": 20,
    }

    def _p(self, key: str, default=None):
        return self.params.get(key, self._DEFAULTS.get(key, default))

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Optional[Signal]:
        if ticker in self._p("excluded_tickers", []):
            return None

        orb_minutes  = self._p("orb_minutes")
        atr_len      = self._p("atr_len")
        rr_ratio     = self._p("rr_ratio")
        sl_mult      = self._p("sl_mult")
        min_volume   = self._p("min_volume")
        volume_sma   = self._p("volume_sma")
        ema_confirm  = self._p("ema_confirm")
        ema_fast     = self._p("ema_fast")
        ema_slow_sma = self._p("ema_slow_sma")

        min_candles = atr_len + orb_minutes // 5 + 5
        if len(df) < min_candles:
            logger.debug("%s: not enough candles (%d)", ticker, len(df))
            return None

        # Build IST-indexed view without mutating df
        try:
            idx_ist = [_localize(ts, IST) for ts in df.index]
        except Exception:
            return None

        # Find candles within the opening range window (9:15 → 9:15+orb_minutes)
        orb_candles = [
            (i, ts) for i, ts in enumerate(idx_ist)
            if ts.hour == 9 and ts.minute >= 15 and (ts.hour * 60 + ts.minute) < (9 * 60 + 15 + orb_minutes)
        ]
        if len(orb_candles) < (orb_minutes // 5):
            logger.debug("%s: opening range candles not yet complete", ticker)
            return None

        orb_indices = [i for i, _ in orb_candles]
        orb_high = float(df["High"].iloc[orb_indices].max())
        orb_low  = float(df["Low"].iloc[orb_indices].min())

        i       = -2  # last completed candle
        candle_t = df.index[i]
        close    = float(df["Close"].iloc[i])
        high     = float(df["High"].iloc[i])
        low      = float(df["Low"].iloc[i])
        volume   = float(df["Volume"].iloc[i])
        atr_series = _atr(df, atr_len)
        atr_val    = float(atr_series.iloc[i])
        vol_sma    = float(df["Volume"].rolling(volume_sma).mean().iloc[i])

        # Prevent re-triggering on opening range candles themselves
        candle_ist = _localize(candle_t, IST)
        candle_mins = candle_ist.hour * 60 + candle_ist.minute
        if candle_mins < (9 * 60 + 15 + orb_minutes):
            return None

        vol_ok = volume > vol_sma and volume >= min_volume

        breakout_up   = close > orb_high and vol_ok
        breakout_down = close < orb_low  and vol_ok

        if not breakout_up and not breakout_down:
            return None

        direction = "BUY" if breakout_up else "SELL"

        # EMA(9) vs SMA(20) confluence confirmation
        if ema_confirm:
            ema9  = float(_ema(df["Close"], ema_fast).iloc[i])
            sma20 = float(df["Close"].rolling(ema_slow_sma).mean().iloc[i])
            if direction == "BUY"  and ema9 <= sma20:
                logger.debug("%s: ORB BUY filtered — EMA%d (%.2f) not above SMA%d (%.2f)",
                             ticker, ema_fast, ema9, ema_slow_sma, sma20)
                return None
            if direction == "SELL" and ema9 >= sma20:
                logger.debug("%s: ORB SELL filtered — EMA%d (%.2f) not below SMA%d (%.2f)",
                             ticker, ema_fast, ema9, ema_slow_sma, sma20)
                return None
        entry = close

        if direction == "BUY":
            sl         = round(entry - atr_val * sl_mult, 2)
            target     = round(entry + atr_val * rr_ratio, 2)
            risk_pct   = round((entry - sl)     / entry * 100, 2)
            reward_pct = round((target - entry) / entry * 100, 2)
        else:
            sl         = round(entry + atr_val * sl_mult, 2)
            target     = round(entry - atr_val * rr_ratio, 2)
            risk_pct   = round((sl - entry)     / entry * 100, 2)
            reward_pct = round((entry - target) / entry * 100, 2)

        logger.info(
            "  %-12s | %s | Entry: %.2f | ORB High: %.2f | ORB Low: %.2f | SL: %.2f | Target: %.2f",
            ticker, direction, entry, orb_high, orb_low, sl, target,
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
            footer        = f"ORB {orb_minutes}-min range · Break: {'above' if breakout_up else 'below'} {orb_high if breakout_up else orb_low:.2f} · Vol" + (" + EMA9>SMA20 confirm" if ema_confirm else ""),
        )
