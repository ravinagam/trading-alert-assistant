"""
Abstract base class and shared Signal dataclass for all strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd


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
    priority: bool = False  # True if stock is in HIGH_ATR_STOCKS list
    strategy_name: str = "" # Strategy that generated this signal
    footer: str = ""        # Describes the filters applied — shown in Telegram


class BaseStrategy(ABC):
    name: str = ""
    description: str = ""

    def __init__(self, params: dict[str, Any] = None):
        self.params = params or {}

    def _p(self, key: str, default: Any) -> Any:
        """Return param value from config, falling back to default."""
        return self.params.get(key, default)

    @abstractmethod
    def generate_signal(self, ticker: str, df: pd.DataFrame) -> Optional[Signal]:
        """Evaluate df and return a Signal if one fires, else None."""
        ...
