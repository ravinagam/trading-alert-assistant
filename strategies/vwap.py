"""
VWAP Crossover strategy — 11:30 AM to 2:30 PM IST.

VWAP resets daily at 9:15 AM. Signal fires when EMA(9) crosses above/below
VWAP. Optional trend filter: price must be above/below EMA(50).

All parameters are read from strategies_config.json params block.
"""

import logging
from typing import Any, Optional

import pandas as pd
import pytz

import config
from strategies.base import BaseStrategy, Signal
from strategies.ema_crossover import _ema, _atr, _adx, _crossover, _crossunder

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def _localize(ts, tz):
    return ts.tz_localize(tz) if ts.tzinfo is None else ts.tz_convert(tz)


def _compute_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Compute intraday VWAP that resets at 9:15 AM each trading day.
    VWAP = cumulative(TP * Volume) / cumulative(Volume) within each day.
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3

    # Build IST timestamps for grouping
    try:
        idx_ist = pd.DatetimeIndex([
            ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
            for ts in df.index
        ])
    except Exception:
        idx_ist = df.index

    dates = pd.Series(idx_ist.date, index=df.index)

    cum_tpv = (tp * df["Volume"]).groupby(dates).cumsum()
    cum_vol = df["Volume"].groupby(dates).cumsum()

    return cum_tpv / cum_vol


class VWAPStrategy(BaseStrategy):
    name        = "vwap"
    description = "VWAP Crossover — EMA(9) crosses VWAP with optional trend filter"

    _DEFAULTS: dict[str, Any] = {
        "fast_ema":     9,
        "trend_ema":    50,
        "atr_len":      14,
        "rr_ratio":     2.0,
        "sl_mult":      1.0,    # SL = ATR × sl_mult
        "trend_filter": True,   # price must be above EMA50 for BUY, below for SELL
        "ema_confirm":  True,   # require EMA(9) > SMA(20) for BUY, < for SELL
        "ema_slow_sma": 20,
        "min_adx":      20,     # skip signal when market is ranging (ADX < threshold)
        "max_sl_rupees": 10,    # skip if ATR-based SL distance > ₹10 (prevents outsized losses on high-price stocks)
    }

    def _p(self, key: str, default=None):
        return self.params.get(key, self._DEFAULTS.get(key, default))

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Optional[Signal]:
        fast_ema     = self._p("fast_ema")
        trend_ema    = self._p("trend_ema")
        atr_len      = self._p("atr_len")
        rr_ratio     = self._p("rr_ratio")
        sl_mult      = self._p("sl_mult")
        trend_filter = self._p("trend_filter")
        ema_confirm  = self._p("ema_confirm")
        ema_slow_sma = self._p("ema_slow_sma")
        min_adx           = self._p("min_adx")
        max_sl_rupees     = self._p("max_sl_rupees")
        excluded_tickers  = self._p("excluded_tickers", [])

        if ticker in excluded_tickers:
            return None

        min_candles = trend_ema + atr_len + 5
        if len(df) < min_candles:
            logger.debug("%s: not enough candles (%d)", ticker, len(df))
            return None

        close = df["Close"]

        ema_fast_s  = _ema(close, fast_ema)
        ema_trend_s = _ema(close, trend_ema)
        atr_series  = _atr(df, atr_len)
        adx_series  = _adx(df, atr_len)
        vwap_series = _compute_vwap(df)

        i = -2  # last completed candle

        price    = float(close.iloc[i])
        atr_val  = float(atr_series.iloc[i])
        ema_t    = float(ema_trend_s.iloc[i])
        vwap_val = float(vwap_series.iloc[i])
        candle_t = df.index[i]

        sl_rupees = atr_val * sl_mult
        if max_sl_rupees and sl_rupees > max_sl_rupees:
            logger.debug("%s: VWAP filtered — SL ₹%.2f (ATR %.2f × %.1f) exceeds max ₹%.0f",
                         ticker, sl_rupees, atr_val, sl_mult, max_sl_rupees)
            return None

        cross_up   = _crossover(close,  vwap_series, i)
        cross_down = _crossunder(close, vwap_series, i)

        if not cross_up and not cross_down:
            return None

        # ADX filter — skip in ranging/choppy market
        adx_val = float(adx_series.iloc[i])
        if adx_val < min_adx:
            logger.debug("%s: VWAP filtered — ADX %.1f below min %d (ranging market)",
                         ticker, adx_val, min_adx)
            return None

        # Trend filter gate
        if trend_filter:
            if cross_up   and price < ema_t:
                logger.debug("%s: BUY signal filtered — price below EMA%d", ticker, trend_ema)
                return None
            if cross_down and price > ema_t:
                logger.debug("%s: SELL signal filtered — price above EMA%d", ticker, trend_ema)
                return None

        direction = "BUY" if cross_up else "SELL"

        # EMA(9) vs SMA(20) confluence confirmation
        if ema_confirm:
            sma20 = float(df["Close"].rolling(ema_slow_sma).mean().iloc[i])
            ema9  = float(ema_fast_s.iloc[i])
            if direction == "BUY"  and ema9 <= sma20:
                logger.debug("%s: VWAP BUY filtered — EMA9 (%.2f) not above SMA%d (%.2f)",
                             ticker, ema9, ema_slow_sma, sma20)
                return None
            if direction == "SELL" and ema9 >= sma20:
                logger.debug("%s: VWAP SELL filtered — EMA9 (%.2f) not below SMA%d (%.2f)",
                             ticker, ema9, ema_slow_sma, sma20)
                return None

        entry = price

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

        trend_desc = f"EMA{trend_ema} filter" if trend_filter else "no trend filter"
        confirm_desc = f" + EMA9>SMA20 confirm" if ema_confirm else ""
        logger.info(
            "  %-12s | %s | Entry: %.2f | VWAP: %.2f | SL: %.2f | Target: %.2f",
            ticker, direction, entry, vwap_val, sl, target,
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
            footer        = f"Close x VWAP crossover · {trend_desc}{confirm_desc}",
        )
