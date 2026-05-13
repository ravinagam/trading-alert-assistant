"""
Strategy registry — loads all known strategies and filters by enabled flag
from strategies_config.json. Passes each strategy's params block on init.
"""

import json
import logging
import os

from strategies.base import BaseStrategy
from strategies.ema_crossover import EMACrossoverStrategy
from strategies.sp_ema_crossover import SPEMACrossoverStrategy
from strategies.orb import ORBStrategy
from strategies.vwap import VWAPStrategy
from strategies.ema9_sma20 import EMA9SMA20Strategy

logger = logging.getLogger(__name__)

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "strategies_config.json")

# Maps strategy name → class (not instance — instantiated after loading params)
_STRATEGY_CLASSES: dict[str, type] = {
    "ema_crossover_pro": EMACrossoverStrategy,
    "sp_ema_crossover":  SPEMACrossoverStrategy,
    "orb":               ORBStrategy,
    "vwap":              VWAPStrategy,
    "ema9_sma20":        EMA9SMA20Strategy,
}


def _load_config() -> dict:
    with open(_CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def _build_instances(cfg: dict) -> dict[str, BaseStrategy]:
    """Instantiate each strategy with its params from config."""
    instances = {}
    for name, cls in _STRATEGY_CLASSES.items():
        entry  = cfg.get("strategies", {}).get(name, {})
        params = entry.get("params", {})
        instances[name] = cls(params=params)
    return instances


def get_active_strategies() -> list[BaseStrategy]:
    """Return strategy instances that are currently enabled in config."""
    cfg       = _load_config()
    instances = _build_instances(cfg)
    active    = []
    for name, instance in instances.items():
        entry = cfg.get("strategies", {}).get(name, {})
        if entry.get("enabled", False):
            active.append(instance)
    if not active:
        logger.warning("No strategies are enabled — no signals will be generated.")
    return active


def list_all() -> dict[str, dict]:
    """Return all strategy entries from config with their enabled state."""
    cfg    = _load_config()
    result = {}
    for name, cls in _STRATEGY_CLASSES.items():
        entry = cfg.get("strategies", {}).get(name, {})
        result[name] = {
            "enabled":     entry.get("enabled", False),
            "description": entry.get("description", cls.description),
            "params":      entry.get("params", {}),
        }
    return result


def get_params(strategy_name: str) -> dict:
    """Return the params block for a given strategy name."""
    cfg   = _load_config()
    entry = cfg.get("strategies", {}).get(strategy_name, {})
    return entry.get("params", {})


def set_enabled(strategy_name: str, enabled: bool) -> None:
    """Enable or disable a strategy by name and persist to config file."""
    if strategy_name not in _STRATEGY_CLASSES:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Available: {', '.join(_STRATEGY_CLASSES)}"
        )
    cfg = _load_config()
    cfg.setdefault("strategies", {}).setdefault(strategy_name, {})["enabled"] = enabled
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    state = "enabled" if enabled else "disabled"
    logger.info("Strategy '%s' %s.", strategy_name, state)
