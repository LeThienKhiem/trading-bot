"""
Entry point for the self-learning crypto trading bot.
Sets up logging, validates configuration, and starts the APScheduler
with two jobs:
  1. Trade cycle — every 4 hours (00:00, 04:00, ..., 20:00 UTC)
  2. Nightly review — daily at 23:30 UTC

Usage:
  python main.py           # Start bot in live mode
  python main.py --verify  # Run one dry-run cycle (no real trades)
"""

import argparse
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
    3. Calculate daily PnL
    4. Ask Claude for a trading decision (with PnL context)
    5. Run safety checks and execute if appropriate
    6. Save everything to Supabase
    """
    mode = "[DRY RUN] " if config.DRY_RUN else ""
    logger.info("=" * 60)
    logger.info(f"{mode}TRADE CYCLE — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    # Step 1: Fetch market data
    logger.info("Step 1: Fetching market data...")
    mkt_data = market_data.fetch_all_market_data()
    if mkt_data is None:
        logger.error("Failed to fetch market data — skipping this cycle")
        notifier.notify_error("Failed to fetch market data — cycle skipped")
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

    # Step 3: Calculate daily PnL
    daily_pnl = memory.calculate_daily_pnl(balance["total_usdt"])
    logger.info(
        f"Daily PnL: ${daily_pnl['pnl_usdt']:+.2f} ({daily_pnl['pnl_percent']:+.2f}%) "
        f"| Target reached: {daily_pnl['target_reached']}"
    )

    # Step 4: Ask Claude for a decision
    logger.info("Step 3: Asking Claude for trading decision...")
    decision = brain.get_trading_decision(mkt_data, balance, open_positions, daily_pnl)

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
            "today_pnl_consideration": "N/A — API failure",
        }

    # Step 5 & 6: Execute and log
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
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Self-Learning Crypto Trading Bot")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run one dry-run cycle (no real trades) then exit",
    )
    args = parser.parse_args()

    # Set dry-run mode if --verify flag is used
    if args.verify:
        config.DRY_RUN = True

    setup_logging()

    mode_str = "DRY RUN (--verify)" if config.DRY_RUN else "LIVE"
    logger.info("=" * 60)
    logger.info(f"  SELF-LEARNING CRYPTO TRADING BOT [{mode_str}]")
    logger.info(f"  Symbol: {config.SYMBOL}")
    logger.info(f"  Model: {config.CLAUDE_MODEL}")
    logger.info(f"  Initial Capital: ${config.INITIAL_CAPITAL}")
    logger.info(f"  Max Position: {config.MAX_POSITION_PERCENT * 100:.0f}%")
    logger.info(f"  Min Confidence: {config.MIN_CONFIDENCE_TO_TRADE}/10")
    logger.info(f"  Daily Target: +{config.DAILY_TARGET_PERCENT}%")
    logger.info(f"  Safety Stop: ${config.STOP_LOSS_MINIMUM_BALANCE}")
    logger.info("=" * 60)

    # Validate configuration
    if not validate_config():
        sys.exit(1)

    # Notify startup via Telegram
    notifier.notify_startup()

    # Quick balance diagnostic on startup
    logger.info("Running startup balance check...")
    try:
        from bot.market_data import get_binance_client
        client = get_binance_client()
        account = client.get_account()
        acc_type = account.get("accountType", "?")
        can_trade = account.get("canTrade", "?")
        non_zero = []
        for b in account.get("balances", []):
            f_val = float(b["free"])
            l_val = float(b["locked"])
            if f_val > 0 or l_val > 0:
                non_zero.append(f"{b['asset']}: free={f_val}, locked={l_val}")
        diag = (
            f"🔍 <b>STARTUP BALANCE CHECK</b>\n"
            f"Account type: {acc_type}\n"
            f"canTrade: {can_trade}\n"
            f"Non-zero assets ({len(non_zero)}):\n"
        )
        diag += "\n".join(non_zero[:15]) if non_zero else "⚠️ ALL ZERO — check API key permissions"
        notifier.send_message(diag)
        logger.info(f"Balance check: {len(non_zero)} non-zero assets, type={acc_type}")
    except Exception as e:
        err_msg = f"🚨 <b>BALANCE CHECK FAILED</b>\n<code>{type(e).__name__}: {str(e)[:500]}</code>"
        notifier.send_message(err_msg)
        logger.error(f"Balance check failed: {e}", exc_info=True)

    # If --verify, run one cycle and exit
    if config.DRY_RUN:
        logger.info("Running verification cycle (dry run)...")
        trade_cycle()
        logger.info("Verification complete. No real trades were executed.")
        logger.info("Review the output above and Telegram notification.")
        return

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
