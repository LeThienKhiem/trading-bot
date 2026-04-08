"""
Binance order executor for the trading bot.
Handles all safety checks, order placement, stop-loss management,
and account balance queries. Every Binance call is wrapped in
try/except to ensure the bot never crashes on execution failure.
"""

import logging
from typing import Optional

from binance.client import Client as BinanceClient
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET, ORDER_TYPE_STOP_LOSS_LIMIT

import config
from bot import memory, notifier
from bot.market_data import get_binance_client, get_current_price

logger = logging.getLogger(__name__)


# ── Account Balance ──────────────────────────────────────────────────────────

def get_bot_btc_quantity() -> float:
    """
    Calculate how much BTC the bot currently owns by summing open BUY
    positions from Supabase. This ensures the bot only sells BTC it
    bought, not BTC the user holds from DCA or other strategies.

    Returns:
        Total BTC quantity owned by the bot.
    """
    open_trades = memory.get_open_trades()
    total_btc = 0.0
    for t in open_trades:
        if t.get("action") == "BUY" and t.get("quantity_usdt") and t.get("price_at_decision"):
            total_btc += t["quantity_usdt"] / t["price_at_decision"]
    return total_btc


def get_account_balance() -> dict:
    """
    Fetch current USDT and BTC balances from Binance.
    Also tracks bot-owned BTC separately from user's existing holdings.

    Returns:
        Dict with keys: usdt, btc, btc_bot, total_usdt.
        - btc: total BTC in Binance account
        - btc_bot: BTC that the bot bought (tracked via Supabase)
        Returns zero balances on failure.
    """
    try:
        client = get_binance_client()
        account = client.get_account()

        # Debug: send raw account permissions to Telegram
        can_trade = account.get("canTrade", "?")
        can_withdraw = account.get("canWithdraw", "?")
        can_deposit = account.get("canDeposit", "?")
        account_type = account.get("accountType", "?")
        total_balances_count = len(account.get("balances", []))
        logger.info(
            f"Binance account: type={account_type}, canTrade={can_trade}, "
            f"canWithdraw={can_withdraw}, canDeposit={can_deposit}, "
            f"total_assets={total_balances_count}"
        )

        # Build balance dict with both free and locked amounts
        free_balances = {}
        locked_balances = {}
        non_zero_assets = []
        for b in account["balances"]:
            free_val = float(b["free"])
            locked_val = float(b["locked"])
            if free_val > 0 or locked_val > 0:
                free_balances[b["asset"]] = free_val
                locked_balances[b["asset"]] = locked_val
                non_zero_assets.append(
                    f"{b['asset']}: free={free_val}, locked={locked_val}"
                )
                logger.info(
                    f"  {b['asset']}: free={free_val}, locked={locked_val}"
                )

        # Send diagnostic to Telegram (temporary debug)
        diag = (
            f"🔍 <b>BALANCE DEBUG</b>\n"
            f"Account type: {account_type}\n"
            f"canTrade: {can_trade}\n"
            f"Total assets in response: {total_balances_count}\n"
            f"Non-zero assets: {len(non_zero_assets)}\n"
        )
        if non_zero_assets:
            diag += "\n".join(non_zero_assets[:10])
        else:
            diag += "⚠️ ALL BALANCES ARE ZERO"
        notifier.send_message(diag)

        usdt_free = free_balances.get("USDT", 0.0)
        usdt_locked = locked_balances.get("USDT", 0.0)
        usdt_total = usdt_free + usdt_locked
        btc_free = free_balances.get("BTC", 0.0)
        btc_locked = locked_balances.get("BTC", 0.0)
        btc_total = btc_free + btc_locked
        btc_bot = get_bot_btc_quantity()

        logger.info(
            f"USDT: free={usdt_free}, locked={usdt_locked}, total={usdt_total}"
        )
        logger.info(
            f"BTC: free={btc_free}, locked={btc_locked}, total={btc_total}"
        )

        # Use free USDT for trading decisions (locked is in pending orders)
        # But report total for portfolio value
        btc_price = get_current_price() or 0
        total = usdt_free + (btc_bot * btc_price)

        balance = {
            "usdt": round(usdt_free, 2),
            "usdt_total": round(usdt_total, 2),
            "btc": btc_free,
            "btc_total": btc_total,
            "btc_bot": btc_bot,
            "total_usdt": round(total, 2),
        }
        logger.info(
            f"Account balance: ${balance['usdt']} USDT (free) + "
            f"${round(usdt_locked, 2)} USDT (locked) | "
            f"{balance['btc']:.8f} BTC (bot owns: {btc_bot:.8f} BTC) "
            f"= ~${balance['total_usdt']} (bot portfolio)"
        )
        return balance

    except Exception as e:
        logger.error(f"Failed to fetch account balance: {e}", exc_info=True)
        notifier.send_message(
            f"🚨 <b>BALANCE ERROR</b>\n"
            f"<code>{type(e).__name__}: {str(e)[:300]}</code>"
        )
        return {"usdt": 0.0, "usdt_total": 0.0, "btc": 0.0, "btc_total": 0.0, "btc_bot": 0.0, "total_usdt": 0.0}


def save_account_snapshot(balance: Optional[dict] = None) -> None:
    """
    Save current account state to Supabase account_snapshots table.
    Includes daily PnL and drawdown calculations.

    Args:
        balance: Optional pre-fetched balance dict. Fetches fresh if None.
    """
    if balance is None:
        balance = get_account_balance()

    daily_pnl = memory.calculate_daily_pnl(balance["total_usdt"])

    snapshot = {
        "usdt_balance": balance["usdt"],
        "btc_balance": balance.get("btc_bot", balance["btc"]),
        "total_value_usdt": balance["total_usdt"],
        "daily_pnl_percent": daily_pnl["pnl_percent"],
    }
    memory.save_account_snapshot(snapshot)


# ── Safety Checks ────────────────────────────────────────────────────────────

def run_safety_checks(
    decision: dict,
    balance: dict,
    open_positions: list[dict],
    daily_pnl: Optional[dict] = None,
) -> tuple[bool, str]:
    """
    Run all safety checks before executing a trade.

    Args:
        decision: Claude's decision dict (action, confidence, news_impact, etc.).
        balance: Current account balance dict.
        open_positions: List of open position dicts.
        daily_pnl: Optional daily PnL info dict.

    Returns:
        Tuple of (is_safe, reason).
        - is_safe: True if the trade can proceed.
        - reason: Explanation if blocked.
    """
    action = decision.get("action", "HOLD")
    confidence = decision.get("confidence", 0)
    news_impact = decision.get("news_impact", "neutral")

    # HOLD always passes — just log it
    if action == "HOLD":
        return True, "HOLD decision — no execution needed"

    # Hard stop: protect minimum balance
    if balance["total_usdt"] < config.STOP_LOSS_MINIMUM_BALANCE:
        notifier.notify_error(
            f"🚨 SAFETY STOP TRIGGERED\n"
            f"Balance dropped to ${balance['total_usdt']:.2f} "
            f"(below ${config.STOP_LOSS_MINIMUM_BALANCE} threshold)\n"
            f"Bot paused. Review required."
        )
        return False, (
            f"HARD STOP: Total balance ${balance['total_usdt']:.2f} is below "
            f"minimum ${config.STOP_LOSS_MINIMUM_BALANCE} — forcing HOLD"
        )

    # Daily target reached — switch to HOLD-only mode
    if daily_pnl and daily_pnl.get("target_reached"):
        notifier.notify_error(
            f"✅ Daily target +{config.DAILY_TARGET_PERCENT}% reached!\n"
            f"Switching to HOLD mode for today.\n"
            f"Balance: ${balance['total_usdt']:.2f}"
        )
        return False, (
            f"Daily target +{config.DAILY_TARGET_PERCENT}% reached — "
            f"HOLD-only mode to protect gains"
        )

    # Check minimum confidence
    if confidence < config.MIN_CONFIDENCE_TO_TRADE:
        return False, (
            f"Confidence {confidence}/10 is below minimum "
            f"{config.MIN_CONFIDENCE_TO_TRADE}/10 — forcing HOLD"
        )

    # High alert news = always HOLD
    if news_impact == "high_alert":
        return False, "NEWS HIGH ALERT detected — forcing HOLD for safety"

    # BUY-specific checks
    if action == "BUY":
        btc_bot_value = balance.get("btc_bot", 0) * (get_current_price() or 0)
        if btc_bot_value > balance["total_usdt"] * 0.5:
            return False, (
                f"Bot's BTC position (${btc_bot_value:.2f}) is >50% of "
                f"bot portfolio — cannot BUY more"
            )

        trade_amount = balance["usdt"] * config.MAX_POSITION_PERCENT
        if trade_amount < 10:
            return False, (
                f"Trade amount ${trade_amount:.2f} is below Binance minimum"
            )

    # SELL-specific checks
    if action == "SELL":
        if balance.get("btc_bot", 0) <= 0:
            return False, "Bot has no BTC positions to sell"

    return True, "All safety checks passed"


# ── Order Execution ──────────────────────────────────────────────────────────

def execute_decision(decision: dict, market_data: dict) -> Optional[dict]:
    """
    Execute a trading decision after running safety checks.
    Handles BUY, SELL, HOLD, and BLOCKED actions. Logs all decisions to Supabase.
    Supports dry-run mode (config.DRY_RUN) which skips actual Binance execution.

    Args:
        decision: Claude's decision dict.
        market_data: Current market data dict.

    Returns:
        The saved trade record dict, or None on failure.
    """
    balance = get_account_balance()
    open_positions = memory.get_open_trades()
    daily_pnl = memory.calculate_daily_pnl(balance["total_usdt"])

    # Run safety checks
    is_safe, reason = run_safety_checks(decision, balance, open_positions, daily_pnl)

    original_action = decision.get("action", "HOLD")
    action = original_action
    price = market_data.get("btc_price", 0)

    # If safety check fails, log as BLOCKED
    if not is_safe and action != "HOLD":
        logger.warning(f"Safety check blocked {action}: {reason}")
        action = "BLOCKED"

    # Calculate stop loss price
    stop_loss_pct = decision.get("suggested_stop_loss_percent", 5)
    stop_loss_price = round(price * (1 - stop_loss_pct / 100), 2)

    # Build trade record
    trade_record = {
        "action": action,
        "symbol": config.SYMBOL,
        "price_at_decision": price,
        "quantity_usdt": 0.0,
        "reasoning": decision.get("reasoning", reason),
        "confidence": decision.get("confidence", 0),
        "suggested_stop_loss": stop_loss_price,
        "status": "closed" if action in ("HOLD", "BLOCKED") else "open",
    }

    # Add block reason to reasoning
    if action == "BLOCKED":
        trade_record["reasoning"] = (
            f"[BLOCKED: {reason}] Original: {original_action}. "
            f"{decision.get('reasoning', '')}"
        )

    # Dry-run mode: log but don't execute
    if config.DRY_RUN:
        logger.info(f"[DRY RUN] Would execute: {action} @ ${price:,.2f}")
        trade_record["reasoning"] = f"[DRY RUN] {trade_record['reasoning']}"
        if action in ("BUY", "SELL"):
            trade_record["status"] = "closed"
            action = "HOLD"  # Don't execute

    # Execute the trade on Binance
    if action == "BUY":
        trade_result = _execute_buy(balance, price)
        if trade_result:
            trade_record["quantity_usdt"] = trade_result["cost_usdt"]
            trade_record["price_at_decision"] = trade_result["fill_price"]
            logger.info(
                f"BUY executed: {trade_result['btc_qty']:.8f} BTC "
                f"@ ${trade_result['fill_price']:,.2f}"
            )
            save_account_snapshot()
        else:
            trade_record["action"] = "BLOCKED"
            trade_record["status"] = "closed"
            trade_record["reasoning"] += " [BUY execution failed on Binance]"

    elif action == "SELL":
        trade_result = _execute_sell(balance, price, open_positions)
        if trade_result:
            trade_record["quantity_usdt"] = trade_result["received_usdt"]
            trade_record["price_at_decision"] = trade_result["fill_price"]
            logger.info(
                f"SELL executed: {trade_result['btc_qty']:.8f} BTC "
                f"@ ${trade_result['fill_price']:,.2f}"
            )
            _close_open_positions(open_positions, trade_result["fill_price"])
            save_account_snapshot()
        else:
            trade_record["action"] = "BLOCKED"
            trade_record["status"] = "closed"
            trade_record["reasoning"] += " [SELL execution failed on Binance]"

    # Always log the decision to Supabase
    saved = memory.save_trade(trade_record)

    # Save account snapshot on each cycle
    save_account_snapshot(balance)

    # Send Telegram notification with daily PnL info
    notifier.notify_trade(decision, market_data, balance, daily_pnl)

    return saved


# ── Binance Order Helpers ────────────────────────────────────────────────────

def _execute_buy(balance: dict, current_price: float) -> Optional[dict]:
    """
    Execute a market BUY order on Binance.

    Args:
        balance: Current account balance dict.
        current_price: Current BTC price for reference.

    Returns:
        Dict with btc_qty, fill_price, cost_usdt, or None on failure.
    """
    try:
        client = get_binance_client()
        usdt_to_spend = round(balance["usdt"] * config.MAX_POSITION_PERCENT, 2)

        if usdt_to_spend < 10:
            logger.warning(f"Insufficient USDT to buy: ${usdt_to_spend}")
            return None

        order = client.create_order(
            symbol=config.SYMBOL,
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quoteOrderQty=usdt_to_spend,
        )

        fills = order.get("fills", [])
        total_qty = sum(float(f["qty"]) for f in fills)
        total_cost = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        avg_price = total_cost / total_qty if total_qty > 0 else current_price

        logger.info(f"BUY order filled: {total_qty:.8f} BTC for ${total_cost:.2f}")
        return {
            "btc_qty": total_qty,
            "fill_price": round(avg_price, 2),
            "cost_usdt": round(total_cost, 2),
        }

    except Exception as e:
        logger.error(f"Failed to execute BUY order: {e}")
        return None


def _execute_sell(
    balance: dict,
    current_price: float,
    open_positions: list[dict],
) -> Optional[dict]:
    """
    Execute a market SELL order on Binance. Only sells BTC that the bot
    bought (tracked via open positions in Supabase), leaving the user's
    existing BTC holdings untouched.

    Args:
        balance: Current account balance dict.
        current_price: Current BTC price for reference.
        open_positions: List of open trade dicts.

    Returns:
        Dict with btc_qty, fill_price, received_usdt, or None on failure.
    """
    try:
        client = get_binance_client()

        btc_to_sell = balance.get("btc_bot", 0)

        if btc_to_sell <= 0:
            logger.warning("Bot has no BTC positions to sell")
            return None

        if btc_to_sell > balance["btc"]:
            logger.warning(
                f"Bot thinks it owns {btc_to_sell:.8f} BTC but account "
                f"only has {balance['btc']:.8f} — selling available amount"
            )
            btc_to_sell = balance["btc"]

        # Get step size for proper quantity formatting
        info = client.get_symbol_info(config.SYMBOL)
        step_size = None
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                break

        if step_size:
            precision = len(str(step_size).rstrip("0").split(".")[-1])
            btc_to_sell = round(btc_to_sell, precision)

        order = client.create_order(
            symbol=config.SYMBOL,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=btc_to_sell,
        )

        fills = order.get("fills", [])
        total_qty = sum(float(f["qty"]) for f in fills)
        total_received = sum(
            float(f["qty"]) * float(f["price"]) for f in fills
        )
        avg_price = total_received / total_qty if total_qty > 0 else current_price

        logger.info(
            f"SELL order filled: {total_qty:.8f} BTC for ${total_received:.2f}"
        )
        return {
            "btc_qty": total_qty,
            "fill_price": round(avg_price, 2),
            "received_usdt": round(total_received, 2),
        }

    except Exception as e:
        logger.error(f"Failed to execute SELL order: {e}")
        return None


def _close_open_positions(open_positions: list[dict], exit_price: float) -> None:
    """
    Close all open BUY positions by updating their status and PnL in Supabase.

    Args:
        open_positions: List of open trade dicts.
        exit_price: The price at which the position was closed.
    """
    for pos in open_positions:
        if pos.get("action") != "BUY":
            continue

        entry_price = pos.get("price_at_decision", 0)
        quantity = pos.get("quantity_usdt", 0)

        if entry_price > 0 and quantity > 0:
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
            pnl_usdt = quantity * (pnl_percent / 100)
        else:
            pnl_percent = 0.0
            pnl_usdt = 0.0

        memory.update_trade(pos["id"], {
            "status": "closed",
            "exit_price": exit_price,
            "pnl_usdt": round(pnl_usdt, 2),
            "pnl_percent": round(pnl_percent, 2),
        })
        logger.info(
            f"Closed position {pos['id']}: PnL ${pnl_usdt:.2f} ({pnl_percent:.2f}%)"
        )
