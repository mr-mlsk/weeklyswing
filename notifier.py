# =============================================================================
# notifier.py -- Telegram + WhatsApp Signal Notifications
# =============================================================================

import logging
import requests
import alert_config as cfg

logger = logging.getLogger(__name__)


# ─── Message Formatter ───────────────────────────────────────────────────────

def _format_signal_message(signal: dict, msg_type: str = "telegram") -> str:
    """
    Build a clean, readable alert message.

    signal keys:
        symbol, clean_name, entry_price, ema_value, target_price,
        stop_ref, signal_date, entry_date, ema_diff_pct, candle_return_pct
    """
    sym        = signal["clean_name"]
    entry      = signal["entry_price"]
    ema        = signal["ema_value"]
    target     = signal["target_price"]
    stop_ref   = signal["stop_ref"]          # "weekly close below 7 EMA"
    sig_date   = signal["signal_date"]
    ema_pct    = signal["ema_diff_pct"]      # % close is above EMA
    candle_ret = signal["candle_return_pct"] # signal week return

    sep = "-" * 30

    if msg_type == "telegram":
        return (
            f"*WEEKLY 7 EMA BOUNCE SIGNAL*\n"
            f"{sep}\n"
            f"*{sym}*\n\n"
            f"Signal Week : {sig_date}\n"
            f"Entry Price : Rs.{entry:,.2f}  *(next Monday open)*\n"
            f"Weekly 7 EMA: Rs.{ema:,.2f}\n"
            f"Close vs EMA: +{ema_pct:.2f}% above\n"
            f"Candle Return: +{candle_ret:.2f}% (green)\n\n"
            f"*Target (+30%)*: Rs.{target:,.2f}\n"
            f"*Stop Loss*: Weekly close below 7 EMA\n"
            f"*Hold*: Up to 9 weeks (~2 months)\n\n"
            f"_Strategy: Weekly 7 EMA Bounce_\n"
            f"_Capital: Rs.1,00,000 per trade_"
        )
    else:
        # Plain text for WhatsApp
        return (
            f"WEEKLY 7 EMA BOUNCE SIGNAL\n"
            f"{sep}\n"
            f"Stock: {sym}\n\n"
            f"Signal Week : {sig_date}\n"
            f"Entry Price : Rs.{entry:,.2f} (next Monday open)\n"
            f"Weekly 7 EMA: Rs.{ema:,.2f}\n"
            f"Close vs EMA: +{ema_pct:.2f}% above\n"
            f"Candle Return: +{candle_ret:.2f}% (green)\n\n"
            f"Target (+30%): Rs.{target:,.2f}\n"
            f"Stop Loss: Weekly close below 7 EMA\n"
            f"Hold: Up to 9 weeks (~2 months)\n\n"
            f"Strategy: Weekly 7 EMA Bounce\n"
            f"Capital: Rs.1,00,000 per trade"
        )


def _format_exit_message(signal: dict, msg_type: str = "telegram") -> str:
    """Alert for SL hit or target reached on an open position."""
    sym    = signal["clean_name"]
    reason = signal["exit_reason"]
    ep     = signal["entry_price"]
    xp     = signal["exit_price"]
    ret    = (xp - ep) / ep * 100
    pnl    = (xp - ep) / ep * 100_000
    emoji  = "TARGET HIT" if "TARGET" in reason else "STOP LOSS"

    if msg_type == "telegram":
        color = "+" if pnl >= 0 else ""
        return (
            f"*{emoji}*\n"
            f"*{sym}*\n\n"
            f"Entry: Rs.{ep:,.2f}\n"
            f"Exit : Rs.{xp:,.2f}\n"
            f"Return: {color}{ret:.2f}%\n"
            f"P&L   : {color}Rs.{pnl:,.0f}\n\n"
            f"Reason: {reason}"
        )
    else:
        color = "+" if pnl >= 0 else ""
        return (
            f"{emoji}\n"
            f"Stock: {sym}\n\n"
            f"Entry: Rs.{ep:,.2f}\n"
            f"Exit : Rs.{xp:,.2f}\n"
            f"Return: {color}{ret:.2f}%\n"
            f"P&L   : {color}Rs.{pnl:,.0f}\n\n"
            f"Reason: {reason}"
        )


def _format_weekly_summary(open_positions: list, msg_type: str = "telegram") -> str:
    """Monday morning summary of all open positions."""
    if not open_positions:
        return "No open positions this week."

    lines = ["*OPEN POSITIONS SUMMARY*\n"] if msg_type == "telegram" else ["OPEN POSITIONS SUMMARY\n"]
    lines.append("-" * 30)
    for pos in open_positions:
        sym    = pos["clean_name"]
        ep     = pos["entry_price"]
        target = pos["target_price"]
        ema    = pos["ema_value"]
        wk     = pos.get("weeks_held", "?")
        lines.append(
            f"\n{sym}\n"
            f"  Entry: Rs.{ep:,.2f}  |  Target: Rs.{target:,.2f}\n"
            f"  7 EMA SL ref: Rs.{ema:,.2f}  |  Week {wk}/9"
        )
    return "\n".join(lines)


# ─── Telegram Sender ──────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success."""
    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    cfg.TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if data.get("ok"):
            logger.info("Telegram sent successfully")
            return True
        else:
            logger.error("Telegram error: %s", data)
            return False
    except Exception as e:
        logger.error("Telegram exception: %s", e)
        return False


def test_telegram() -> bool:
    """Send a test message to confirm Telegram is configured correctly."""
    msg = (
        "*7 EMA Signal Bot -- Connected*\n\n"
        "Your live signal scanner is active.\n"
        "Buy signals from the Weekly 7 EMA Bounce strategy\n"
        "will appear here every Friday after 3:45 PM IST."
    )
    return send_telegram(msg)


# ─── WhatsApp Sender (Twilio) ─────────────────────────────────────────────────

def send_whatsapp(text: str) -> bool:
    """Send a WhatsApp message via Twilio. Returns True on success."""
    try:
        from twilio.rest import Client
        client = Client(cfg.TWILIO_ACCOUNT_SID, cfg.TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=text,
            from_=cfg.TWILIO_FROM_WA,
            to=cfg.TWILIO_TO_WA,
        )
        logger.info("WhatsApp sent: SID %s", message.sid)
        return True
    except ImportError:
        logger.error("twilio not installed. Run: pip install twilio")
        return False
    except Exception as e:
        logger.error("WhatsApp exception: %s", e)
        return False


def test_whatsapp() -> bool:
    msg = (
        "7 EMA Signal Bot -- Connected\n\n"
        "Your live signal scanner is active.\n"
        "Buy signals from the Weekly 7 EMA Bounce strategy\n"
        "will appear here every Friday after 3:45 PM IST."
    )
    return send_whatsapp(msg)


# ─── Unified Dispatcher ───────────────────────────────────────────────────────

def dispatch_signal(signal: dict):
    """Send a BUY signal alert to all enabled channels."""
    if cfg.TELEGRAM_ENABLED:
        msg = _format_signal_message(signal, "telegram")
        send_telegram(msg)

    if cfg.WHATSAPP_ENABLED:
        msg = _format_signal_message(signal, "whatsapp")
        send_whatsapp(msg)


def dispatch_exit(signal: dict):
    """Send an EXIT alert (SL hit / target hit) to all enabled channels."""
    if cfg.TELEGRAM_ENABLED:
        send_telegram(_format_exit_message(signal, "telegram"))
    if cfg.WHATSAPP_ENABLED:
        send_whatsapp(_format_exit_message(signal, "whatsapp"))


def dispatch_summary(open_positions: list):
    """Send Monday morning summary of open positions."""
    if cfg.TELEGRAM_ENABLED:
        send_telegram(_format_weekly_summary(open_positions, "telegram"))
    if cfg.WHATSAPP_ENABLED:
        send_whatsapp(_format_weekly_summary(open_positions, "whatsapp"))


def dispatch_raw(text: str):
    """Send any plain text message to all enabled channels."""
    if cfg.TELEGRAM_ENABLED:
        send_telegram(text)
    if cfg.WHATSAPP_ENABLED:
        send_whatsapp(text)
