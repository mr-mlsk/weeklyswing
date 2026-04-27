# =============================================================================
# aws_scanner.py -- Weekly 7 EMA Bounce Scanner (AWS / Headless Version)
# =============================================================================
# Data source: yfinance (free, no auth, no token expiry)
# Identical signal logic to the backtested strategy
# Designed to run 24/7 on AWS EC2 Ubuntu via systemd
#
# RUN MODES:
#   python aws_scanner.py --scan        # Manual scan right now
#   python aws_scanner.py --test        # Test Telegram notification
#   python aws_scanner.py --status      # Show open positions
#   python aws_scanner.py --scheduler   # Start 24/7 scheduler (used by systemd)
# =============================================================================

import os
import sys
import logging
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import alert_config as cfg
from notifier import (dispatch_signal, dispatch_exit,
                      dispatch_summary, dispatch_raw,
                      test_telegram)
from position_tracker import (add_position, close_position, is_position_open,
                               get_open_positions, increment_weeks, log_signal)

# ── Logging (file + stdout) ───────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/aws_scanner.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ─── yfinance Data Fetcher ────────────────────────────────────────────────────

def _fyers_to_yf(symbol: str) -> str:
    """
    Convert Fyers symbol format to Yahoo Finance format.
    NSE:BHARTIARTL-EQ  ->  BHARTIARTL.NS
    """
    return symbol.replace("NSE:", "").replace("-EQ", "") + ".NS"


def fetch_weekly_yf(symbol: str, months: int = 12) -> pd.DataFrame:
    """
    Fetch weekly OHLCV from Yahoo Finance.
    Returns DataFrame: date | open | high | low | close | volume
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. Run: pip install yfinance")
        return pd.DataFrame()

    yf_sym = _fyers_to_yf(symbol)
    start  = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    end    = datetime.now().strftime("%Y-%m-%d")

    try:
        ticker = yf.Ticker(yf_sym)
        df     = ticker.history(start=start, end=end, interval="1wk",
                                auto_adjust=True)
        if df.empty:
            logger.warning("No yfinance data for %s (%s)", symbol, yf_sym)
            return pd.DataFrame()

        df = df.reset_index()
        df.columns = [c.lower() for c in df.columns]
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)

        # Keep only what we need
        df = df[["date","open","high","low","close","volume"]].copy()
        df = df.sort_values("date").reset_index(drop=True)
        logger.info("yfinance: %d weekly bars for %s", len(df), yf_sym)
        return df

    except Exception as e:
        logger.error("yfinance error for %s: %s", yf_sym, e)
        return pd.DataFrame()


# ─── EMA ──────────────────────────────────────────────────────────────────────

def compute_ema(series: pd.Series, period: int = 7) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ─── Signal Detection (identical to backtest logic) ───────────────────────────

def check_buy_signal(df: pd.DataFrame, symbol: str) -> dict | None:
    """
    Detect Weekly 7 EMA bounce BUY signal on the last completed candle.

    Entry conditions (all 3):
      1. This week close > 7 EMA
      2. Previous week close <= EMA x 1.015  (was near/below EMA)
      3. This week is green (close > open)
    """
    if df.empty or len(df) < cfg.EMA_PERIOD + 3:
        return None

    df      = df.copy().reset_index(drop=True)
    df["ema7"] = compute_ema(df["close"], cfg.EMA_PERIOD)

    i     = len(df) - 1       # last completed weekly candle
    row   = df.iloc[i]
    prev  = df.iloc[i - 1]

    ema_val  = row["ema7"]
    prev_ema = prev["ema7"]

    if pd.isna(ema_val) or pd.isna(prev_ema):
        return None

    close_above = row["close"] > ema_val
    prev_near   = prev["close"] <= prev_ema * cfg.EMA_BUFFER
    green       = row["close"] > row["open"]

    if not (close_above and prev_near and green):
        return None

    clean        = symbol.replace("NSE:", "").replace("-EQ", "")
    entry_price  = round(float(row["close"]), 2)
    ema_val_r    = round(float(ema_val), 2)
    target_price = round(entry_price * cfg.TARGET_MULT, 2)
    ema_diff_pct = round((entry_price - ema_val) / ema_val * 100, 2)
    candle_ret   = round((row["close"] - row["open"]) / row["open"] * 100, 2)
    signal_date  = row["date"]

    return {
        "symbol":            symbol,
        "clean_name":        clean,
        "entry_price":       entry_price,
        "ema_value":         ema_val_r,
        "target_price":      target_price,
        "stop_ref":          "Weekly close below 7 EMA",
        "signal_date":       signal_date.strftime("%Y-%m-%d"),
        "entry_date":        (signal_date + timedelta(days=3)).strftime("%Y-%m-%d"),
        "ema_diff_pct":      ema_diff_pct,
        "candle_return_pct": candle_ret,
        "signal_type":       "BUY",
        "notes":             f"EMA={ema_val_r} prev_close={prev['close']:.2f}",
    }


def check_exit(df: pd.DataFrame, position: dict) -> dict | None:
    """
    Check open position for exits:
      1. Target  : high >= entry x 1.30
      2. SL      : weekly close < 7 EMA
      3. Time    : held >= HOLD_WEEKS
    """
    if df.empty or len(df) < 2:
        return None

    df = df.copy().reset_index(drop=True)
    df["ema7"] = compute_ema(df["close"], cfg.EMA_PERIOD)

    cur     = df.iloc[-1]
    ema_val = float(cur["ema7"])
    ep      = float(position["entry_price"])
    target  = float(position["target_price"])
    weeks   = int(position.get("weeks_held", 0))

    # Target hit
    if float(cur["high"]) >= target:
        return {**position, "exit_price": target, "weeks_held": weeks,
                "exit_reason": "TARGET +30% HIT",
                "return_pct":  30.0,
                "rupee_pnl":   30000}

    # Stop Loss
    if not pd.isna(ema_val) and float(cur["close"]) < ema_val:
        ret = (float(cur["close"]) - ep) / ep * 100
        return {**position, "exit_price": round(float(cur["close"]), 2),
                "weeks_held": weeks, "exit_reason": "STOP LOSS -- Close below 7 EMA",
                "return_pct": round(ret, 2),
                "rupee_pnl":  round(ret / 100 * 100_000, 0)}

    # Time exit
    if weeks >= cfg.HOLD_WEEKS:
        ret = (float(cur["close"]) - ep) / ep * 100
        return {**position, "exit_price": round(float(cur["close"]), 2),
                "weeks_held": weeks, "exit_reason": "TIME EXIT -- 9 weeks complete",
                "return_pct": round(ret, 2),
                "rupee_pnl":  round(ret / 100 * 100_000, 0)}

    return None


# ─── Main Scan ────────────────────────────────────────────────────────────────

def run_scan(notify: bool = True):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info("=" * 60)
    logger.info("SCAN STARTED: %s | Universe: %d stocks", now, len(cfg.WATCHLIST))

    new_signals = []
    exits_fired = []

    for symbol in cfg.WATCHLIST:
        clean = symbol.replace("NSE:", "").replace("-EQ", "")
        df    = fetch_weekly_yf(symbol, months=9)

        if df.empty:
            logger.warning("Skipping %s -- no data", clean)
            continue

        # Check exits first for open positions
        if is_position_open(symbol):
            positions = {p["symbol"]: p for p in get_open_positions()}
            pos       = positions.get(symbol)
            if pos:
                exit_info = check_exit(df, pos)
                if exit_info:
                    close_position(symbol, exit_info["exit_price"],
                                   exit_info["exit_reason"])
                    log_signal({**exit_info, "signal_type": "EXIT"})
                    exits_fired.append(exit_info)
                    if notify:
                        dispatch_exit(exit_info)
                    logger.info("EXIT: %s | %s | Rs.%+.0f",
                                clean, exit_info["exit_reason"],
                                exit_info["rupee_pnl"])
            continue

        # Check for new BUY signal
        signal = check_buy_signal(df, symbol)
        if signal:
            add_position(signal)
            log_signal(signal)
            new_signals.append(signal)
            if notify:
                dispatch_signal(signal)
            logger.info("BUY SIGNAL: %s @ Rs.%.2f | EMA Rs.%.2f | +%.2f%%",
                        clean, signal["entry_price"],
                        signal["ema_value"], signal["ema_diff_pct"])

    increment_weeks()
    open_now = get_open_positions()

    # Summary log
    logger.info("SCAN DONE | Signals: %d | Exits: %d | Open: %d",
                len(new_signals), len(exits_fired), len(open_now))

    # No-signal Telegram nudge
    if notify and not new_signals and not exits_fired:
        dispatch_raw(
            f"*Weekly Scan -- {datetime.now().strftime('%d %b %Y')}*\n\n"
            f"No new signals this week.\n"
            f"Open positions: {len(open_now)}\n"
            f"Watchlist: {len(cfg.WATCHLIST)} stocks"
        )

    return new_signals, exits_fired


def send_monday_summary():
    open_pos = get_open_positions()
    logger.info("Monday summary: %d open positions", len(open_pos))
    dispatch_summary(open_pos)


# ─── Scheduler ────────────────────────────────────────────────────────────────

def start_scheduler():
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("apscheduler not installed: pip install apscheduler")
        sys.exit(1)

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    fri_h, fri_m = map(int, cfg.SCAN_FRIDAY_TIME.split(":"))
    mon_h, mon_m = map(int, cfg.SCAN_MONDAY_TIME.split(":"))

    scheduler.add_job(run_scan, CronTrigger(
        day_of_week="fri", hour=fri_h, minute=fri_m, timezone="Asia/Kolkata"),
        id="weekly_scan", name="Weekly 7 EMA Scan")

    scheduler.add_job(send_monday_summary, CronTrigger(
        day_of_week="mon", hour=mon_h, minute=mon_m, timezone="Asia/Kolkata"),
        id="monday_summary", name="Monday Summary")

    logger.info("Scheduler live | Friday %s IST | Monday %s IST",
                cfg.SCAN_FRIDAY_TIME, cfg.SCAN_MONDAY_TIME)

    dispatch_raw(
        f"*7 EMA Scanner -- AWS Online*\n\n"
        f"Server: AWS EC2\n"
        f"Watchlist: {len(cfg.WATCHLIST)} stocks\n"
        f"Friday scan : {cfg.SCAN_FRIDAY_TIME} IST\n"
        f"Monday summary: {cfg.SCAN_MONDAY_TIME} IST\n\n"
        f"_No Fyers auth needed -- using yfinance_"
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
        scheduler.shutdown()


# ─── Status Print ─────────────────────────────────────────────────────────────

def print_status():
    positions = get_open_positions()
    print(f"\n{'='*55}")
    print(f"  OPEN POSITIONS  ({len(positions)} total)")
    print(f"{'='*55}")
    if not positions:
        print("  No open positions.")
    for p in positions:
        print(f"  {p['clean_name']:<18} Entry: Rs.{float(p['entry_price']):>10,.2f}"
              f"  Target: Rs.{float(p['target_price']):>10,.2f}"
              f"  Week {p.get('weeks_held',0)}/9")
    print(f"{'='*55}\n")


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="7 EMA AWS Scanner")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan",      action="store_true", help="Manual scan now")
    group.add_argument("--test",      action="store_true", help="Test Telegram")
    group.add_argument("--status",    action="store_true", help="Open positions")
    group.add_argument("--scheduler", action="store_true", help="Start 24/7 scheduler")
    group.add_argument("--summary",   action="store_true", help="Send Monday summary now")
    args = parser.parse_args()

    if args.test:
        ok = test_telegram()
        print(f"Telegram: {'OK' if ok else 'FAILED'}")
    elif args.status:
        print_status()
    elif args.summary:
        send_monday_summary()
    elif args.scan:
        sigs, exits = run_scan(notify=True)
        print(f"\nDone. Signals: {len(sigs)} | Exits: {len(exits)}")
    elif args.scheduler:
        start_scheduler()
