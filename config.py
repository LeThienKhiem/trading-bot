"""
Configuration constants for the trading bot (Quant Edition).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Binance ──────────────────────────────────────────────────────────────────
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# ── Anthropic Claude ─────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_TIMEOUT = 30

# ── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Trading Parameters ───────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "150"))

# Position sizing (dynamic range based on Claude's confidence)
MIN_POSITION_PERCENT = float(os.getenv("MIN_POSITION_PERCENT", "0.20"))
MAX_POSITION_PERCENT = float(os.getenv("MAX_POSITION_PERCENT", "0.50"))

# Safety
STOP_LOSS_MINIMUM_BALANCE = float(os.getenv("STOP_LOSS_MINIMUM_BALANCE", "70"))
MIN_CONFIDENCE_TO_TRADE = int(os.getenv("MIN_CONFIDENCE_TO_TRADE", "7"))
MIN_RISK_REWARD_RATIO = float(os.getenv("MIN_RISK_REWARD_RATIO", "1.5"))

# Daily target (now used as a soft target, not hard stop)
DAILY_TARGET_PERCENT = float(os.getenv("DAILY_TARGET_PERCENT", "2.0"))

DRY_RUN = False  # Set to True via --verify flag at runtime

# ── Price Monitor ────────────────────────────────────────────────────────────
PRICE_CHECK_INTERVAL = int(os.getenv("PRICE_CHECK_INTERVAL", "30"))  # seconds
TRAILING_STOP_ACTIVATION_PCT = float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "1.5"))  # activate trailing stop after +1.5%
TRAILING_STOP_DISTANCE_PCT = float(os.getenv("TRAILING_STOP_DISTANCE_PCT", "1.0"))  # trail 1% below peak
EMERGENCY_DROP_PCT = float(os.getenv("EMERGENCY_DROP_PCT", "5.0"))  # emergency sell if down 5%
TRADE_COOLDOWN_SECONDS = int(os.getenv("TRADE_COOLDOWN_SECONDS", "1800"))  # 30 min cooldown after auto-sell

# ── Scheduler ────────────────────────────────────────────────────────────────
TRADE_CYCLE_HOURS = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22]  # Every 2h for more opportunities
REVIEW_HOUR = 23
REVIEW_MINUTE = 30

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE = "trading-bot.log"
LOG_LEVEL = "INFO"

# ── API Endpoints ────────────────────────────────────────────────────────────
FEAR_GREED_URL = "https://api.alternative.me/fng/"
