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

TRADING_SYSTEM_PROMPT = """You are an expert crypto trading AI managing a $100 BTC/USDT spot portfolio.

PERSONALITY: Balanced — protect capital first, grow consistently second.
Do not chase pumps. Do not panic sell bottoms. Think in probabilities.

YOUR DAILY TARGET: +2% on total portfolio value.
- If already up >2% today: recommend HOLD to protect gains
- If down >5% today: be more conservative, preserve capital
- Normal conditions: seek high-confidence setups only

DECISION FRAMEWORK:
1. Check market regime first (trending/sideways/volatile)
2. In volatile/unclear market: default to HOLD
3. Only BUY when: RSI not overbought (<65), clear support level,
   positive/neutral news, confidence >= 7
4. Only SELL when: holding BTC position AND
   (target reached OR stop loss triggered OR trend reversal confirmed)
5. HOLD when: uncertain, conflicting signals, or news_impact = high_alert

LEARNING: You will receive your last 30 trades with outcomes and
14 daily lessons. Reference them explicitly in your reasoning.
Say things like: "Last 3 times RSI was above 70, price dropped —
avoiding BUY" or "Lesson from Day 5: don't trade during high volatility"

RISK AWARENESS:
- This is a real person's $100. Every loss matters.
- A confident wrong decision is worse than a cautious HOLD.
- If unsure: HOLD. There will always be another opportunity.

OUTPUT: Return ONLY valid JSON, no markdown, no explanation outside JSON:
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 1-10,
  "reasoning": "2-3 sentences explaining WHY, referencing past lessons if relevant",
  "suggested_stop_loss_percent": 3-7,
  "market_regime": "trending_up" | "trending_down" | "sideways" | "volatile",
  "news_impact": "positive" | "negative" | "neutral" | "high_alert",
  "risk_level": "low" | "medium" | "high",
  "today_pnl_consideration": "brief note on today's P&L and how it affects decision"
}"""


# ── Decision Making ──────────────────────────────────────────────────────────

def build_context(
    market_data: dict,
    account_balance: dict,
    open_positions: list[dict],
    daily_pnl: Optional[dict] = None,
) -> str:
    """
    Build a comprehensive context string for Claude from all available data.

    Args:
        market_data: Dict from market_data.fetch_all_market_data().
        account_balance: Dict with 'usdt' and 'btc' balances.
        open_positions: List of open trade dicts from Supabase.
        daily_pnl: Optional dict with today's PnL info.

    Returns:
        Formatted context string for the Claude prompt.
    """
    # Fetch historical data from Supabase
    recent_trades = memory.get_recent_trades(limit=30)
    recent_lessons = memory.get_recent_lessons(limit=14)
    win_rate = memory.calculate_win_rate(recent_trades)

    # Format recent trades summary
    trades_summary = ""
    for t in recent_trades[:10]:
        pnl = t.get("pnl_usdt")
        pnl_str = f"PnL: ${pnl:.2f}" if pnl is not None else "PnL: pending"
        trades_summary += (
            f"  - {t['action']} @ ${t.get('price_at_decision', 0):,.2f} | "
            f"{pnl_str} | Reason: {t.get('reasoning', 'N/A')[:80]}\n"
        )

    # Format lessons summary
    lessons_summary = ""
    for lesson in recent_lessons[:7]:
        lessons_summary += (
            f"  [{lesson.get('date')}] {lesson.get('lesson_text', 'N/A')[:200]}\n"
            f"    Rule: {lesson.get('adjustment_made', 'N/A')}\n"
        )

    # Format open positions
    positions_str = "None"
    if open_positions:
        positions_str = "\n".join(
            f"  - {p['action']} @ ${p.get('price_at_decision', 0):,.2f} "
            f"(stop loss: ${p.get('suggested_stop_loss', 'N/A')})"
            for p in open_positions
        )

    # Format daily PnL
    pnl_str = "No data yet (first cycle of the day)"
    if daily_pnl:
        pnl_str = (
            f"Today's PnL: ${daily_pnl.get('pnl_usdt', 0):+.2f} "
            f"({daily_pnl.get('pnl_percent', 0):+.2f}%)\n"
            f"Daily target (+{config.DAILY_TARGET_PERCENT}%): "
            f"{'REACHED — protect gains' if daily_pnl.get('target_reached') else 'not yet reached'}"
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
Bot BTC: {account_balance.get('btc_bot', 0):.8f}
Bot Portfolio Value: ~${account_balance.get('total_usdt', 0):,.2f}

=== TODAY'S P&L ===
{pnl_str}

=== OPEN POSITIONS ===
{positions_str}

=== TRADE HISTORY (Last 30 trades) ===
Win Rate: {win_rate:.1%} ({len(recent_trades)} trades)
Recent trades:
{trades_summary or '  No trades yet'}

=== LESSONS LEARNED (Last 14 days) ===
{lessons_summary or '  No lessons yet — this is the beginning of the journey.'}
"""
    return context


def get_trading_decision(
    market_data: dict,
    account_balance: dict,
    open_positions: list[dict],
    daily_pnl: Optional[dict] = None,
) -> Optional[dict]:
    """
    Ask Claude to make a trading decision based on all available context.

    Args:
        market_data: Dict from market_data.fetch_all_market_data().
        account_balance: Dict with 'usdt', 'btc', 'total_usdt' keys.
        open_positions: List of open trade dicts.
        daily_pnl: Optional dict with today's PnL info.

    Returns:
        Parsed JSON decision dict, or None if Claude API fails.
    """
    context = build_context(market_data, account_balance, open_positions, daily_pnl)

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

REVIEW_SYSTEM_PROMPT = """You are reviewing your own trading performance for today.
Be honest, critical, and specific. Vague lessons are useless.

REVIEW STRUCTURE:
1. Performance summary (wins/losses/P&L today)
2. For each LOSING trade: exactly what signal was wrong and why
3. For each WINNING trade: what worked and can it be repeated
4. Pattern check: compare today vs last 7 lessons — any recurring mistake?
5. Market regime assessment: what kind of market was today?
6. ONE specific rule to add/change for tomorrow
   (e.g. "Do not BUY when Fear & Greed > 75" or
    "RSI divergence on 4h is more reliable than 1h")
7. Confidence in current strategy: 1-10, with honest explanation

OUTPUT: Valid JSON only:
{
  "lesson_text": "full honest review (2-3 paragraphs)",
  "pattern_identified": "specific recurring pattern or null",
  "rule_for_tomorrow": "ONE specific actionable rule",
  "strategy_confidence": 1-10,
  "market_regime_today": "trending_up|trending_down|sideways|volatile"
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
    total = len(today_trades)
    wins = sum(1 for t in today_trades if (t.get("pnl_usdt") or 0) > 0)
    losses = sum(1 for t in today_trades if (t.get("pnl_usdt") or 0) < 0)
    holds = sum(1 for t in today_trades if t.get("action") == "HOLD")
    blocked = sum(1 for t in today_trades if t.get("action") == "BLOCKED")
    total_pnl = sum(t.get("pnl_usdt", 0) or 0 for t in today_trades)

    trades_detail = ""
    for t in today_trades:
        pnl = t.get("pnl_usdt")
        pnl_str = f"${pnl:.2f}" if pnl is not None else "N/A"
        trades_detail += (
            f"  - {t['action']} @ ${t.get('price_at_decision', 0):,.2f} | "
            f"PnL: {pnl_str} | Confidence: {t.get('confidence')}/10 | "
            f"Reason: {t.get('reasoning', 'N/A')[:150]}\n"
        )

    lessons_context = ""
    for lesson in recent_lessons[:7]:
        lessons_context += (
            f"  [{lesson.get('date')}] {lesson.get('lesson_text', '')[:150]}\n"
            f"    Rule: {lesson.get('adjustment_made', 'N/A')}\n"
        )

    review_prompt = f"""=== TODAY'S PERFORMANCE ===
Total decisions: {total}
Wins: {wins} | Losses: {losses} | Holds: {holds} | Blocked: {blocked}
Total P&L today: ${total_pnl:+.2f}

=== TODAY'S TRADES (DETAIL) ===
{trades_detail or '  No trades executed today (all HOLDs or no cycles ran).'}

=== MARKET CONDITIONS TODAY ===
{market_summary}

=== RECENT LESSONS (last 7 days for pattern comparison) ===
{lessons_context or '  No previous lessons yet.'}

Write today's review. Be specific about what went right, what went wrong,
and give ONE concrete rule for tomorrow."""

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
