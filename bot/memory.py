"""
Supabase database interface for the trading bot.
Handles all reads and writes to trades, daily_lessons, account_snapshots,
and market_contexts tables. Every write is wrapped in try/except to ensure
the bot never crashes due to a database failure.
"""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

from supabase import create_client, Client

import config

logger = logging.getLogger(__name__)

# ── Supabase Client ──────────────────────────────────────────────────────────

_client: Optional[Client] = None


def get_client() -> Client:
    """Return a singleton Supabase client, creating it on first call."""
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_ANON_KEY)
    return _client


# ── Trades ───────────────────────────────────────────────────────────────────

def save_trade(trade_data: dict) -> Optional[dict]:
    """
    Insert a new row into the trades table.

    Args:
        trade_data: Dict with keys matching the trades table columns
                    (action, symbol, price_at_decision, quantity_usdt,
                     reasoning, confidence, suggested_stop_loss, status).

    Returns:
        The inserted row as a dict, or None on failure.
    """
    try:
        result = get_client().table("trades").insert(trade_data).execute()
        logger.info(f"Trade saved: {trade_data.get('action')} @ ${trade_data.get('price_at_decision')}")
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to save trade: {e}")
        return None


def update_trade(trade_id: str, updates: dict) -> Optional[dict]:
    """
    Update an existing trade row (e.g. to close it with exit_price and pnl).

    Args:
        trade_id: UUID of the trade to update.
        updates: Dict of column names to new values.

    Returns:
        The updated row as a dict, or None on failure.
    """
    try:
        result = (
            get_client()
            .table("trades")
            .update(updates)
            .eq("id", trade_id)
            .execute()
        )
        logger.info(f"Trade {trade_id} updated: {updates}")
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to update trade {trade_id}: {e}")
        return None


def get_recent_trades(limit: int = 30) -> list[dict]:
    """
    Fetch the most recent trades, ordered newest first.

    Args:
        limit: Maximum number of trades to return (default 30).

    Returns:
        List of trade dicts, or empty list on failure.
    """
    try:
        result = (
            get_client()
            .table("trades")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch recent trades: {e}")
        return []


def get_open_trades() -> list[dict]:
    """
    Fetch all trades with status='open'.

    Returns:
        List of open trade dicts, or empty list on failure.
    """
    try:
        result = (
            get_client()
            .table("trades")
            .select("*")
            .eq("status", "open")
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch open trades: {e}")
        return []


def get_today_trades() -> list[dict]:
    """
    Fetch all trades created today (UTC).

    Returns:
        List of trade dicts from today, or empty list on failure.
    """
    try:
        today_start = datetime.utcnow().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        result = (
            get_client()
            .table("trades")
            .select("*")
            .gte("created_at", today_start)
            .order("created_at", desc=False)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch today's trades: {e}")
        return []


# ── Daily Lessons ────────────────────────────────────────────────────────────

def save_daily_lesson(lesson_data: dict) -> Optional[dict]:
    """
    Insert a new daily lesson from the nightly review.

    Args:
        lesson_data: Dict with keys matching the daily_lessons table columns.

    Returns:
        The inserted row as a dict, or None on failure.
    """
    try:
        result = get_client().table("daily_lessons").insert(lesson_data).execute()
        logger.info(f"Daily lesson saved for {lesson_data.get('date')}")
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to save daily lesson: {e}")
        return None


def get_recent_lessons(limit: int = 14) -> list[dict]:
    """
    Fetch the most recent daily lessons, ordered newest first.

    Args:
        limit: Maximum number of lessons to return (default 14).

    Returns:
        List of lesson dicts, or empty list on failure.
    """
    try:
        result = (
            get_client()
            .table("daily_lessons")
            .select("*")
            .order("date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to fetch recent lessons: {e}")
        return []


# ── Account Snapshots ────────────────────────────────────────────────────────

def save_account_snapshot(snapshot_data: dict) -> Optional[dict]:
    """
    Insert a new account snapshot.

    Args:
        snapshot_data: Dict with usdt_balance, btc_balance,
                       total_value_usdt, daily_pnl_percent.

    Returns:
        The inserted row as a dict, or None on failure.
    """
    try:
        result = (
            get_client()
            .table("account_snapshots")
            .insert(snapshot_data)
            .execute()
        )
        logger.info(
            f"Account snapshot saved: ${snapshot_data.get('total_value_usdt', 0):.2f}"
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to save account snapshot: {e}")
        return None


def get_latest_snapshot() -> Optional[dict]:
    """
    Fetch the most recent account snapshot.

    Returns:
        The latest snapshot dict, or None if none found or on failure.
    """
    try:
        result = (
            get_client()
            .table("account_snapshots")
            .select("*")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to fetch latest snapshot: {e}")
        return None


# ── Market Contexts ──────────────────────────────────────────────────────────

def save_market_context(context_data: dict) -> Optional[dict]:
    """
    Insert a new market context snapshot.

    Args:
        context_data: Dict with btc_price, rsi_1h, macd_signal,
                      fear_greed_index, top_news_headlines, news_sentiment.

    Returns:
        The inserted row as a dict, or None on failure.
    """
    try:
        result = (
            get_client()
            .table("market_contexts")
            .insert(context_data)
            .execute()
        )
        logger.info(f"Market context saved: BTC=${context_data.get('btc_price')}")
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Failed to save market context: {e}")
        return None


# ── Utility ──────────────────────────────────────────────────────────────────

def calculate_win_rate(trades: list[dict]) -> float:
    """
    Calculate win rate from a list of closed trades.

    Args:
        trades: List of trade dicts (should have pnl_usdt field).

    Returns:
        Win rate as a float between 0.0 and 1.0. Returns 0.0 if no
        closed trades with PnL data are found.
    """
    closed = [t for t in trades if t.get("pnl_usdt") is not None]
    if not closed:
        return 0.0
    wins = sum(1 for t in closed if t["pnl_usdt"] > 0)
    return wins / len(closed)
