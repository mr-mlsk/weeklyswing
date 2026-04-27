# =============================================================================
# live_scanner.py -- Weekly 7 EMA Bounce Strategy -- Live Signal Scanner
# =============================================================================
#
# WHAT THIS DOES:
#   1. Fetches the latest weekly OHLCV for all watchlist stocks from Fyers
#   2. Computes Weekly 7 EMA on the fresh data
#   3. Checks for BUY signals (close > 7 EMA, prev close near EMA, green candle)
#   4. Checks open positions for:
#        - Stop Loss  : current week close < 7 EMA
#        - Target Hit : current week high >= entry x 1.30
#        - Time Exit  : position held >= HOLD_WEEKS
#   5. Fires Telegram / WhatsApp alerts for every new event
#   6. Logs everything to signal_log.csv and open_positions.json
#
# RUN MODES:
#   python live_scanner.py --scan        # Manual scan right now
#   python live_scanner.py --test        # Test notifications only
#   python live_scanner.py --status      # Show open positions
#   python live_scanner.py --scheduler   # Start auto scheduler (run 24/7)
#
# =============================================================================

import os
import sys
import logging
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import alert_config as cfg
from auth import get_fyers_client
from data_fetcher import fetch_weekly_ohlcv
from notifier import (dispatch_signal, dispatch_exit,
                      dispatch_summary, dispatch_raw,
                      test_telegram, test_whatsapp)
from position_tracker import (add_position, close_position, is_position_open,
                               get_open_positions, get_positions_exceeding_hold,
                               increment_weeks, log_signal)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(cfg.SCANNER_LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ─── EMA Calculation ──────────────────────────────────────────────────────────

def compute_ema(series: pd.Series, period: int = 7) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ─── Core Signal Detection ────────────────────────────────────────────────────

def check_signal(df: pd.DataFrame, symbol: str) -> dict | None:
    """
    Run signal detection on the latest weekly data.
    Looks at the LAST COMPLETED weekly candle.

    Returns a signal dict if a BUY is detected, else None.
    """
    if df.empty or len(df) < cfg.EMA_PERIOD + 3:
        logger.warning("Insufficient data for %s", symbol)
        return None

    df = df.copy().reset_index(drop=True)
    df["ema7"] = compute_ema(df["close"], cfg.EMA_PERIOD)

    # Last two complete candles
    i    = len(df) - 1   # most recent candle (last completed week)
    row  = df.iloc[i]
    prev = df.iloc[i - 1]

    ema_val   = row["ema7"]
    prev_ema  = prev["ema7"]

    if pd.isna(ema_val) or pd.isna(prev_ema):
        return None

    # ── Entry Conditions ──────────────────────────────────────────────────────
    close_above_ema = row["close"] > ema_val
    prev_near_ema   = prev["close"] <= prev_ema * cfg.EMA_BUFFER
    green_candle    = row["close"] > row["open"]

    if not (close_above_ema and prev_near_ema and green_candle):
        return None

    # ── Signal confirmed ──────────────────────────────────────────────────────
    clean_name   = symbol.replace("NSE:", "").replace("-EQ", "")
    entry_price  = row["close"]                    # This week's close
    target_price = entry_price * cfg.TARGET_MULT
    ema_diff_pct = (entry_price - ema_val) / ema_val * 100
    candle_ret   = (row["close"] - row["open"]) / row["open"] * 100

    # Next Monday is the realistic entry
    signal_date  = row["date"]
    entry_date   = signal_date + timedelta(days=3)  # approximate next Mon

    signal = {
        "symbol":            symbol,
        "clean_name":        clean_name,
        "entry_price":       round(entry_price, 2),
        "ema_value":         round(ema_val, 2),
        "target_price":      round(target_price, 2),
        "stop_ref":          "Weekly close below 7 EMA",
        "signal_date":       signal_date.strftime("%Y-%m-%d"),
        "entry_date":        entry_date.strftime("%Y-%m-%d"),
        "ema_diff_pct":      round(ema_diff_pct, 2),
        "candle_return_pct": round(candle_ret, 2),
        "signal_type":       "BUY",
        "notes":             f"EMA={ema_val:.2f} | prev_close={prev['close']:.2f}",
    }
    logger.info("SIGNAL: %s | Close %.2f | EMA %.2f | +%.2f%% above",
                clean_name, entry_price, ema_val, ema_diff_pct)
    return signal


def check_exit(df: pd.DataFrame, position: dict) -> dict | None:
    """
    Check if an open position should be exited.
    Returns exit dict if exit triggered, else None.
    """
    if df.empty or len(df) < 2:
        return None

    df = df.copy().reset_index(drop=True)
    df["ema7"] = compute_ema(df["close"], cfg.EMA_PERIOD)

    current = df.iloc[-1]
    ema_val = current["ema7"]
    ep      = position["entry_price"]
    target  = position["target_price"]
    weeks   = position.get("weeks_held", 0)

    # 1. Target: high >= entry x 1.30
    if current["high"] >= target:
        return {**position,
                "exit_price":  target,
                "exit_reason": "TARGET +30% HIT",
                "return_pct":  round((target - ep) / ep * 100, 2),
                "rupee_pnl":   round((target - ep) / ep * 100_000, 0)}

    # 2. Stop Loss: weekly close < 7 EMA
    if not pd.isna(ema_val) and current["close"] < ema_val:
        ret = (current["close"] - ep) / ep * 100
        return {**position,
                "exit_price":  round(current["close"], 2),
                "exit_reason": "STOP LOSS -- Close below 7 EMA",
                "return_pct":  round(ret, 2),
                "rupee_pnl":   round(ret / 100 * 100_000, 0)}

    # 3. Time Exit: held >= HOLD_WEEKS
    if weeks >= cfg.HOLD_WEEKS:
        ret = (current["close"] - ep) / ep * 100
        return {**position,
                "exit_price":  round(current["close"], 2),
                "exit_reason": "TIME EXIT -- 9 weeks complete",
                "return_pct":  round(ret, 2),
                "rupee_pnl":   round(ret / 100 * 100_000, 0)}

    return None


# ─── Main Scan Function ───────────────────────────────────────────────────────

def run_scan(fyers=None, notify: bool = True):
    """
    Full scan cycle:
      - Fetch latest weekly data for all watchlist symbols
      - Check for new BUY signals (skip if already in a position)
      - Check open positions for exits
      - Send notifications and update logs
    """
    today = datetime.now().strftime("%Y-%m-%d")
    # Fetch last 6 months for fresh EMA warmup
    start = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info("SCAN STARTED: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("Universe: %d symbols", len(cfg.WATCHLIST))

    if fyers is None:
        fyers = get_fyers_client()

    new_signals  = []
    exits_fired  = []

    for symbol in cfg.WATCHLIST:
        clean = symbol.replace("NSE:", "").replace("-EQ", "")
        logger.info("Scanning: %s", clean)

        df = fetch_weekly_ohlcv(fyers, symbol, start, today)
        if df.empty:
            logger.warning("No data for %s", clean)
            continue

        # ── Check open position for exits first ──────────────────────────────
        if is_position_open(symbol):
            positions = {p["symbol"]: p for p in get_open_positions()}
            pos       = positions.get(symbol)
            if pos:
                exit_info = check_exit(df, pos)
                if exit_info:
                    close_position(symbol, exit_info["exit_price"], exit_info["exit_reason"])
                    log_signal({**exit_info, "signal_type": "EXIT"})
                    exits_fired.append(exit_info)
                    if notify:
                        dispatch_exit(exit_info)
                    logger.info("EXIT: %s | %s | Rs.%+.0f",
                                clean, exit_info["exit_reason"], exit_info["rupee_pnl"])
            continue   # Don't also check for new entry if was in position

        # ── Check for new BUY signal ──────────────────────────────────────────
        signal = check_signal(df, symbol)
        if signal:
            add_position(signal)
            log_signal(signal)
            new_signals.append(signal)
            if notify:
                dispatch_signal(signal)

    increment_weeks()

    # ── Scan summary ──────────────────────────────────────────────────────────
    open_now = get_open_positions()
    summary_lines = [
        f"Scan complete: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"New signals : {len(new_signals)}",
        f"Exits fired : {len(exits_fired)}",
        f"Open positions: {len(open_now)}",
    ]
    if new_signals:
        summary_lines.append("\nNew BUY signals:")
        for s in new_signals:
            summary_lines.append(f"  {s['clean_name']} @ Rs.{s['entry_price']:,.2f}"
                                  f" | Target Rs.{s['target_price']:,.2f}")
    if exits_fired:
        summary_lines.append("\nExits:")
        for e in exits_fired:
            summary_lines.append(f"  {e['clean_name']} | {e['exit_reason']} | "
                                  f"Rs.{e['rupee_pnl']:+,.0f}")

    summary_text = "\n".join(summary_lines)
    logger.info(summary_text)

    if notify and (new_signals or exits_fired):
        # No separate summary message -- individual alerts already sent
        pass
    elif notify and not new_signals and not exits_fired:
        dispatch_raw(
            f"*Weekly Scan -- {datetime.now().strftime('%d %b %Y')}*\n\n"
            f"No new signals today.\n"
            f"Open positions: {len(open_now)}\n\n"
            f"Watchlist: {len(cfg.WATCHLIST)} stocks monitored"
        )

    return new_signals, exits_fired


# ─── Monday Summary ───────────────────────────────────────────────────────────

def send_monday_summary():
    """Send open positions reminder every Monday morning."""
    open_pos = get_open_positions()
    logger.info("Monday summary: %d open positions", len(open_pos))
    dispatch_summary(open_pos)


# ─── CLI Interface ────────────────────────────────────────────────────────────

def print_status():
    """Print current open positions to console."""
    positions = get_open_positions()
    print(f"\n{'='*55}")
    print(f"  OPEN POSITIONS  ({len(positions)} total)")
    print(f"{'='*55}")
    if not positions:
        print("  No open positions.")
    for p in positions:
        ep  = p["entry_price"]
        tgt = p["target_price"]
        wk  = p.get("weeks_held", 0)
        print(f"  {p['clean_name']:<18} Entry: Rs.{ep:>10,.2f}"
              f"  Target: Rs.{tgt:>10,.2f}  Week {wk}/9")
    print(f"{'='*55}\n")


def start_scheduler():
    """
    Start the APScheduler to run scans automatically.
    Friday 3:45 PM IST -> full scan + signal alerts
    Monday 9:10 AM IST -> position summary
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("APScheduler not installed. Run: pip install apscheduler")
        sys.exit(1)

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")

    fri_h, fri_m = map(int, cfg.SCAN_FRIDAY_TIME.split(":"))
    mon_h, mon_m = map(int, cfg.SCAN_MONDAY_TIME.split(":"))

    scheduler.add_job(
        func=run_scan,
        trigger=CronTrigger(day_of_week="fri", hour=fri_h, minute=fri_m,
                             timezone="Asia/Kolkata"),
        id="weekly_scan",
        name="Weekly 7 EMA Signal Scan",
    )
    scheduler.add_job(
        func=send_monday_summary,
        trigger=CronTrigger(day_of_week="mon", hour=mon_h, minute=mon_m,
                             timezone="Asia/Kolkata"),
        id="monday_summary",
        name="Monday Position Summary",
    )

    print(f"\nScheduler started (IST timezone)")
    print(f"  Friday {cfg.SCAN_FRIDAY_TIME} IST  -> full scan + signals")
    print(f"  Monday {cfg.SCAN_MONDAY_TIME} IST  -> position summary")
    print(f"\nPress Ctrl+C to stop\n")

    dispatch_raw(
        f"*7 EMA Scanner -- Scheduler Started*\n\n"
        f"Monitoring {len(cfg.WATCHLIST)} stocks\n"
        f"Friday scan: {cfg.SCAN_FRIDAY_TIME} IST\n"
        f"Monday summary: {cfg.SCAN_MONDAY_TIME} IST"
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")
        scheduler.shutdown()


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="7 EMA Live Signal Scanner")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan",      action="store_true",
                       help="Run a manual scan right now")
    group.add_argument("--test",      action="store_true",
                       help="Send test notifications to verify setup")
    group.add_argument("--status",    action="store_true",
                       help="Show open positions")
    group.add_argument("--scheduler", action="store_true",
                       help="Start automated weekly scheduler")
    group.add_argument("--summary",   action="store_true",
                       help="Send Monday open positions summary now")
    args = parser.parse_args()

    if args.test:
        print("\nSending test notifications...")
        if cfg.TELEGRAM_ENABLED:
            ok = test_telegram()
            print(f"  Telegram: {'OK' if ok else 'FAILED -- check BOT_TOKEN and CHAT_ID'}")
        if cfg.WHATSAPP_ENABLED:
            ok = test_whatsapp()
            print(f"  WhatsApp: {'OK' if ok else 'FAILED -- check Twilio credentials'}")

    elif args.status:
        print_status()

    elif args.summary:
        send_monday_summary()
        print("Summary sent.")

    elif args.scan:
        print("\nRunning manual scan...")
        sigs, exits = run_scan(notify=True)
        print(f"\nDone. Signals: {len(sigs)} | Exits: {len(exits)}")

    elif args.scheduler:
        start_scheduler()
