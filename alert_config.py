# =============================================================================
# alert_config.py -- Live Signal Alert Configuration
# =============================================================================
# Fill in your credentials before running live_scanner.py
# =============================================================================

# ── CHOOSE YOUR NOTIFICATION CHANNEL ─────────────────────────────────────────
# Set True for the channels you want to use
TELEGRAM_ENABLED  = True
WHATSAPP_ENABLED  = False   # Requires Twilio account (see setup below)

# ── TELEGRAM SETUP ────────────────────────────────────────────────────────────
# Step 1: Open Telegram, search @BotFather
# Step 2: Send /newbot, follow prompts, copy the API token
# Step 3: Start a chat with your bot, then visit:
#         https://api.telegram.org/bot<TOKEN>/getUpdates
#         to get your chat_id
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"         # e.g. "123456789:ABCdef..."
TELEGRAM_CHAT_ID   = "YOUR_CHAT_ID"           # e.g. "987654321"
                                               # For group: "-100123456789"

# ── WHATSAPP SETUP (via Twilio) ───────────────────────────────────────────────
# Step 1: Sign up at twilio.com (free trial)
# Step 2: Go to Messaging > Try it out > Send a WhatsApp message
# Step 3: Join sandbox by sending the join code from your WhatsApp
# Step 4: Fill in credentials below
TWILIO_ACCOUNT_SID  = "YOUR_ACCOUNT_SID"      # ACxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN   = "YOUR_AUTH_TOKEN"
TWILIO_FROM_WA      = "whatsapp:+14155238886"  # Twilio sandbox number
TWILIO_TO_WA        = "whatsapp:+91XXXXXXXXXX" # Your number with country code

# ── STOCK UNIVERSE ────────────────────────────────────────────────────────────
# Top 15 optimal stocks identified from backtest analysis
# (edit this list to add/remove stocks)
WATCHLIST = [
    "NSE:BHARTIARTL-EQ",
    "NSE:SUNDARMFIN-EQ",
    "NSE:HDFCAMC-EQ",
    "NSE:MUTHOOTFIN-EQ",
    "NSE:WIPRO-EQ",
    "NSE:ADANIPORTS-EQ",
    "NSE:POWERGRID-EQ",
    "NSE:DIXON-EQ",
    "NSE:BOSCHLTD-EQ",
    "NSE:BRITANNIA-EQ",
    "NSE:TRENT-EQ",
    "NSE:NTPC-EQ",
    "NSE:AUROPHARMA-EQ",
    "NSE:BAJAJ-AUTO-EQ",
    "NSE:NHPC-EQ",
]

# ── STRATEGY PARAMETERS ───────────────────────────────────────────────────────
EMA_PERIOD    = 7        # Weekly 7 EMA
EMA_BUFFER    = 1.015    # Previous close <= EMA x 1.5% (near EMA buffer)
TARGET_MULT   = 1.30     # +30% target
HOLD_WEEKS    = 9        # ~2 month hold

# ── SCHEDULER ────────────────────────────────────────────────────────────────
# The scanner runs automatically at these times (IST)
# Friday 3:45 PM -- after NSE weekly close, check for new signals
# Monday 9:10 AM -- reminder of open signals before market opens
SCAN_FRIDAY_TIME  = "15:45"   # HH:MM IST
SCAN_MONDAY_TIME  = "09:10"   # HH:MM IST

# ── FILE PATHS ────────────────────────────────────────────────────────────────
OPEN_POSITIONS_FILE = "open_positions.json"   # Tracks open trades
SIGNAL_LOG_FILE     = "signal_log.csv"        # All signals ever sent
SCANNER_LOG_FILE    = "scanner.log"
