"""
Entry point for the self-learning crypto trading bot.
Sets up logging, validates configuration, and starts the APScheduler
with two jobs:
  1. Trade cycle — every 4 hours (00:00, 04:00, ..., 20:00 UTC)
  2. Nightly review — daily at 23:30 UTC
"""

import logging
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from bot import market_data, brain, executor, memory, reviewer, notifier

# ── Logging Setup ────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """Configure logging to both console and file."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        ],
    )

logger = logging.getLogger(__name__)


# ── Configuration Validation ─────────────────────────────────────────────────

def validate_config() -> bool:
    """
    Check that all required environment variables are set.

    Returns:
        True if all required config is present, False otherwise.
    """
    required = {
        "BINANCE_API_KEY": config.BINANCE_API_KEY,
        "BINANCE_API_SECRET": config.BINANCE_API_SECRET,
        "ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY,
        "SUPABASE_URL": config.SUPABASE_URL,
        "SUPABASE_ANON_KEY": config.SUPABASE_ANON_KEY,
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.error(f"Missing required config: {', '.join(missing)}")
        logger.error("Please set these in your .env file. See .env.example.")
        return False

    return True


# ── Trade Cycle ──────────────────────────────────────────────────────────────

def trade_cycle() -> None:
    """
    Run one full trade cycle:
    1. Fetch all market data
    2. Get account balance and open positions
    3. Ask Claude for a trading decision
    4. Run safety checks and execute if appropriate
    5. Save everything to Supabase
    """
    logger.info("=" * 60)
    logger.info(f"TRADE CYCLE — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    # Step 1: Fetch market data
    logger.info("Step 1: Fetching market data...")
    mkt_data = market_data.fetch_all_market_data()
    if mkt_data is None:
        logger.error("Failed to fetch market data — skipping this cycle")
        return

    # Save market context to Supabase
    memory.save_market_context({
        "btc_price": mkt_data["btc_price"],
        "rsi_1h": mkt_data.get("rsi_1h", 0),
        "macd_signal": mkt_data.get("macd_signal", "neutral"),
        "fear_greed_index": mkt_data.get("fear_greed_index", 0),
        "top_news_headlines": mkt_data.get("top_news_headlines", ""),
        "news_sentiment": mkt_data.get("news_sentiment", "neutral"),
    })

    # Step 2: Get account state
    logger.info("Step 2: Getting account balance...")
    balance = executor.get_account_balance()
    open_positions = memory.get_open_trades()

    # Step 3: Ask Claude for a decision
    logger.info("Step 3: Asking Claude for trading decision...")
    decision = brain.get_trading_decision(mkt_data, balance, open_positions)

    if decision is None:
        logger.warning("Claude did not return a decision — defaulting to HOLD")
        decision = {
            "action": "HOLD",
            "confidence": 0,
            "reasoning": "Claude API unavailable — automatic HOLD",
            "suggested_stop_loss_percent": 5,
            "market_regime": "unknown",
            "news_impact": "neutral",
            "risk_level": "high",
        }

    # Step 4 & 5: Execute and log
    logger.info(
        f"Step 4: Executing decision — {decision['action']} "
        f"(confidence: {decision.get('confidence', 0)}/10)"
    )
    executor.execute_decision(decision, mkt_data)

    logger.info("Trade cycle complete")
    logger.info("-" * 60)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Initialize the bot and start the scheduler."""
    setup_logging()

    logger.info("=" * 60)
    logger.info("  SELF-LEARNING CRYPTO TRADING BOT")
    logger.info(f"  Symbol: {config.SYMBOL}")
    logger.info(f"  Model: {config.CLAUDE_MODEL}")
    logger.info(f"  Initial Capital: ${config.INITIAL_CAPITAL}")
    logger.info(f"  Max Position: {config.MAX_POSITION_PERCENT * 100:.0f}%")
    logger.info(f"  Min Confidence: {config.MIN_CONFIDENCE_TO_TRADE}/10")
    logger.info("=" * 60)

    # Validate configuration
    if not validate_config():
        sys.exit(1)

    # Notify startup via Telegram
    notifier.notify_startup()

    # Run one cycle immediately on startup
    logger.info("Running initial trade cycle on startup...")
    trade_cycle()

    # Set up scheduler
    scheduler = BlockingScheduler()

    # Trade cycle: every 4 hours at 00:00, 04:00, 08:00, 12:00, 16:00, 20:00
    hours_str = ",".join(str(h) for h in config.TRADE_CYCLE_HOURS)
    scheduler.add_job(
        trade_cycle,
        CronTrigger(hour=hours_str, minute="0"),
        id="trade_cycle",
        name="Trade Cycle (every 4h)",
        misfire_grace_time=300,
    )

    # Nightly review: daily at 23:30 UTC
    scheduler.add_job(
        reviewer.run_nightly_review,
        CronTrigger(hour=config.REVIEW_HOUR, minute=config.REVIEW_MINUTE),
        id="nightly_review",
        name="Nightly Self-Review",
        misfire_grace_time=300,
    )

    logger.info(
        f"Scheduler started — trade cycle at {hours_str}:00 UTC, "
        f"review at {config.REVIEW_HOUR}:{config.REVIEW_MINUTE:02d} UTC"
    )
    logger.info("Press Ctrl+C to stop the bot.\n")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        notifier.notify_error(f"Bot crashed: {e}")


if __name__ == "__main__":
    main()
