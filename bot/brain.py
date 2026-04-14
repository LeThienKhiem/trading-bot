"""
Claude API brain for the trading bot (Quant Edition).
Uses multi-timeframe technical analysis and risk/reward framework.
Pure data-driven — no news sentiment dependency.
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

TRADING_SYSTEM_PROMPT = """You are a quantitative BTC/USDT spot trading algorithm.
You make decisions based ONLY on technical data and statistical edge. No emotions, no FOMO, no panic.

PORTFOLIO: ~$150 USDT. Every dollar matters — protect capital first, grow second.

YOUR EDGE: Proven strategies from backtested Freqtrade community (65-82% win rate),
adapted for multi-timeframe analysis. You follow STRICT rules, not gut feelings.

═══════════════════════════════════════════
STRATEGY A: MEAN REVERSION (primary — 78% win rate backtested)
═══════════════════════════════════════════
Use when: Price dips within an uptrend (buying the dip).

BUY when ALL true:
  1. 4H EMA(20) > EMA(50) — uptrend confirmed on higher timeframe
  2. 1H Bollinger %B < 0.2 — price near/below lower band (oversold)
  3. 1H MACD histogram negative BUT turning up (current > previous)
  4. 1H RSI(14) between 25-45 — oversold zone, not falling knife
  5. Volume ratio > 1.0 (above 20-period average)
  6. Price > EMA(200) on 1H if available — long-term uptrend intact

SELL when ANY true:
  1. 1H Bollinger %B > 0.95 — price at upper band (target reached)
  2. 1H RSI > 75 — overbought
  3. 1H MACD bearish crossover (MACD line crosses below signal)
  4. Stop loss: entry - 2x ATR(14)
  5. Take profit: entry + 3x ATR(14)

═══════════════════════════════════════════
STRATEGY B: TRIPLE CONFLUENCE MOMENTUM (73% win rate, 235 trades)
═══════════════════════════════════════════
Use when: Fresh trend starting (catching momentum).

BUY when ALL true:
  1. 4H EMA(20) > EMA(50) — trend filter
  2. 1H MACD bullish crossover (fresh cross, not old)
  3. 1H RSI(14) between 45-65 — momentum zone, not overbought
  4. 1H Bollinger %B between 0.3-0.7 — price in middle zone trending up
  5. Volume ratio > 1.2 (strong participation)

SELL when ANY true:
  1. 1H RSI > 75 — overbought
  2. 1H MACD bearish crossover
  3. Price < EMA(50) on 4H — trend broken
  4. Stop loss: entry - 1.5x ATR(14)
  5. Take profit: entry + 3x ATR(14) — gives 2:1 R:R

═══════════════════════════════════════════
STRATEGY SELECTION LOGIC
═══════════════════════════════════════════
- If 1H BB %B < 0.2 and RSI < 45: use Strategy A (mean reversion)
- If 1H MACD just crossed bullish and RSI 45-65: use Strategy B (momentum)
- If neither setup is clean: HOLD. Wait for the next cycle.

═══════════════════════════════════════════
HOLD CONDITIONS (do NOT trade)
═══════════════════════════════════════════
- 4H EMA(20) < EMA(50) — downtrend, never buy against it
- 1H RSI between 45-55 AND MACD flat — no momentum, no direction
- Bollinger bandwidth < 2% — squeeze forming, wait for breakout
- Volume ratio < 0.8 — low conviction, fake moves likely
- 1H and 4H signals conflict (e.g., 1H bullish but 4H bearish)

═══════════════════════════════════════════
TRAILING STOP RULES (from NostalgiaForInfinity)
═══════════════════════════════════════════
After BUY, the price monitor handles these automatically, but factor them
into your take_profit_price:
- Profit > 1.5%: trailing stop activates at 1% below peak
- Profit > 3%: take partial mental note, trail tighter
- Profit > 5%: strongly consider full exit

═══════════════════════════════════════════
RISK MANAGEMENT
═══════════════════════════════════════════
- Risk/reward minimum 1.5:1 (prefer 2:1 or higher)
- Stop loss ALWAYS based on ATR — never arbitrary percentages
- Max 1 open position at a time
- Losing streak 3+: reduce position size, require confidence 9+
- NEVER chase price that already moved >3% in a direction

POSITION SIZING:
- Confidence 9-10: 40-50% of USDT
- Confidence 8: 30-40%
- Confidence 7: 20-30%
- Below 7: DO NOT TRADE

LEARNING: You receive last 30 trades + 14 daily lessons.
Reference them explicitly: "Strategy A triggered 3 times this week, won 2/3"

OUTPUT: Return ONLY valid JSON, no markdown:
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 1-10,
  "reasoning": "2-3 sentences referencing specific indicator values and past lessons",
  "position_size_pct": 20-50,
  "stop_loss_price": <exact price for stop loss based on ATR/support>,
  "take_profit_price": <exact price for take profit based on resistance>,
  "risk_reward_ratio": <calculated R:R ratio>,
  "market_regime": "trending_up" | "trending_down" | "sideways" | "volatile",
  "risk_level": "low" | "medium" | "high",
  "setup_quality": "A+" | "A" | "B" | "C" | "no_setup"
}"""


# ── Context Builder ──────────────────────────────────────────────────────────

def format_timeframe(tf_data: dict, label: str) -> str:
    """Format a single timeframe's data for the prompt."""
    if "error" in tf_data:
        return f"  {label}: DATA UNAVAILABLE\n"

    rsi = tf_data.get("rsi", "N/A")
    macd = tf_data.get("macd", {})
    bb = tf_data.get("bollinger", {})
    emas = tf_data.get("emas", {})
    atr = tf_data.get("atr", "N/A")
    volume = tf_data.get("volume", {})
    sr = tf_data.get("support_resistance", {})

    lines = [
        f"  [{label}] RSI: {rsi}",
        f"  [{label}] MACD: signal={macd.get('signal', 'N/A')}, histogram={macd.get('histogram', 'N/A')}, crossover={macd.get('crossover', 'none')}",
        f"  [{label}] Bollinger: upper={bb.get('upper', 'N/A')}, middle={bb.get('middle', 'N/A')}, lower={bb.get('lower', 'N/A')}, %B={bb.get('percent_b', 'N/A')}, bandwidth={bb.get('bandwidth', 'N/A')}%",
        f"  [{label}] EMA: 20={emas.get('ema_20', 'N/A')}, 50={emas.get('ema_50', 'N/A')}, 200={emas.get('ema_200', 'N/A')}, trend={emas.get('trend', 'N/A')}",
        f"  [{label}] ATR: {atr}",
        f"  [{label}] Volume: SMA ratio={volume.get('volume_sma_ratio', 'N/A')}, trend={volume.get('volume_trend', 'N/A')}, buy_pressure={volume.get('buy_pressure', 'N/A')}",
        f"  [{label}] S/R: support=${sr.get('nearest_support', 'N/A')} ({sr.get('distance_to_support_pct', 'N/A')}% away), resistance=${sr.get('nearest_resistance', 'N/A')} ({sr.get('distance_to_resistance_pct', 'N/A')}% away)",
    ]
    return "\n".join(lines) + "\n"


def build_context(
    market_data: dict,
    account_balance: dict,
    open_positions: list[dict],
    daily_pnl: Optional[dict] = None,
) -> str:
    """Build comprehensive context string for Claude."""
    recent_trades = memory.get_recent_trades(limit=30)
    recent_lessons = memory.get_recent_lessons(limit=14)
    win_rate = memory.calculate_win_rate(recent_trades)

    # Format timeframes
    timeframes = market_data.get("timeframes", {})
    tf_1h_str = format_timeframe(timeframes.get("1h", {}), "1H")
    tf_4h_str = format_timeframe(timeframes.get("4h", {}), "4H")

    # 24h stats
    stats = market_data.get("stats_24h", {})
    stats_str = (
        f"24h Change: {stats.get('price_change_pct', 'N/A')}%\n"
        f"24h High: ${stats.get('high_24h', 'N/A')} | Low: ${stats.get('low_24h', 'N/A')}\n"
        f"24h Volume: {stats.get('volume_24h', 'N/A')} BTC"
    )

    # Recent trades
    trades_summary = ""
    for t in recent_trades[:10]:
        pnl = t.get("pnl_usdt")
        pnl_str = f"PnL: ${pnl:.2f}" if pnl is not None else "PnL: pending"
        trades_summary += (
            f"  - {t['action']} @ ${t.get('price_at_decision', 0):,.2f} | "
            f"{pnl_str} | {t.get('reasoning', 'N/A')[:80]}\n"
        )

    # Lessons
    lessons_summary = ""
    for lesson in recent_lessons[:7]:
        lessons_summary += (
            f"  [{lesson.get('date')}] {lesson.get('lesson_text', 'N/A')[:200]}\n"
            f"    Rule: {lesson.get('adjustment_made', 'N/A')}\n"
        )

    # Open positions
    positions_str = "None"
    if open_positions:
        positions_str = "\n".join(
            f"  - {p['action']} @ ${p.get('price_at_decision', 0):,.2f} "
            f"(stop: ${p.get('suggested_stop_loss', 'N/A')}) "
            f"(qty: ${p.get('quantity_usdt', 0):.2f})"
            for p in open_positions
        )

    # Daily PnL
    pnl_str = "No data yet (first cycle)"
    if daily_pnl:
        pnl_str = (
            f"PnL: ${daily_pnl.get('pnl_usdt', 0):+.2f} "
            f"({daily_pnl.get('pnl_percent', 0):+.2f}%)\n"
            f"Peak balance: ${daily_pnl.get('peak_balance', 0):.2f}\n"
            f"Drawdown: {daily_pnl.get('current_drawdown_percent', 0):.2f}%"
        )

    # Win/loss streak
    streak = 0
    streak_type = "none"
    for t in recent_trades[:10]:
        pnl = t.get("pnl_usdt") or 0
        if pnl > 0:
            if streak_type == "win":
                streak += 1
            else:
                streak_type = "win"
                streak = 1
                break
        elif pnl < 0:
            if streak_type == "loss":
                streak += 1
            else:
                streak_type = "loss"
                streak = 1
                break

    context = f"""=== BTC/USDT PRICE ===
${market_data['btc_price']:,.2f}

=== MULTI-TIMEFRAME TECHNICAL ANALYSIS ===
{tf_1h_str}
{tf_4h_str}

=== 24H MARKET STATS ===
{stats_str}

=== FEAR & GREED INDEX ===
{market_data.get('fear_greed_index', 'N/A')}/100

=== ACCOUNT ===
USDT: ${account_balance.get('usdt', 0):,.2f}
Bot BTC: {account_balance.get('btc_bot', 0):.8f}
Portfolio: ~${account_balance.get('total_usdt', 0):,.2f}

=== TODAY'S P&L ===
{pnl_str}

=== PERFORMANCE STATS ===
Win Rate: {win_rate:.1%} ({len(recent_trades)} trades)
Current streak: {streak} {streak_type}{'s' if streak > 1 else ''} in a row

=== OPEN POSITIONS ===
{positions_str}

=== RECENT TRADES ===
{trades_summary or '  No trades yet'}

=== LESSONS LEARNED ===
{lessons_summary or '  No lessons yet.'}
"""
    return context


def get_trading_decision(
    market_data: dict,
    account_balance: dict,
    open_positions: list[dict],
    daily_pnl: Optional[dict] = None,
) -> Optional[dict]:
    """Ask Claude for a trading decision based on all available context."""
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
        logger.info(f"Claude raw response: {raw_text[:300]}")

        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1]
            raw_text = raw_text.rsplit("```", 1)[0]

        decision = json.loads(raw_text)

        required = ["action", "confidence", "reasoning"]
        for field in required:
            if field not in decision:
                logger.error(f"Missing field: {field}")
                return None

        decision["action"] = decision["action"].upper()
        if decision["action"] not in ("BUY", "SELL", "HOLD"):
            logger.error(f"Invalid action: {decision['action']}")
            return None

        logger.info(
            f"Decision: {decision['action']} (confidence: {decision['confidence']}/10, "
            f"setup: {decision.get('setup_quality', 'N/A')})"
        )
        return decision

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Claude response: {e}")
        return None
    except anthropic.APITimeoutError:
        logger.warning("Claude API timed out")
        return None
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return None


# ── Nightly Review ───────────────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """You are reviewing your quantitative trading performance for today.
Be brutally honest. Vague lessons are useless. Use data.

REVIEW FRAMEWORK:
1. PERFORMANCE METRICS
   - Win rate today, total P&L, average risk/reward achieved
   - Compare to overall win rate — improving or declining?

2. TRADE ANALYSIS (for each trade)
   - Entry: was the 1H/4H alignment correct? Was R:R >= 2?
   - Exit: did we follow the plan (stop loss/take profit)?
   - Grade each trade: A (perfect execution), B (good but improvable), C (mistake), F (violated rules)

3. PATTERN DETECTION
   - Compare today vs last 7 days — recurring mistake?
   - Which indicator gave the best signals? Which gave false signals?
   - Was the market regime correctly identified?

4. STRATEGY ADJUSTMENT
   - ONE specific, testable rule change for tomorrow
   - Must be data-driven: "When [indicator] is [value], do [action]"
   - Example: "When 4H RSI > 70 AND 1H MACD bearish cross, take profit immediately"

5. RISK ASSESSMENT
   - Were position sizes appropriate for signal quality?
   - Any near-miss disasters? (big drawdown avoided by luck)
   - Confidence in tomorrow's market: what regime to expect?

OUTPUT: Valid JSON only:
{
  "lesson_text": "full honest review with specific numbers (2-3 paragraphs)",
  "pattern_identified": "specific recurring pattern or null",
  "rule_for_tomorrow": "ONE specific data-driven rule",
  "strategy_confidence": 1-10,
  "market_regime_today": "trending_up|trending_down|sideways|volatile",
  "best_indicator_today": "which indicator was most accurate",
  "worst_indicator_today": "which indicator gave false signals",
  "suggested_adjustments": {
    "position_sizing": "increase|decrease|maintain",
    "confidence_threshold": "raise|lower|maintain",
    "timeframe_weight": "favor_1h|favor_4h|balanced"
  }
}"""


def get_daily_review(
    today_trades: list[dict],
    recent_lessons: list[dict],
    market_summary: str,
) -> Optional[dict]:
    """Ask Claude to review today's performance."""
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
            f"Setup: {t.get('setup_quality', 'N/A')} | "
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
Total P&L: ${total_pnl:+.2f}
Win rate today: {wins/max(wins+losses,1)*100:.0f}%

=== TRADE DETAILS ===
{trades_detail or '  No trades executed today.'}

=== MARKET CONDITIONS ===
{market_summary}

=== RECENT LESSONS ===
{lessons_context or '  No previous lessons.'}

Review today. Grade each trade. Give ONE concrete rule for tomorrow."""

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
        logger.error(f"Failed to get daily review: {e}")
        return None
