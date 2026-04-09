"""
Realtime price monitor for the trading bot.
Runs in a separate thread, checking BTC price every 30 seconds.
Handles: auto stop-loss, take-profit, trailing stop, emergency triggers.
This is the fast-reaction layer — no Claude calls, pure rule-based execution.
"""

import logging
import threading
import time
from typing import Optional

import config
from bot import memory, notifier
from bot.market_data import get_binance_client, get_current_price

logger = logging.getLogger(__name__)


class PriceMonitor:
    """
    Monitors BTC price in realtime and executes protective actions.
    Runs as a daemon thread alongside the main scheduler.
    """

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._active_position: Optional[dict] = None
        self._stop_loss_price: float = 0
        self._take_profit_price: float = 0
        self._trailing_stop_price: float = 0
        self._highest_since_entry: float = 0
        self._cooldown_until: float = 0  # timestamp
        self._check_interval: int = config.PRICE_CHECK_INTERVAL

    def start(self):
        """Start the price monitor in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"Price monitor started (checking every {self._check_interval}s)")

    def stop(self):
        """Stop the price monitor."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("Price monitor stopped")

    def update_position(self, entry_price: float, stop_loss: float,
                        take_profit: float, quantity_usdt: float):
        """
        Called after a BUY is executed to set monitoring targets.

        Args:
            entry_price: BTC price at entry.
            stop_loss: Stop loss price.
            take_profit: Take profit price.
            quantity_usdt: Position size in USDT.
        """
        self._active_position = {
            "entry_price": entry_price,
            "quantity_usdt": quantity_usdt,
        }
        self._stop_loss_price = stop_loss
        self._take_profit_price = take_profit
        self._trailing_stop_price = 0
        self._highest_since_entry = entry_price
        logger.info(
            f"Position monitor set: entry=${entry_price:,.2f}, "
            f"SL=${stop_loss:,.2f}, TP=${take_profit:,.2f}"
        )

    def clear_position(self):
        """Called after a SELL to clear monitoring."""
        self._active_position = None
        self._stop_loss_price = 0
        self._take_profit_price = 0
        self._trailing_stop_price = 0
        self._highest_since_entry = 0
        logger.info("Position monitor cleared")

    def set_cooldown(self, seconds: int):
        """Set a trade cooldown period."""
        self._cooldown_until = time.time() + seconds
        logger.info(f"Trade cooldown set for {seconds}s")

    def is_in_cooldown(self) -> bool:
        """Check if we're in cooldown period."""
        return time.time() < self._cooldown_until

    def has_active_position(self) -> bool:
        """Check if there's an active monitored position."""
        return self._active_position is not None

    def _run_loop(self):
        """Main monitoring loop — runs every check_interval seconds."""
        while self._running:
            try:
                if self._active_position:
                    self._check_price()
            except Exception as e:
                logger.error(f"Price monitor error: {e}")
            time.sleep(self._check_interval)

    def _check_price(self):
        """Check current price against stop-loss, take-profit, and trailing stop."""
        price = get_current_price()
        if price is None:
            return

        entry = self._active_position["entry_price"]
        pnl_pct = (price - entry) / entry * 100

        # Update highest price since entry (for trailing stop)
        if price > self._highest_since_entry:
            self._highest_since_entry = price
            # Update trailing stop: activate when profit > trailing_activation_pct
            if pnl_pct >= config.TRAILING_STOP_ACTIVATION_PCT:
                self._trailing_stop_price = price * (1 - config.TRAILING_STOP_DISTANCE_PCT / 100)
                logger.debug(
                    f"Trailing stop updated: ${self._trailing_stop_price:,.2f} "
                    f"(price=${price:,.2f}, highest=${self._highest_since_entry:,.2f})"
                )

        # Check STOP LOSS
        if self._stop_loss_price > 0 and price <= self._stop_loss_price:
            logger.warning(f"STOP LOSS triggered: price=${price:,.2f} <= SL=${self._stop_loss_price:,.2f}")
            self._execute_emergency_sell(price, "STOP_LOSS", pnl_pct)
            return

        # Check TAKE PROFIT
        if self._take_profit_price > 0 and price >= self._take_profit_price:
            logger.info(f"TAKE PROFIT triggered: price=${price:,.2f} >= TP=${self._take_profit_price:,.2f}")
            self._execute_emergency_sell(price, "TAKE_PROFIT", pnl_pct)
            return

        # Check TRAILING STOP
        if self._trailing_stop_price > 0 and price <= self._trailing_stop_price:
            logger.info(
                f"TRAILING STOP triggered: price=${price:,.2f} <= trail=${self._trailing_stop_price:,.2f} "
                f"(peak=${self._highest_since_entry:,.2f})"
            )
            self._execute_emergency_sell(price, "TRAILING_STOP", pnl_pct)
            return

        # Check EMERGENCY: rapid drop > emergency_drop_pct since entry
        if pnl_pct <= -config.EMERGENCY_DROP_PCT:
            logger.warning(f"EMERGENCY DROP: {pnl_pct:.2f}% from entry")
            self._execute_emergency_sell(price, "EMERGENCY_DROP", pnl_pct)
            return

    def _execute_emergency_sell(self, current_price: float, trigger: str, pnl_pct: float):
        """Execute an emergency sell order."""
        from bot.executor import get_bot_btc_quantity

        try:
            client = get_binance_client()
            btc_to_sell = get_bot_btc_quantity()

            if btc_to_sell <= 0:
                logger.warning("No bot BTC to sell in emergency")
                self.clear_position()
                return

            # Get actual Binance balance
            account = client.get_account()
            actual_btc = 0
            for b in account["balances"]:
                if b["asset"] == "BTC":
                    actual_btc = float(b["free"])
                    break

            btc_to_sell = min(btc_to_sell, actual_btc)
            if btc_to_sell <= 0:
                self.clear_position()
                return

            # Format quantity properly
            info = client.get_symbol_info(config.SYMBOL)
            for f in info["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step_size = float(f["stepSize"])
                    precision = len(str(step_size).rstrip("0").split(".")[-1])
                    btc_to_sell = round(btc_to_sell, precision)
                    break

            if config.DRY_RUN:
                logger.info(f"[DRY RUN] Would {trigger} sell {btc_to_sell:.8f} BTC @ ${current_price:,.2f}")
            else:
                from binance.enums import SIDE_SELL, ORDER_TYPE_MARKET
                order = client.create_order(
                    symbol=config.SYMBOL,
                    side=SIDE_SELL,
                    type=ORDER_TYPE_MARKET,
                    quantity=btc_to_sell,
                )
                fills = order.get("fills", [])
                total_received = sum(float(f["qty"]) * float(f["price"]) for f in fills)
                logger.info(f"{trigger} SELL executed: received ${total_received:.2f}")

            # Record the trade
            entry = self._active_position["entry_price"]
            qty_usdt = self._active_position["quantity_usdt"]
            pnl_usdt = qty_usdt * (pnl_pct / 100)

            trade_record = {
                "action": "SELL",
                "symbol": config.SYMBOL,
                "price_at_decision": current_price,
                "quantity_usdt": qty_usdt,
                "reasoning": f"[{trigger}] Auto-sell at ${current_price:,.2f} (entry: ${entry:,.2f}, PnL: {pnl_pct:+.2f}%)",
                "confidence": 10,
                "suggested_stop_loss": 0,
                "status": "closed",
                "pnl_usdt": round(pnl_usdt, 2),
                "pnl_percent": round(pnl_pct, 2),
            }
            memory.save_trade(trade_record)

            # Close open positions in Supabase
            open_trades = memory.get_open_trades()
            for pos in open_trades:
                if pos.get("action") == "BUY":
                    memory.update_trade(pos["id"], {
                        "status": "closed",
                        "exit_price": current_price,
                        "pnl_usdt": round(pnl_usdt, 2),
                        "pnl_percent": round(pnl_pct, 2),
                    })

            # Notify via Telegram
            emoji = "🔴" if pnl_pct < 0 else "🟢"
            notifier.send_message(
                f"⚡ <b>AUTO {trigger}</b> {emoji}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Price: ${current_price:,.2f}\n"
                f"Entry: ${entry:,.2f}\n"
                f"PnL: {pnl_pct:+.2f}% (${pnl_usdt:+.2f})\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Trigger: {trigger.replace('_', ' ')}"
            )

            self.clear_position()
            self.set_cooldown(config.TRADE_COOLDOWN_SECONDS)

        except Exception as e:
            logger.error(f"Failed to execute emergency sell: {e}", exc_info=True)
            notifier.notify_error(f"Emergency {trigger} SELL failed: {e}")


# Global singleton
price_monitor = PriceMonitor()
