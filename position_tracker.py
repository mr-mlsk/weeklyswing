# =============================================================================
# position_tracker.py -- Persistent Open Position & Signal Log Manager
# =============================================================================

import json
import csv
import os
import logging
from datetime import datetime, date
import alert_config as cfg

logger = logging.getLogger(__name__)


# ─── Open Positions (JSON) ───────────────────────────────────────────────────

def load_positions() -> dict:
    """Load open positions. Returns {symbol: position_dict}."""
    if not os.path.exists(cfg.OPEN_POSITIONS_FILE):
        return {}
    try:
        with open(cfg.OPEN_POSITIONS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Error loading positions: %s", e)
        return {}


def save_positions(positions: dict):
    """Save open positions to file."""
    try:
        with open(cfg.OPEN_POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2, default=str)
        logger.info("Positions saved: %d open", len(positions))
    except Exception as e:
        logger.error("Error saving positions: %s", e)


def add_position(signal: dict):
    """Record a new open position."""
    positions = load_positions()
    sym = signal["symbol"]
    positions[sym] = {
        "symbol":        sym,
        "clean_name":    signal["clean_name"],
        "entry_price":   signal["entry_price"],
        "ema_value":     signal["ema_value"],
        "target_price":  signal["target_price"],
        "signal_date":   str(signal["signal_date"]),
        "entry_date":    str(signal.get("entry_date", "")),
        "weeks_held":    0,
        "opened_at":     str(datetime.now()),
    }
    save_positions(positions)
    logger.info("Position opened: %s @ Rs.%.2f", sym, signal["entry_price"])


def close_position(symbol: str, exit_price: float, reason: str) -> dict:
    """Close a position and return the closed position dict."""
    positions = load_positions()
    pos = positions.pop(symbol, None)
    if pos:
        pos["exit_price"]  = exit_price
        pos["exit_reason"] = reason
        pos["closed_at"]   = str(datetime.now())
        save_positions(positions)
        logger.info("Position closed: %s @ Rs.%.2f | %s", symbol, exit_price, reason)
    return pos


def increment_weeks():
    """Call every Friday to increment week count on all open positions."""
    positions = load_positions()
    for sym in positions:
        positions[sym]["weeks_held"] = positions[sym].get("weeks_held", 0) + 1
    save_positions(positions)


def is_position_open(symbol: str) -> bool:
    """Check if a symbol already has an open position."""
    return symbol in load_positions()


def get_open_positions() -> list:
    """Return list of all open position dicts."""
    return list(load_positions().values())


def get_positions_exceeding_hold(max_weeks: int = None) -> list:
    """Return positions that have been held for >= max_weeks."""
    if max_weeks is None:
        max_weeks = cfg.HOLD_WEEKS
    positions = load_positions()
    return [p for p in positions.values() if p.get("weeks_held", 0) >= max_weeks]


# ─── Signal Log (CSV) ────────────────────────────────────────────────────────

SIGNAL_LOG_HEADERS = [
    "timestamp", "symbol", "signal_type", "entry_price",
    "ema_value", "target_price", "exit_price", "return_pct",
    "rupee_pnl", "weeks_held", "exit_reason", "notes"
]


def log_signal(signal: dict):
    """Append a signal event to the CSV log."""
    file_exists = os.path.exists(cfg.SIGNAL_LOG_FILE)
    try:
        with open(cfg.SIGNAL_LOG_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SIGNAL_LOG_HEADERS,
                                    extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            row = {
                "timestamp":   str(datetime.now()),
                "symbol":      signal.get("symbol", ""),
                "signal_type": signal.get("signal_type", "BUY"),
                "entry_price": signal.get("entry_price", ""),
                "ema_value":   signal.get("ema_value", ""),
                "target_price":signal.get("target_price", ""),
                "exit_price":  signal.get("exit_price", ""),
                "return_pct":  signal.get("return_pct", ""),
                "rupee_pnl":   signal.get("rupee_pnl", ""),
                "weeks_held":  signal.get("weeks_held", ""),
                "exit_reason": signal.get("exit_reason", ""),
                "notes":       signal.get("notes", ""),
            }
            writer.writerow(row)
    except Exception as e:
        logger.error("Error writing signal log: %s", e)


def load_signal_log() -> list:
    """Load all historical signal logs."""
    if not os.path.exists(cfg.SIGNAL_LOG_FILE):
        return []
    try:
        with open(cfg.SIGNAL_LOG_FILE, "r") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        logger.error("Error reading signal log: %s", e)
        return []
