"""
Binance order executor for the trading bot (Quant Edition).
Handles dynamic position sizing, safety checks, order placement,
and integration with price monitor for stop-loss/take-profit.
"""

import logging
from typing import Optional

from binance.client import Client as BinanceClient
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET

import config
from bot import memory, notifier
from bot.market_data import get_binance_client, get_current_price

logger = logging.getLogger(__name__)


# ── Account Balance ──────────────────────────────────────────────────────────

def get_bot_btc_quantity() -> float:
    """Calculate how much BTC the bot currently owns from Supabase open positions."""
    open_trades = memory.get_open_trades()
    total_btc = 0.0
    for t in open_trades:
        if t.get("action") == "BUY" and t.get("quantity_usdt") and t.get("price_at_decision"):
            total_btc += t["quantity_usdt"] / t["price_at_decision"]
    return total_btc


def get_account_balance() -> dict:
    """Fetch current USDT and BTC balances from Binance."""
    try:
        client = get_binance_client()
        account = client.get_account()

        free_balances = {}
        locked_balances = {}
        for b in account["balances"]:
            free_val = float(b["free"])
            locked_val = float(b["locked"])
            if free_val > 0 or locked_val > 0:
                free_balances[b["asset"]] = free_val
                locked_balances[b["asset"]] = locked_val

        usdt_free = free_balances.get("USDT", 0.0)
        usdt_locked = locked_balances.get("USDT", 0.0)
        usdt_total = usdt_free + usdt_locked
        btc_free = free_balances.get("BTC", 0.0)
        btc_total = btc_free + locked_balances.get("BTC", 0.0)
        btc_bot = get_bot_btc_quantity()

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
            f"Balance: ${balance['usdt']} USDT | "
            f"Bot BTC: {btc_bot:.8f} (~${btc_bot * btc_price:.2f}) | "
            f"Portfolio: ${balance['total_usdt']}"
        )
        return balance

    except Exception as e:
        logger.error(f"Failed to fetch balance: {e}", exc_info=True)
        notifier.notify_error(f"Cannot read Binance balance: {type(e).__name__}")
        return {"usdt": 0.0, "usdt_total": 0.0, "btc": 0.0, "btc_total": 0.0, "btc_bot": 0.0, "total_usdt": 0.0}


def save_account_snapshot(balance: Optional[dict] = None) -> None:
    """Save current account state to Supabase."""
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


# ── Dynamic Position Sizing ──────────────────────────────────────────────────

def calculate_position_size(decision: dict, balance: dict) -> float:
    """
    Calculate position size based on confidence and recent performance.

    Claude suggests position_size_pct (20-50%), but we apply additional
    adjustments based on win streak/loss streak.
    """
    confidence = decision.get("confidence", 0)
    suggested_pct = decision.get("position_size_pct", 30) / 100

    # Clamp to config limits
    min_pct = config.MIN_POSITION_PERCENT
    max_pct = config.MAX_POSITION_PERCENT
    position_pct = max(min_pct, min(max_pct, suggested_pct))

    # Check recent performance for adjustment
    recent_trades = memory.get_recent_trades(limit=5)
    recent_losses = sum(1 for t in recent_trades if (t.get("pnl_usdt") or 0) < 0)

    # On losing streak: reduce size
    if recent_losses >= 3:
        position_pct *= 0.5
        logger.info(f"Losing streak ({recent_losses}/5) — reducing position to {position_pct*100:.0f}%")
    elif recent_losses >= 2:
        position_pct *= 0.75
        logger.info(f"Recent losses ({recent_losses}/5) — reducing position to {position_pct*100:.0f}%")

    usdt_to_spend = round(balance["usdt"] * position_pct, 2)
    logger.info(
        f"Position sizing: confidence={confidence}, pct={position_pct*100:.0f}%, "
        f"amount=${usdt_to_spend}"
    )
    return usdt_to_spend


# ── Safety Checks ────────────────────────────────────────────────────────────

def run_safety_checks(
    decision: dict,
    balance: dict,
    open_positions: list[dict],
    daily_pnl: Optional[dict] = None,
) -> tuple[bool, str]:
    """Run all safety checks before executing a trade."""
    action = decision.get("action", "HOLD")
    confidence = decision.get("confidence", 0)

    if action == "HOLD":
        return True, "HOLD — no execution needed"

    # Hard stop: protect minimum balance
    if balance["total_usdt"] < config.STOP_LOSS_MINIMUM_BALANCE:
        notifier.notify_error(
            f"SAFETY STOP: Balance ${balance['total_usdt']:.2f} "
            f"below ${config.STOP_LOSS_MINIMUM_BALANCE}"
        )
        return False, f"HARD STOP: Balance below ${config.STOP_LOSS_MINIMUM_BALANCE}"

    # Confidence gate
    if confidence < config.MIN_CONFIDENCE_TO_TRADE:
        return False, f"Confidence {confidence}/10 below minimum {config.MIN_CONFIDENCE_TO_TRADE}"

    # Check cooldown from price monitor
    from bot.price_monitor import price_monitor
    if price_monitor.is_in_cooldown():
        return False, "Trade cooldown active — waiting after recent auto-sell"

    # BUY checks
    if action == "BUY":
        # Can't buy if already holding
        if open_positions:
            return False, "Already have an open position — max 1 at a time"

        trade_amount = calculate_position_size(decision, balance)
        if trade_amount < 10:
            return False, f"Trade amount ${trade_amount:.2f} below Binance minimum"

        # Risk/reward check
        rr = decision.get("risk_reward_ratio", 0)
        if rr and rr < config.MIN_RISK_REWARD_RATIO:
            return False, f"Risk/reward {rr:.1f} below minimum {config.MIN_RISK_REWARD_RATIO}"

    # SELL checks
    if action == "SELL":
        if balance.get("btc_bot", 0) <= 0:
            return False, "No bot BTC positions to sell"

    return True, "All safety checks passed"


# ── Order Execution ──────────────────────────────────────────────────────────

def execute_decision(decision: dict, market_data: dict) -> Optional[dict]:
    """Execute a trading decision after running safety checks."""
    balance = get_account_balance()
    open_positions = memory.get_open_trades()
    daily_pnl = memory.calculate_daily_pnl(balance["total_usdt"])

    is_safe, reason = run_safety_checks(decision, balance, open_positions, daily_pnl)

    original_action = decision.get("action", "HOLD")
    action = original_action
    price = market_data.get("btc_price", 0)

    if not is_safe and action != "HOLD":
        logger.warning(f"Safety blocked {action}: {reason}")
        action = "BLOCKED"

    # Build trade record
    stop_loss_price = decision.get("stop_loss_price", price * 0.95)
    take_profit_price = decision.get("take_profit_price", price * 1.03)

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

    if action == "BLOCKED":
        trade_record["reasoning"] = (
            f"[BLOCKED: {reason}] Original: {original_action}. "
            f"{decision.get('reasoning', '')}"
        )

    # Dry-run mode
    if config.DRY_RUN and action in ("BUY", "SELL"):
        logger.info(f"[DRY RUN] Would execute: {action} @ ${price:,.2f}")
        trade_record["reasoning"] = f"[DRY RUN] {trade_record['reasoning']}"
        trade_record["status"] = "closed"
        action = "HOLD"

    # Execute BUY
    if action == "BUY":
        usdt_to_spend = calculate_position_size(decision, balance)
        trade_result = _execute_buy(balance, price, usdt_to_spend)
        if trade_result:
            trade_record["quantity_usdt"] = trade_result["cost_usdt"]
            trade_record["price_at_decision"] = trade_result["fill_price"]
            logger.info(
                f"BUY: {trade_result['btc_qty']:.8f} BTC @ ${trade_result['fill_price']:,.2f}"
            )
            # Set up price monitor for this position
            from bot.price_monitor import price_monitor
            price_monitor.update_position(
                entry_price=trade_result["fill_price"],
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                quantity_usdt=trade_result["cost_usdt"],
            )
            save_account_snapshot()
        else:
            trade_record["action"] = "BLOCKED"
            trade_record["status"] = "closed"
            trade_record["reasoning"] += " [BUY execution failed]"

    # Execute SELL
    elif action == "SELL":
        trade_result = _execute_sell(balance, price, open_positions)
        if trade_result:
            trade_record["quantity_usdt"] = trade_result["received_usdt"]
            trade_record["price_at_decision"] = trade_result["fill_price"]
            logger.info(
                f"SELL: {trade_result['btc_qty']:.8f} BTC @ ${trade_result['fill_price']:,.2f}"
            )
            _close_open_positions(open_positions, trade_result["fill_price"])
            from bot.price_monitor import price_monitor
            price_monitor.clear_position()
            save_account_snapshot()
        else:
            trade_record["action"] = "BLOCKED"
            trade_record["status"] = "closed"
            trade_record["reasoning"] += " [SELL execution failed]"

    # Log to Supabase
    saved = memory.save_trade(trade_record)
    save_account_snapshot(balance)

    # Telegram notification — use trade_record (actual result), not decision (intent)
    actual_decision = dict(decision)
    actual_decision["action"] = trade_record["action"]
    actual_decision["reasoning"] = trade_record["reasoning"]
    # Refresh balance after execution
    if trade_record["action"] in ("BUY", "SELL"):
        balance = get_account_balance()
    notifier.notify_trade(actual_decision, market_data, balance, daily_pnl)

    return saved


# ── Binance Order Helpers ────────────────────────────────────────────────────

def _execute_buy(balance: dict, current_price: float, usdt_to_spend: float) -> Optional[dict]:
    """Execute a market BUY order on Binance."""
    try:
        client = get_binance_client()

        if usdt_to_spend < 10:
            logger.warning(f"Insufficient USDT: ${usdt_to_spend}")
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

        logger.info(f"BUY filled: {total_qty:.8f} BTC for ${total_cost:.2f}")
        return {
            "btc_qty": total_qty,
            "fill_price": round(avg_price, 2),
            "cost_usdt": round(total_cost, 2),
        }

    except Exception as e:
        logger.error(f"BUY order failed: {e}", exc_info=True)
        notifier.notify_error(f"BUY failed: {type(e).__name__}: {str(e)[:200]}")
        return None


def _execute_sell(balance: dict, current_price: float, open_positions: list[dict]) -> Optional[dict]:
    """Execute a market SELL order. Only sells bot-owned BTC."""
    try:
        client = get_binance_client()
        btc_to_sell = balance.get("btc_bot", 0)

        if btc_to_sell <= 0:
            logger.warning("No bot BTC to sell")
            notifier.notify_error("SELL failed: btc_bot = 0 (no positions)")
            return None

        actual_btc = balance.get("btc", 0)
        if btc_to_sell > actual_btc:
            logger.warning(f"Bot owns {btc_to_sell:.8f} but only {actual_btc:.8f} available")
            btc_to_sell = actual_btc

        if btc_to_sell <= 0:
            notifier.notify_error(f"SELL failed: no BTC available (bot={balance.get('btc_bot', 0):.8f}, actual={actual_btc:.8f})")
            return None

        # Format quantity
        info = client.get_symbol_info(config.SYMBOL)
        for f in info["filters"]:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                precision = len(str(step_size).rstrip("0").split(".")[-1])
                btc_to_sell = round(btc_to_sell, precision)
                break

        logger.info(f"Attempting SELL: {btc_to_sell:.8f} BTC")

        order = client.create_order(
            symbol=config.SYMBOL,
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=btc_to_sell,
        )

        fills = order.get("fills", [])
        total_qty = sum(float(f["qty"]) for f in fills)
        total_received = sum(float(f["qty"]) * float(f["price"]) for f in fills)
        avg_price = total_received / total_qty if total_qty > 0 else current_price

        logger.info(f"SELL filled: {total_qty:.8f} BTC for ${total_received:.2f}")
        return {
            "btc_qty": total_qty,
            "fill_price": round(avg_price, 2),
            "received_usdt": round(total_received, 2),
        }

    except Exception as e:
        logger.error(f"SELL order failed: {e}", exc_info=True)
        notifier.notify_error(
            f"SELL failed on Binance:\n"
            f"<code>{type(e).__name__}: {str(e)[:300]}</code>\n"
            f"btc_to_sell={btc_to_sell:.8f}, btc_bot={balance.get('btc_bot', 0):.8f}"
        )
        return None


def _close_open_positions(open_positions: list[dict], exit_price: float) -> None:
    """Close all open BUY positions in Supabase."""
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
        logger.info(f"Closed position {pos['id']}: PnL ${pnl_usdt:.2f} ({pnl_percent:.2f}%)")
