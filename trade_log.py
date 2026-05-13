"""
Thread-safe trade journal.

The broker writes one record per signal. The monitor thread updates it when
the position closes. trade_status.py reads it for enriched summaries.

Log file: trades_YYYYMMDD.json  (one file per trading day, auto-created)
"""

import json
import os
import threading
from datetime import date, datetime
from typing import Optional

_lock = threading.Lock()


def _log_path() -> str:
    return f"trades_{date.today().strftime('%Y%m%d')}.json"


def _load() -> dict:
    path = _log_path()
    if not os.path.exists(path):
        return {"date": str(date.today()), "trades": []}
    with open(path) as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(_log_path(), "w") as f:
        json.dump(data, f, indent=2, default=str)


def record_entry(
    ticker:          str,
    direction:       str,
    strategy:        str,
    signal_entry:    float,
    fill_price:      float,
    sl_price:        float,
    target_price:    float,
    qty:             int,
    entry_order_id:  str,
    sl_order_id:     Optional[str],
    target_order_id: Optional[str],
) -> None:
    with _lock:
        data = _load()
        data["trades"].append({
            "ticker":           ticker,
            "direction":        direction,
            "strategy":         strategy,
            "signal_entry":     signal_entry,
            "fill_price":       fill_price,
            "sl_price":         sl_price,
            "target_price":     target_price,
            "qty":              qty,
            "entry_order_id":   entry_order_id,
            "sl_order_id":      sl_order_id,
            "target_order_id":  target_order_id,
            "status":           "OPEN",
            "exit_price":       None,
            "exit_time":        None,
            "pnl":              None,
            "entry_time":       datetime.now().strftime("%d-%b %H:%M:%S"),
        })
        _save(data)


def record_exit(
    entry_order_id: str,
    status:         str,       # "TARGET_HIT" | "SL_HIT" | "KITE_CLOSED"
    exit_price:     float,
    pnl:            float,
) -> None:
    with _lock:
        data = _load()
        for trade in data["trades"]:
            if trade["entry_order_id"] == entry_order_id:
                trade["status"]     = status
                trade["exit_price"] = exit_price
                trade["exit_time"]  = datetime.now().strftime("%d-%b %H:%M:%S")
                trade["pnl"]        = round(pnl, 2)
                break
        _save(data)


def load_today() -> list[dict]:
    with _lock:
        return _load().get("trades", [])


def load_days(n: int = 1) -> list[dict]:
    """Load trades from the last n calendar days, newest first."""
    from datetime import timedelta
    trades = []
    today  = date.today()
    for i in range(n):
        d    = today - timedelta(days=i)
        path = f"trades_{d.strftime('%Y%m%d')}.json"
        if os.path.exists(path):
            with _lock:
                with open(path) as f:
                    day_data = json.load(f)
            for t in day_data.get("trades", []):
                t.setdefault("log_date", str(d))
                trades.append(t)
    return trades


def available_log_dates() -> list[str]:
    """Return sorted list of dates that have a trade log file."""
    import glob
    paths = sorted(glob.glob("trades_????????.json"), reverse=True)
    return [p.replace("trades_", "").replace(".json", "") for p in paths]


def log_path() -> str:
    return _log_path()
