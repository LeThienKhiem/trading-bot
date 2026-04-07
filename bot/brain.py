"""
Claude API brain for the trading bot.
Builds context from market data, trade history, and lessons learned,
then asks Claude to make a BUY/SELL/HOLD decision. Also provides the
review function for nightly self-evaluation.
"""

import json
import logging
from typing import Optional

import anthropic

import config
from bot import memory

logger = logging.getLogger(__name__)

# ── Claude Client ────────────────────────────────────────────────────────────

_client: Optional[anthropic.Anthropic] = None


def get_client() -> anthropic.Anthropic:
    """Return a singleton Anthropic client."""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ── System Prompt ────────────────────────────────────────────────────────────

TRADING_SYSTEM_PROMPT = """You are an expert cryptocurrency trader managing a BTC/USDT portfolio on Binance Spot.

Your job:
1. Analyze all provided market data holistically (price, RSI, MACD, volume, Fear & Greed, news).
2. Review past trade history and lessons learned to avoid repeating mistakes.
3. Consider news impact on short-term price movement.
4. Make a clear BUY, SELL, or HOLD decision.

Rules:
- Be conservative. Protect capital first, grow second.
- Only suggest BUY when multiple indicators align (e.g., oversold RSI + bullish MACD + positive news).
- Suggest SELL to lock in profits or cut losses when trend reverses.
- Default to HOLD when signals are mixed or uncertain.
- Always explain your reasoning clearly, referencing specific data points.
- If news indicates a major event (hack, regulation, black swan), lean toward HOLD regardless.

You MUST respond with ONLY valid JSON in this exact format, no markdown, no extra text:
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 1-10,
  "reasoning": "detailed explanation referencing specific data points",
  "suggested_stop_loss_percent": 3-7,
  "market_regime": "trending_up" | "trending_down" | "sideways" | "volatile",
  "news_impact": "positive" | "negative" | "neutral" | "high_alert",
  "risk_level": "low" | "medium" | "high"
}"""


# ── Decision Making ──────────────────────────────────────────────────────────

def build_context(
    market_data: dict,
    account_balance: dict,
    open_positions: list[dict],
) -> str:
    """
    Build a comprehensive context string for Claude from all available data.

    Args:
        market_data: Dict from market_data.fetch_all_market_data().
        account_balance: Dict with 'usdt' and 'btc' balances.
        open_positions: List of open trade dicts from Supabase.

    Returns:
        Formatted context string for the Claude prompt.
    """
    # Fetch historical data from Supabase
    recent_trades = memory.get_recent_trades(limit=30)
    recent_lessons = memory.get_recent_lessons(limit=14)
    win_rate = memory.calculate_win_rate(recent_trades)

    # Format recent trades summary
    trades_summary = ""
    for t in recent_trades[:10]:  # Show last 10 in detail
        pnl = t.get("pnl_usdt")
        pnl_str = f"PnL: ${pnl:.2f}" if pnl is not None else "PnL: pending"
        trades_summary += (
            f"  - {t['action']} @ ${t.get('price_at_decision', 0):,.2f} | "
            f"{pnl_str} | Reason: {t.get('reasoning', 'N/A')[:80]}\n"
        )

    # Format lessons summary
    lessons_summary = ""
    for lesson in recent_lessons[:5]:  # Show last 5 lessons
        lessons_summary += (
            f"  [{lesson.get('date')}] {lesson.get('lesson_text', 'N/A')[:150]}\n"
        )

    # Format open positions
    positions_str = "None"
    if open_positions:
        positions_str = "\n".join(
            f"  - {p['action']} @ ${p.get('price_at_decision', 0):,.2f} "
            f"(stop loss: ${p.get('suggested_stop_loss', 'N/A')})"
            for p in open_positions
        )

    context = f"""=== CURRENT MARKET DATA ===
BTC/USDT Price: ${market_data['btc_price']:,.2f}
RSI (14, 1h): {market_data.get('rsi_1h', 'N/A')}
MACD Signal: {market_data.get('macd_signal', 'N/A')}
24h Price Change: {market_data.get('volume_change_24h', 'N/A')}%
Fear & Greed Index: {market_data.get('fear_greed_index', 'N/A')}/100

=== NEWS ===
{market_data.get('top_news_headlines', 'No news available')}
News Sentiment: {market_data.get('news_sentiment', 'neutral')}

=== ACCOUNT ===
USDT Balance: ${account_balance.get('usdt', 0):,.2f}
BTC Balance: {account_balance.get('btc', 0):.8f}
Total Portfolio Value: ~${account_balance.get('total_usdt', 0):,.2f}

=== OPEN POSITIONS ===
{positions_str}

=== TRADE HISTORY (Last 30 trades) ===
Win Rate: {win_rate:.1%} ({len(recent_trades)} trades)
Recent trades:
{trades_summary or '  No trades yet'}

=== LESSONS LEARNED ===
{lessons_summary or '  No lessons yet — this is the beginning of the journey.'}
"""
    return context


def get_trading_decision(
    market_data: dict,
    account_balance: dict,
    open_positions: list[dict],
) -> Optional[dict]:
    """
    Ask Claude to make a trading decision based on all available context.

    Args:
        market_data: Dict from market_data.fetch_all_market_data().
        account_balance: Dict with 'usdt', 'btc', 'total_usdt' keys.
        open_positions: List of open trade dicts.

    Returns:
        Parsed JSON decision dict with keys: action, confidence, reasoning,
        suggested_stop_loss_percent, market_regime, news_impact, risk_level.
        Returns None if Claude API fails (defaults to HOLD in caller).
    """
    context = build_context(market_data, account_balance, open_positions)

    try:
        response = get_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            system=TRADING_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": f"Analyze and decide:\n\n{context}"}
            ],
            timeout=config.CLAUDE_TIMEOUT,
        )

        raw_text = response.content[0].text.strip()
        logger.info(f"Claude raw response: {raw_text[:200]}")

        # Parse JSON — strip markdown code fences if Claude adds them
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            raw_text = raw_text.rsplit("```", 1)[0]

        decision = json.loads(raw_text)

        # Validate required fields
        required = ["action", "confidence", "reasoning"]
        for field in required:
            if field not in decision:
                logger.error(f"Missing field in Claude response: {field}")
                return None

        # Normalize action to uppercase
        decision["action"] = decision["action"].upper()
        if decision["action"] not in ("BUY", "SELL", "HOLD"):
            logger.error(f"Invalid action from Claude: {decision['action']}")
            return None

        logger.info(
            f"Claude decision: {decision['action']} "
            f"(confidence: {decision['confidence']}/10)"
        )
        return decision

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response as JSON: {e}")
        return None
    except anthropic.APITimeoutError:
        logger.warning("Claude API timed out — defaulting to HOLD")
        return None
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return None


# ── Nightly Review ───────────────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """You are a trading coach reviewing a crypto trading bot's daily performance.

Your job is to write a thoughtful daily reflection that will help the bot improve tomorrow.

Analyze the trades, market conditions, wins, and losses. Be specific about:
1. What worked and WHY (reference specific indicators that aligned)
2. What failed and WHY (what signal was wrong or ignored)
3. Any pattern you notice across recent days
4. One specific, actionable adjustment for tomorrow

You MUST respond with ONLY valid JSON in this exact format:
{
  "lesson_text": "2-3 paragraph reflection on today's performance",
  "pattern_identified": "specific pattern noticed, or null if none",
  "adjustment_made": "specific change to apply tomorrow",
  "strategy_confidence": 1-10
}"""


def get_daily_review(
    today_trades: list[dict],
    recent_lessons: list[dict],
    market_summary: str,
) -> Optional[dict]:
    """
    Ask Claude to review today's performance and write a daily lesson.

    Args:
        today_trades: List of all trades from today.
        recent_lessons: Recent daily lessons for context.
        market_summary: Brief summary of today's market conditions.

    Returns:
        Parsed JSON review dict, or None on failure.
    """
    # Calculate stats
    total = len(today_trades)
    wins = sum(1 for t in today_trades if (t.get("pnl_usdt") or 0) > 0)
    losses = sum(1 for t in today_trades if (t.get("pnl_usdt") or 0) < 0)
    holds = sum(1 for t in today_trades if t.get("action") == "HOLD")

    trades_detail = ""
    for t in today_trades:
        pnl = t.get("pnl_usdt")
        pnl_str = f"${pnl:.2f}" if pnl is not None else "N/A"
        trades_detail += (
            f"  - {t['action']} @ ${t.get('price_at_decision', 0):,.2f} | "
            f"PnL: {pnl_str} | Confidence: {t.get('confidence')}/10 | "
            f"Reason: {t.get('reasoning', 'N/A')[:100]}\n"
        )

    lessons_context = ""
    for lesson in recent_lessons[:7]:
        lessons_context += (
            f"  [{lesson.get('date')}] {lesson.get('lesson_text', '')[:120]}\n"
        )

    review_prompt = f"""=== TODAY'S PERFORMANCE ===
Total decisions: {total}
Wins: {wins} | Losses: {losses} | Holds: {holds}

=== TODAY'S TRADES ===
{trades_detail or '  No trades executed today (all HOLDs or no cycles ran).'}

=== MARKET CONDITIONS TODAY ===
{market_summary}

=== RECENT LESSONS (for context) ===
{lessons_context or '  No previous lessons yet.'}

Please write today's daily review."""

    try:
        response = get_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            system=REVIEW_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": review_prompt}
            ],
            timeout=config.CLAUDE_TIMEOUT,
        )

        raw_text = response.content[0].text.strip()

        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            raw_text = raw_text.rsplit("```", 1)[0]

        review = json.loads(raw_text)
        logger.info("Daily review received from Claude")
        return review

    except Exception as e:
        logger.error(f"Failed to get daily review from Claude: {e}")
        return None
