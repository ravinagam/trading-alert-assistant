"""
RK-EMA Crossover PRO strategy — Python replication of the Pine Script original.

Key design decisions
--------------------
* We always evaluate the LAST COMPLETED candle (index -2), never the live
  forming candle (index -1).  This prevents look-ahead / repainting.
* ATR uses Wilder's smoothing (RMA) — identical to Pine Script's ta.atr().
* EMA uses standard exponential smoothing (alpha = 2/(span+1)) — identical
  to Pine Script's ta.ema().
* All filter thresholds are read from strategies_config.json params block,
  with sensible defaults as fallback.
"""

import logging
from typing import Any, Optional

import pandas as pd

import config
from strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)


# ── Internal helpers ─────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    """Standard EMA — matches Pine Script ta.ema()."""
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR with Wilder's smoothing (RMA) — matches Pine Script ta.atr()."""
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat(
        [(h - l), (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _crossover(a: pd.Series, b: pd.Series, idx: int) -> bool:
    """a crosses above b at candle idx."""
    return (a.iloc[idx] > b.iloc[idx]) and (a.iloc[idx - 1] <= b.iloc[idx - 1])


def _crossunder(a: pd.Series, b: pd.Series, idx: int) -> bool:
    """a crosses below b at candle idx."""
    return (a.iloc[idx] < b.iloc[idx]) and (a.iloc[idx - 1] >= b.iloc[idx - 1])


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD — matches Pine Script ta.macd(). Returns (macd_line, signal_line)."""
    ema_fast    = series.ewm(span=fast,   adjust=False).mean()
    ema_slow    = series.ewm(span=slow,   adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX — measures trend strength using Wilder's smoothing."""
    h, l, c = df["High"], df["Low"], df["Close"]
    up   = h - h.shift(1)
    down = l.shift(1) - l
    plus_dm  = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr      = _atr(df, period)
    plus_di  = 100 * plus_dm.ewm(alpha=1/period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/period, adjust=False).mean() / atr
    dx       = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
    return dx.ewm(alpha=1/period, adjust=False).mean()


# ── Strategy class ────────────────────────────────────────────────────────────

class EMACrossoverStrategy(BaseStrategy):
    name        = "ema_crossover_pro"
    description = "RK EMA Crossover PRO (9/21/50 EMA with volume, ATR, ADX, MACD filters)"

    # Default param values — overridden by strategies_config.json
    _DEFAULTS: dict[str, Any] = {
        "fast_ema":       9,
        "slow_ema":       21,
        "trend_ema":      50,
        "atr_len":        14,
        "rr_ratio":       3.0,
        "volume_sma":     20,
        "min_atr_pct":    0.2,    # percentage (0.2 = 0.2% of price)
        "min_adx":        20,
        "min_volume":     50_000,
        "spike_atr_mult": 2.5,
    }

    def _p(self, key: str, default=None):
        return self.params.get(key, self._DEFAULTS.get(key, default))

    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Optional[Signal]:
        fast_ema       = self._p("fast_ema")
        slow_ema       = self._p("slow_ema")
        trend_ema      = self._p("trend_ema")
        atr_len        = self._p("atr_len")
        rr_ratio       = self._p("rr_ratio")
        volume_sma     = self._p("volume_sma")
        min_atr_pct    = self._p("min_atr_pct") / 100   # convert % → decimal
        min_adx        = self._p("min_adx")
        min_volume     = self._p("min_volume")
        spike_atr_mult = self._p("spike_atr_mult")

        min_candles = trend_ema + atr_len + 5
        if len(df) < min_candles:
            logger.debug("%s: not enough candles (%d)", ticker, len(df))
            return None

        close  = df["Close"]
        volume = df["Volume"]

        ema_fast_s   = _ema(close, fast_ema)
        ema_slow_s   = _ema(close, slow_ema)
        ema_trend_s  = _ema(close, trend_ema)
        vol_sma_s    = volume.rolling(volume_sma).mean()
        atr_series   = _atr(df, atr_len)
        adx_series   = _adx(df, atr_len)
        macd_line, macd_signal = _macd(close)

        i = -2  # last completed candle

        price        = float(close.iloc[i])
        atr_val      = float(atr_series.iloc[i])
        adx_val      = float(adx_series.iloc[i])
        vol_cur      = float(volume.iloc[i])
        vol_avg      = float(vol_sma_s.iloc[i])
        ema_t        = float(ema_trend_s.iloc[i])
        macd_val     = float(macd_line.iloc[i])
        macd_sig     = float(macd_signal.iloc[i])
        atr_pct      = atr_val / price * 100
        candle_range = float(df["High"].iloc[i] - df["Low"].iloc[i])

        cross_up   = _crossover(ema_fast_s,  ema_slow_s, i)
        cross_down = _crossunder(ema_fast_s, ema_slow_s, i)
        trend_up   = price > ema_t
        trend_down = price < ema_t
        vol_ok     = vol_cur > vol_avg
        vol_min_ok = vol_cur >= min_volume
        macd_bull  = macd_val > macd_sig
        macd_bear  = macd_val < macd_sig
        atr_ok     = round(atr_pct, 2) >= round(min_atr_pct * 100, 2)
        adx_ok     = adx_val >= min_adx
        not_spike  = candle_range <= spike_atr_mult * atr_val

        buy_signal  = cross_up   and trend_up   and vol_ok and vol_min_ok and macd_bull and not_spike
        sell_signal = cross_down and trend_down and vol_ok and vol_min_ok and macd_bear and not_spike

        if cross_up or cross_down:
            def _tick(flag): return "✔" if flag else "✘"
            side = "BUY" if cross_up else "SELL"
            logger.info(
                "  %-12s | %s | Price: %8.2f | "
                "Trend: %s  Vol: %s  MinVol: %s  ATR: %s(%.2f%%)  ADX: %s(%.1f)  MACD: %s  Spike: %s",
                ticker, side, price,
                _tick(trend_up  if cross_up else trend_down),
                _tick(vol_ok), _tick(vol_min_ok),
                _tick(atr_ok), atr_pct,
                _tick(adx_ok), adx_val,
                _tick(macd_bull if cross_up else macd_bear),
                _tick(not_spike),
            )

        if not buy_signal and not sell_signal:
            return None

        entry    = price
        candle_t = df.index[i]

        if not vol_min_ok:
            logger.info("  %-12s | BLOCKED — Volume too low (%,.0f < %,d)", ticker, vol_cur, min_volume)
            return None
        if not atr_ok:
            logger.info("  %-12s | BLOCKED — ATR too small (%.2f%% < %.2f%%)", ticker, atr_pct, min_atr_pct * 100)
            return None
        if not adx_ok:
            logger.info("  %-12s | BLOCKED — ADX too low (%.1f < %d)", ticker, adx_val, min_adx)
            return None
        if not not_spike:
            logger.info("  %-12s | BLOCKED — Spike candle (range ₹%.2f > %.1fx ATR ₹%.2f)",
                        ticker, candle_range, spike_atr_mult, atr_val)
            return None

        if buy_signal:
            sl         = entry - atr_val
            target     = entry + atr_val * rr_ratio
            risk_pct   = round((entry - sl)     / entry * 100, 2)
            reward_pct = round((target - entry) / entry * 100, 2)
            direction  = "BUY"
            logger.info(
                "  \033[1m\033[92m%-12s | ✔ BUY SIGNAL  — Entry: %.2f  SL: %.2f  Target: %.2f\033[0m",
                ticker, entry, sl, target)
        else:
            sl         = entry + atr_val
            target     = entry - atr_val * rr_ratio
            risk_pct   = round((sl - entry)     / entry * 100, 2)
            reward_pct = round((entry - target) / entry * 100, 2)
            direction  = "SELL"
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
            footer        = (
                f"EMA {fast_ema}/{slow_ema} cross · EMA {trend_ema} trend · "
                f"Vol · ATR≥{self._p('min_atr_pct')}% · ADX≥{min_adx} · MACD"
            ),
        )
