"""
Nightly self-review module for the trading bot.
Runs at 23:30 UTC each day. Collects all trades, market context,
and previous lessons, then asks Claude to write a daily reflection.
The lesson is saved to Supabase for use in future trading decisions.
"""

import logging
from datetime import date

from bot import memory, brain, notifier
from bot.market_data import get_current_price, get_fear_greed_index

logger = logging.getLogger(__name__)


def run_nightly_review() -> None:
    """
    Execute the full nightly review cycle:
    1. Gather today's trades and stats
    2. Build a market summary for context
    3. Ask Claude to write a daily lesson
    4. Save the lesson to Supabase
    """
    logger.info("=" * 60)
    logger.info("NIGHTLY REVIEW — Starting daily self-evaluation")
    logger.info("=" * 60)

    # 1. Gather today's data
    today_trades = memory.get_today_trades()
    recent_lessons = memory.get_recent_lessons(limit=14)

    total = len(today_trades)
    wins = sum(1 for t in today_trades if (t.get("pnl_usdt") or 0) > 0)
    losses = sum(1 for t in today_trades if (t.get("pnl_usdt") or 0) < 0)

    logger.info(f"Today's stats: {total} decisions, {wins} wins, {losses} losses")

    # 2. Build market summary
    price = get_current_price()
    fear_greed = get_fear_greed_index()
    market_summary = (
        f"BTC end-of-day price: ${price:,.2f}\n"
        f"Fear & Greed Index: {fear_greed}/100"
        if price
        else "Market data unavailable for end-of-day summary."
    )

    # 3. Ask Claude for a daily review
    review = brain.get_daily_review(today_trades, recent_lessons, market_summary)

    if review is None:
        logger.warning("Claude failed to produce a daily review — skipping")
        # Save a minimal record so we don't lose the day's stats
        review = {
            "lesson_text": "Review unavailable — Claude API did not respond.",
            "pattern_identified": None,
            "adjustment_made": None,
            "strategy_confidence": 0,
        }

    # 4. Save lesson to Supabase
    lesson_data = {
        "date": str(date.today()),
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "lesson_text": review.get("lesson_text", ""),
        "pattern_identified": review.get("pattern_identified"),
        "adjustment_made": review.get("adjustment_made"),
    }

    saved = memory.save_daily_lesson(lesson_data)

    if saved:
        logger.info("Daily lesson saved successfully")
        logger.info(f"Lesson preview: {review.get('lesson_text', '')[:200]}")
        if review.get("pattern_identified"):
            logger.info(f"Pattern identified: {review['pattern_identified']}")
        if review.get("adjustment_made"):
            logger.info(f"Adjustment for tomorrow: {review['adjustment_made']}")

        # Send daily report to Telegram
        notifier.notify_daily_report(lesson_data, review, total, wins, losses)
    else:
        logger.error("Failed to save daily lesson to Supabase")
        notifier.notify_error("Failed to save daily lesson to Supabase")

    logger.info("NIGHTLY REVIEW — Complete")
