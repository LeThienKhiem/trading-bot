"""
Telegram notification module for the trading bot.
Sends real-time alerts for trades, daily reports, and errors.
Uses Telegram Bot API directly via requests (no async dependency).
All sends are wrapped in try/except — notification failure never crashes the bot.
"""

import logging
from datetime import datetime

import requests

import config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    Send a message to the configured Telegram chat.

    Args:
        text: Message text (supports HTML formatting).
        parse_mode: Telegram parse mode ('HTML' or 'Markdown').

    Returns:
        True if sent successfully, False otherwise.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — skipping notification")
        return False

    try:
        url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN)
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def notify_trade(decision: dict, market_data: dict, balance: dict) -> None:
    """
    Send a trade notification after each cycle decision.

    Args:
        decision: Claude's decision dict (action, confidence, reasoning, etc.).
        market_data: Current market data dict.
        balance: Current account balance dict.
    """
    action = decision.get("action", "HOLD")
    confidence = decision.get("confidence", 0)
    reasoning = decision.get("reasoning", "N/A")
    price = market_data.get("btc_price", 0)
    rsi = market_data.get("rsi_1h", "N/A")
    macd = market_data.get("macd_signal", "N/A")
    fear_greed = market_data.get("fear_greed_index", "N/A")
    risk = decision.get("risk_level", "N/A")
    regime = decision.get("market_regime", "N/A")

    # Emoji based on action
    emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⏸️"}.get(action, "❓")

    text = (
        f"{emoji} <b>{action}</b> | Confidence: {confidence}/10\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 BTC Price: <b>${price:,.2f}</b>\n"
        f"📊 RSI: {rsi} | MACD: {macd}\n"
        f"😱 Fear & Greed: {fear_greed}/100\n"
        f"📈 Regime: {regime} | Risk: {risk}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 USDT: ${balance.get('usdt', 0):,.2f}\n"
        f"₿ Bot BTC: {balance.get('btc_bot', 0):.8f}\n"
        f"💵 Bot Portfolio: ${balance.get('total_usdt', 0):,.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <i>{reasoning[:300]}</i>"
    )

    send_message(text)


def notify_daily_report(
    lesson_data: dict,
    review: dict,
    total: int,
    wins: int,
    losses: int,
) -> None:
    """
    Send end-of-day report after nightly review.

    Args:
        lesson_data: The saved lesson dict.
        review: Claude's review response dict.
        total: Total trades today.
        wins: Number of winning trades.
        losses: Number of losing trades.
    """
    lesson_text = review.get("lesson_text", "N/A")
    pattern = review.get("pattern_identified", "None")
    adjustment = review.get("adjustment_made", "None")
    confidence = review.get("strategy_confidence", "N/A")

    win_rate = f"{wins/total*100:.0f}%" if total > 0 else "N/A"

    text = (
        f"📋 <b>DAILY REPORT</b> — {datetime.utcnow().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Trades: {total} | Wins: {wins} | Losses: {losses}\n"
        f"🎯 Win Rate: {win_rate}\n"
        f"🧠 Strategy Confidence: {confidence}/10\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Lesson:</b>\n<i>{lesson_text[:500]}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Pattern: {pattern or 'None'}\n"
        f"🔧 Adjustment: {adjustment or 'None'}"
    )

    send_message(text)


def notify_error(error_msg: str) -> None:
    """
    Send an error/warning alert.

    Args:
        error_msg: Description of the error.
    """
    text = (
        f"🚨 <b>ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{error_msg}"
    )
    send_message(text)


def notify_startup() -> None:
    """Send a notification when the bot starts up."""
    text = (
        f"🤖 <b>Trading Bot Started</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Symbol: {config.SYMBOL}\n"
        f"Model: {config.CLAUDE_MODEL}\n"
        f"Max Position: {config.MAX_POSITION_PERCENT*100:.0f}%\n"
        f"Min Confidence: {config.MIN_CONFIDENCE_TO_TRADE}/10\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ Trade cycle: every 4h\n"
        f"📋 Review: daily 23:30 UTC"
    )
    send_message(text)
