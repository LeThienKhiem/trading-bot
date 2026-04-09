"""
Nightly self-review module for the trading bot (Quant Edition).
Runs at 23:30 UTC. Analyzes performance with detailed metrics,
asks Claude for data-driven review, saves lessons to Supabase.
"""

import logging
from datetime import date

from bot import memory, brain, notifier
from bot.market_data import get_current_price, get_fear_greed_index, analyze_timeframe
from binance.client import Client as BinanceClient

logger = logging.getLogger(__name__)


def calculate_performance_metrics(trades: list[dict]) -> dict:
    """Calculate detailed performance metrics from today's trades."""
    buy_trades = [t for t in trades if t.get("action") == "BUY" and t.get("pnl_usdt") is not None]
    sell_trades = [t for t in trades if t.get("action") == "SELL" and t.get("pnl_usdt") is not None]
    all_closed = buy_trades + sell_trades

    wins = [t for t in all_closed if (t.get("pnl_usdt") or 0) > 0]
    losses = [t for t in all_closed if (t.get("pnl_usdt") or 0) < 0]

    total_pnl = sum(t.get("pnl_usdt", 0) or 0 for t in all_closed)
    avg_win = sum(t["pnl_usdt"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_usdt"] for t in losses) / len(losses) if losses else 0
    win_rate = len(wins) / len(all_closed) if all_closed else 0

    # Risk/reward achieved
    rr_achieved = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    # Largest win/loss
    largest_win = max((t.get("pnl_usdt", 0) for t in all_closed), default=0)
    largest_loss = min((t.get("pnl_usdt", 0) for t in all_closed), default=0)

    return {
        "total_trades": len(trades),
        "executed_trades": len(all_closed),
        "wins": len(wins),
        "losses": len(losses),
        "holds": sum(1 for t in trades if t.get("action") == "HOLD"),
        "blocked": sum(1 for t in trades if t.get("action") == "BLOCKED"),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_rate": round(win_rate, 3),
        "risk_reward_achieved": round(rr_achieved, 2),
        "largest_win": round(largest_win, 2),
        "largest_loss": round(largest_loss, 2),
    }


def run_nightly_review() -> None:
    """Execute the full nightly review cycle."""
    logger.info("=" * 60)
    logger.info("NIGHTLY REVIEW — Starting daily self-evaluation")
    logger.info("=" * 60)

    # 1. Gather data
    today_trades = memory.get_today_trades()
    recent_lessons = memory.get_recent_lessons(limit=14)
    metrics = calculate_performance_metrics(today_trades)

    logger.info(
        f"Today: {metrics['total_trades']} decisions, {metrics['wins']} wins, "
        f"{metrics['losses']} losses, PnL: ${metrics['total_pnl']:+.2f}, "
        f"Win rate: {metrics['win_rate']:.0%}, R:R: {metrics['risk_reward_achieved']:.1f}"
    )

    # 2. Build market summary with current technical data
    price = get_current_price()
    fear_greed = get_fear_greed_index()

    # Get end-of-day technical snapshot
    tf_4h = analyze_timeframe(BinanceClient.KLINE_INTERVAL_4HOUR, "4H")
    ema_trend = tf_4h.get("emas", {}).get("trend", "N/A") if "error" not in tf_4h else "N/A"
    rsi_4h = tf_4h.get("rsi", "N/A") if "error" not in tf_4h else "N/A"

    market_summary = (
        f"BTC EOD price: ${price:,.2f}\n"
        f"Fear & Greed: {fear_greed}/100\n"
        f"4H EMA trend: {ema_trend}\n"
        f"4H RSI: {rsi_4h}\n"
        f"\nPerformance metrics:\n"
        f"  Win rate: {metrics['win_rate']:.0%}\n"
        f"  Risk/Reward achieved: {metrics['risk_reward_achieved']:.1f}\n"
        f"  Avg win: ${metrics['avg_win']:+.2f} | Avg loss: ${metrics['avg_loss']:+.2f}\n"
        f"  Largest win: ${metrics['largest_win']:+.2f} | Largest loss: ${metrics['largest_loss']:+.2f}"
    ) if price else "Market data unavailable."

    # 3. Get Claude review
    review = brain.get_daily_review(today_trades, recent_lessons, market_summary)

    if review is None:
        logger.warning("Claude review unavailable")
        review = {
            "lesson_text": "Review unavailable — Claude API did not respond.",
            "pattern_identified": None,
            "rule_for_tomorrow": None,
            "strategy_confidence": 0,
            "market_regime_today": "unknown",
            "suggested_adjustments": {},
        }

    # 4. Save lesson to Supabase
    lesson_data = {
        "date": str(date.today()),
        "total_trades": metrics["total_trades"],
        "wins": metrics["wins"],
        "losses": metrics["losses"],
        "lesson_text": review.get("lesson_text", ""),
        "pattern_identified": review.get("pattern_identified"),
        "adjustment_made": review.get("rule_for_tomorrow"),
    }

    saved = memory.save_daily_lesson(lesson_data)

    if saved:
        logger.info("Daily lesson saved")

        # 5. Send Telegram report with metrics
        notifier.notify_daily_report(lesson_data, review, metrics)
    else:
        logger.error("Failed to save daily lesson")
        notifier.notify_error("Failed to save daily lesson")

    logger.info("NIGHTLY REVIEW — Complete")
