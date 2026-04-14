"""
Entry point for the self-learning crypto trading bot (Quant Edition).
Multi-timeframe technical analysis + realtime price monitoring.

Usage:
  python main.py           # Start bot in live mode
  python main.py --verify  # Run one dry-run cycle then exit
"""

import argparse
import logging
import sys
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from bot import market_data, brain, executor, memory, reviewer, notifier
from bot.price_monitor import price_monitor

# ── Logging ──────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """Configure logging to console and file."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        ],
    )

logger = logging.getLogger(__name__)


# ── Config Validation ────────────────────────────────────────────────────────

def validate_config() -> bool:
    """Check required environment variables."""
    required = {
        "BINANCE_API_KEY": config.BINANCE_API_KEY,
        "BINANCE_API_SECRET": config.BINANCE_API_SECRET,
        "ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY,
        "SUPABASE_URL": config.SUPABASE_URL,
        "SUPABASE_ANON_KEY": config.SUPABASE_ANON_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.error(f"Missing config: {', '.join(missing)}")
        return False
    return True


# ── Trade Cycle ──────────────────────────────────────────────────────────────

def trade_cycle() -> None:
    """
    Run one full trade cycle:
    1. Fetch multi-timeframe market data
    2. Get account balance and open positions
    3. Calculate daily PnL
    4. Ask Claude for a decision (with full technical context)
    5. Execute with dynamic position sizing
    6. Set price monitor targets
    """
    mode = "[DRY RUN] " if config.DRY_RUN else ""
    logger.info("=" * 60)
    logger.info(f"{mode}TRADE CYCLE — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    # Step 0: Reconcile positions (fix ghost positions)
    executor.reconcile_positions()

    # Step 1: Market data
    logger.info("Fetching multi-timeframe market data...")
    mkt_data = market_data.fetch_all_market_data()
    if mkt_data is None:
        logger.error("Failed to fetch market data — skipping")
        notifier.notify_error("Market data fetch failed — cycle skipped")
        return

    # Save market context
    memory.save_market_context({
        "btc_price": mkt_data["btc_price"],
        "rsi_1h": mkt_data.get("rsi_1h", 0),
        "macd_signal": mkt_data.get("macd_signal", "neutral"),
        "fear_greed_index": mkt_data.get("fear_greed_index", 0),
        "top_news_headlines": "",
        "news_sentiment": "neutral",
    })

    # Step 2: Account state
    logger.info("Getting account balance...")
    balance = executor.get_account_balance()
    open_positions = memory.get_open_trades()

    # Step 3: Daily PnL
    daily_pnl = memory.calculate_daily_pnl(balance["total_usdt"])
    logger.info(
        f"Daily PnL: ${daily_pnl['pnl_usdt']:+.2f} ({daily_pnl['pnl_percent']:+.2f}%)"
    )

    # Step 4: Claude decision
    logger.info("Asking Claude for trading decision...")
    decision = brain.get_trading_decision(mkt_data, balance, open_positions, daily_pnl)

    if decision is None:
        logger.warning("Claude unavailable — defaulting to HOLD")
        decision = {
            "action": "HOLD",
            "confidence": 0,
            "reasoning": "Claude API unavailable — automatic HOLD",
            "suggested_stop_loss_percent": 5,
            "market_regime": "unknown",
            "risk_level": "high",
            "setup_quality": "no_setup",
        }

    # Step 5 & 6: Execute
    logger.info(
        f"Decision: {decision['action']} "
        f"(confidence: {decision.get('confidence', 0)}/10, "
        f"setup: {decision.get('setup_quality', 'N/A')})"
    )
    executor.execute_decision(decision, mkt_data)

    logger.info("Trade cycle complete")
    logger.info("-" * 60)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Initialize the bot and start the scheduler."""
    parser = argparse.ArgumentParser(description="Quant Crypto Trading Bot")
    parser.add_argument("--verify", action="store_true", help="Dry-run one cycle then exit")
    args = parser.parse_args()

    if args.verify:
        config.DRY_RUN = True

    setup_logging()

    mode_str = "DRY RUN" if config.DRY_RUN else "LIVE"
    logger.info("=" * 60)
    logger.info(f"  QUANT TRADING BOT [{mode_str}]")
    logger.info(f"  Symbol: {config.SYMBOL}")
    logger.info(f"  Model: {config.CLAUDE_MODEL}")
    logger.info(f"  Position: {config.MIN_POSITION_PERCENT*100:.0f}-{config.MAX_POSITION_PERCENT*100:.0f}%")
    logger.info(f"  Min R:R: {config.MIN_RISK_REWARD_RATIO}")
    logger.info(f"  Price monitor: every {config.PRICE_CHECK_INTERVAL}s")
    logger.info(f"  Trailing stop: +{config.TRAILING_STOP_ACTIVATION_PCT}% activation")
    logger.info("=" * 60)

    if not validate_config():
        sys.exit(1)

    # Startup notification
    notifier.notify_startup()

    # Report server IP for Binance whitelist
    import requests as _req
    try:
        my_ip = _req.get("https://api.ipify.org", timeout=5).text
        notifier.send_message(
            f"🌐 <b>Server IP:</b> <code>{my_ip}</code>\n"
            f"⏳ Waiting up to 3 min for Binance API..."
        )
        logger.info(f"Server IP: {my_ip}")
    except Exception:
        pass

    # Wait for Binance API access
    from bot.market_data import get_binance_client
    api_ready = False
    for attempt in range(18):  # 3 minutes
        try:
            client = get_binance_client()
            client.get_account()
            api_ready = True
            notifier.send_message("✅ Binance API connected!")
            logger.info("Binance API accessible")
            break
        except Exception:
            if attempt == 0:
                logger.info("Waiting for Binance API access...")
            time.sleep(10)

    if not api_ready:
        notifier.send_message("⚠️ Binance API not accessible after 3 min. Starting anyway.")

    # Dry-run mode
    if config.DRY_RUN:
        logger.info("Running verification cycle...")
        trade_cycle()
        logger.info("Verification complete.")
        return

    # Start price monitor (background thread)
    price_monitor.start()
    logger.info("Price monitor started")

    # Check if we have open positions to monitor
    open_positions = memory.get_open_trades()
    if open_positions:
        pos = open_positions[0]
        entry = pos.get("price_at_decision", 0)
        sl = pos.get("suggested_stop_loss", entry * 0.95)
        tp = entry * 1.03  # default TP
        qty = pos.get("quantity_usdt", 0)
        price_monitor.update_position(entry, sl, tp, qty)
        logger.info(f"Resumed monitoring position: entry=${entry:,.2f}")

    # Run initial cycle
    logger.info("Running initial trade cycle...")
    trade_cycle()

    # Set up scheduler
    scheduler = BlockingScheduler()

    hours_str = ",".join(str(h) for h in config.TRADE_CYCLE_HOURS)
    scheduler.add_job(
        trade_cycle,
        CronTrigger(hour=hours_str, minute="0"),
        id="trade_cycle",
        name=f"Trade Cycle (every {config.TRADE_CYCLE_HOURS[1] - config.TRADE_CYCLE_HOURS[0]}h)",
        misfire_grace_time=300,
    )

    scheduler.add_job(
        reviewer.run_nightly_review,
        CronTrigger(hour=config.REVIEW_HOUR, minute=config.REVIEW_MINUTE),
        id="nightly_review",
        name="Nightly Self-Review",
        misfire_grace_time=300,
    )

    logger.info(f"Scheduler: trades at {hours_str}:00 UTC, review at 23:30 UTC")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
        price_monitor.stop()
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        notifier.notify_error(f"Bot crashed: {e}")
        price_monitor.stop()


if __name__ == "__main__":
    main()
