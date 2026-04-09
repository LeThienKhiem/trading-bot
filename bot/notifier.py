"""
Telegram notification module for the trading bot (Quant Edition).
Sends trade alerts, daily reports, and error notifications.
"""

import logging
from datetime import datetime
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to the configured Telegram chat."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured")
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
        logger.error(f"Telegram send failed: {e}")
        return False


def notify_trade(
    decision: dict,
    market_data: dict,
    balance: dict,
    daily_pnl: Optional[dict] = None,
) -> None:
    """Send trade notification with technical data."""
    action = decision.get("action", "HOLD")
    confidence = decision.get("confidence", 0)
    reasoning = decision.get("reasoning", "N/A")
    price = market_data.get("btc_price", 0)
    setup = decision.get("setup_quality", "N/A")
    rr = decision.get("risk_reward_ratio", "N/A")
    regime = decision.get("market_regime", "N/A")
    risk = decision.get("risk_level", "N/A")

    # Get multi-timeframe data
    tf = market_data.get("timeframes", {})
    rsi_1h = tf.get("1h", {}).get("rsi", "N/A")
    rsi_4h = tf.get("4h", {}).get("rsi", "N/A")
    ema_trend_4h = tf.get("4h", {}).get("emas", {}).get("trend", "N/A")
    macd_1h = tf.get("1h", {}).get("macd", {}).get("signal", "N/A")
    bb_pct_b = tf.get("1h", {}).get("bollinger", {}).get("percent_b", "N/A")

    emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⏸️", "BLOCKED": "🚫"}.get(action, "❓")

    # Stop loss / take profit
    sl = decision.get("stop_loss_price", 0)
    tp = decision.get("take_profit_price", 0)
    sl_str = f"${sl:,.2f}" if sl else "N/A"
    tp_str = f"${tp:,.2f}" if tp else "N/A"

    # PnL
    if daily_pnl:
        pnl_line = (
            f"Today P&L: ${daily_pnl.get('pnl_usdt', 0):+.2f} "
            f"({daily_pnl.get('pnl_percent', 0):+.2f}%)"
        )
    else:
        pnl_line = "Today P&L: N/A"

    text = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>{emoji} {action}</b> | Setup: {setup}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 BTC: <b>${price:,.2f}</b>\n"
        f"📊 Confidence: {confidence}/10 | R:R: {rr}\n"
        f"📈 RSI 1H: {rsi_1h} | 4H: {rsi_4h}\n"
        f"📉 MACD 1H: {macd_1h} | BB %B: {bb_pct_b}\n"
        f"🔀 4H Trend: {ema_trend_4h} | Regime: {regime}\n"
        f"🎯 SL: {sl_str} | TP: {tp_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>{reasoning[:300]}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 USDT: ${balance.get('usdt', 0):,.2f}\n"
        f"₿ Bot BTC: {balance.get('btc_bot', 0):.8f}\n"
        f"💵 Portfolio: ${balance.get('total_usdt', 0):,.2f}\n"
        f"{pnl_line}"
    )
    send_message(text)


def notify_daily_report(lesson_data: dict, review: dict, metrics: dict) -> None:
    """Send end-of-day report with performance metrics."""
    lesson_text = review.get("lesson_text", "N/A")
    rule = review.get("rule_for_tomorrow", "None")
    confidence = review.get("strategy_confidence", "N/A")
    regime = review.get("market_regime_today", "N/A")
    best_ind = review.get("best_indicator_today", "N/A")
    worst_ind = review.get("worst_indicator_today", "N/A")

    adjustments = review.get("suggested_adjustments", {})
    adj_str = ""
    if adjustments:
        adj_str = (
            f"Position sizing: {adjustments.get('position_sizing', 'N/A')}\n"
            f"Confidence threshold: {adjustments.get('confidence_threshold', 'N/A')}\n"
            f"Timeframe weight: {adjustments.get('timeframe_weight', 'N/A')}"
        )

    win_rate = f"{metrics.get('win_rate', 0):.0%}"
    rr = metrics.get("risk_reward_achieved", 0)

    text = (
        f"📋 <b>DAILY REVIEW</b> — {datetime.utcnow().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Trades: {metrics.get('executed_trades', 0)} | "
        f"Win: {metrics.get('wins', 0)} | Loss: {metrics.get('losses', 0)}\n"
        f"🎯 Win Rate: {win_rate} | R:R: {rr:.1f}\n"
        f"💰 P&L: ${metrics.get('total_pnl', 0):+.2f}\n"
        f"📈 Market: {regime}\n"
        f"🧠 Strategy Confidence: {confidence}/10\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 <b>Lesson:</b>\n<i>{lesson_text[:500]}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Best indicator: {best_ind}\n"
        f"❌ Worst indicator: {worst_ind}\n"
        f"📌 Rule for tomorrow: {rule}\n"
    )
    if adj_str:
        text += f"━━━━━━━━━━━━━━━━━━━━\n🔧 <b>Adjustments:</b>\n{adj_str}"

    send_message(text)


def notify_error(error_msg: str) -> None:
    """Send an error/warning alert."""
    text = (
        f"🚨 <b>ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{error_msg}"
    )
    send_message(text)


def notify_startup() -> None:
    """Send startup notification."""
    mode = "🔬 DRY RUN" if config.DRY_RUN else "🟢 LIVE"
    text = (
        f"🤖 <b>Trading Bot Started</b> [{mode}]\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Strategy: Quant Multi-Timeframe\n"
        f"Symbol: {config.SYMBOL}\n"
        f"Model: {config.CLAUDE_MODEL}\n"
        f"Position: {config.MIN_POSITION_PERCENT*100:.0f}-{config.MAX_POSITION_PERCENT*100:.0f}%\n"
        f"Min Confidence: {config.MIN_CONFIDENCE_TO_TRADE}/10\n"
        f"Min R:R: {config.MIN_RISK_REWARD_RATIO}\n"
        f"Safety Stop: ${config.STOP_LOSS_MINIMUM_BALANCE}\n"
        f"Price Monitor: every {config.PRICE_CHECK_INTERVAL}s\n"
        f"Trailing Stop: activates at +{config.TRAILING_STOP_ACTIVATION_PCT}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ Strategy cycle: every {config.TRADE_CYCLE_HOURS[1] - config.TRADE_CYCLE_HOURS[0]}h\n"
        f"📋 Review: daily 23:30 UTC"
    )
    send_message(text)
