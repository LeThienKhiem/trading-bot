"""
Configuration constants for the trading bot.
Loads all secrets from .env and defines default parameters.
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
CLAUDE_TIMEOUT = 30  # seconds

# ── Supabase ─────────────────────────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

# ── Telegram ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── News (CoinGecko — free, no key required) ────────────────────────────────

# ── Trading Parameters ───────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100"))
MAX_POSITION_PERCENT = float(os.getenv("MAX_POSITION_PERCENT", "0.30"))
STOP_LOSS_MINIMUM_BALANCE = float(os.getenv("STOP_LOSS_MINIMUM_BALANCE", "70"))
MIN_CONFIDENCE_TO_TRADE = int(os.getenv("MIN_CONFIDENCE_TO_TRADE", "7"))

# ── Scheduler ────────────────────────────────────────────────────────────────
TRADE_CYCLE_HOURS = [0, 4, 8, 12, 16, 20]  # UTC hours for trade cycles
REVIEW_HOUR = 23       # UTC hour for nightly review
REVIEW_MINUTE = 30     # UTC minute for nightly review

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_FILE = "trading-bot.log"
LOG_LEVEL = "INFO"

# ── API Endpoints ────────────────────────────────────────────────────────────
FEAR_GREED_URL = "https://api.alternative.me/fng/"
COINGECKO_BTC_URL = "https://api.coingecko.com/api/v3/search/trending"
