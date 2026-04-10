"""Trade executor — Alpaca limit orders, stop management, exit logic."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass
from alpaca.trading.requests import LimitOrderRequest, StopLossRequest

from src.data_client import DataService

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, data_service: DataService):
        self.ds = data_service
        self.exit_cfg = data_service.settings["strategy"]["exits"]
        # Track stop orders by symbol → order_id
        self._stop_orders: dict[str, str] = {}
        # Track high water marks for trailing stops
        self._high_water: dict[str, float] = {}
        # Track entry times for time-based exits
        self._entry_times: dict[str, datetime] = {}
        # Track entry prices
        self._entry_prices: dict[str, float] = {}
        # Track whether half was sold at profit target
        self._half_sold: dict[str, bool] = {}

    # ── Entry ──────────────────────────────────────────────────

    def execute_buy(self, signal: dict) -> dict:
        """Execute a buy signal: limit order + stop-loss."""
        symbol = signal["symbol"]
        shares = signal["shares"]
        entry_price = signal["entry_price"]
        stop_price = signal["stop_price"]

        try:
            # Use bracket order (OTO) — stop-loss only activates after buy fills
            bracket_request = LimitOrderRequest(
                symbol=symbol,
                qty=shares,
                side=OrderSide.BUY,
                type="limit",
                time_in_force=TimeInForce.DAY,
                limit_price=round(entry_price, 2),
                order_class=OrderClass.OTO,
                stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
            )
            order = self.ds.alpaca.trading.submit_order(bracket_request)
            order_id = str(order.id)

            # Track state
            self._stop_orders[symbol] = "bracket"  # managed by Alpaca
            self._high_water[symbol] = entry_price
            self._entry_times[symbol] = datetime.now()
            self._entry_prices[symbol] = entry_price
            self._half_sold[symbol] = False

            log.info(f"EXECUTED BUY: {shares} {symbol} @ {entry_price:.2f}, stop @ {stop_price:.2f} (bracket)")

            return {
                "success": True,
                "symbol": symbol,
                "action": "BUY",
                "shares": shares,
                "entry_price": entry_price,
                "stop_price": stop_price,
                "order_id": order_id,
                "stop_order_id": "bracket",
            }

        except Exception as e:
            log.error(f"BUY failed for {symbol}: {e}")
            return {
                "success": False,
                "symbol": symbol,
                "action": "BUY",
                "error": str(e),
            }

    # ── Exit Logic ─────────────────────────────────────────────

    def check_exits(self) -> list[dict]:
        """Check all open positions for exit conditions. Returns list of exit actions taken."""
        positions = self.ds.alpaca.get_positions()
        actions = []

        for pos in positions:
            symbol = pos["symbol"]
            current_price = pos["current_price"]
            qty = pos["qty"]
            entry_price = self._entry_prices.get(symbol, pos["avg_entry"])

            # Update high water mark
            if symbol in self._high_water:
                if current_price > self._high_water[symbol]:
                    self._high_water[symbol] = current_price
            else:
                self._high_water[symbol] = current_price

            action = self._evaluate_exit(symbol, current_price, entry_price, qty)
            if action:
                actions.append(action)

        return actions

    def _evaluate_exit(self, symbol: str, current_price: float,
                       entry_price: float, qty: float) -> Optional[dict]:
        """Evaluate exit conditions in priority order."""
        high = self._high_water.get(symbol, current_price)
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        # 1. Trailing stop: -3% from high water mark
        trailing_drop = (high - current_price) / high if high > 0 else 0
        if trailing_drop >= self.exit_cfg["trailing_stop_pct"]:
            return self._execute_sell(symbol, qty, "trailing_stop",
                                      f"Trailing stop hit: {trailing_drop:.1%} from high {high:.2f}")

        # 2. Profit target: +10% → sell half, trail rest
        if pnl_pct >= self.exit_cfg["profit_target_pct"] and not self._half_sold.get(symbol, False):
            half_qty = max(1, int(qty / 2))
            self._half_sold[symbol] = True
            return self._execute_sell(symbol, half_qty, "profit_target",
                                      f"Profit target {pnl_pct:.1%} — selling half ({half_qty} shares)")

        # 3. Sentiment reversal
        if self.exit_cfg.get("sentiment_reversal_exit", True):
            try:
                social = self.ds.lunarcrush.get_stock_social(symbol)
                sentiment_val = social.get("sentiment", 3)
                if sentiment_val <= 2:
                    return self._execute_sell(symbol, qty, "sentiment_reversal",
                                              f"Sentiment bearish ({sentiment_val}/5)")
            except Exception:
                pass  # Don't exit on LC error — rely on other rules

        # 4. Time exit: > 5 days → re-evaluate
        entry_time = self._entry_times.get(symbol)
        if entry_time:
            days_held = (datetime.now() - entry_time).days
            if days_held >= self.exit_cfg["max_hold_days"]:
                # Only exit if not still bullish
                try:
                    social = self.ds.lunarcrush.get_stock_social(symbol)
                    if social.get("sentiment", 0) < 4:
                        return self._execute_sell(symbol, qty, "time_exit",
                                                  f"Held {days_held} days, sentiment not bullish")
                except Exception:
                    return self._execute_sell(symbol, qty, "time_exit",
                                              f"Held {days_held} days, cannot confirm sentiment")

        return None

    def _execute_sell(self, symbol: str, qty: float, reason: str,
                      detail: str) -> dict:
        """Execute a sell order and cancel any existing stop."""
        try:
            # Cancel existing stop order
            if symbol in self._stop_orders:
                try:
                    self.ds.alpaca.cancel_order(self._stop_orders[symbol])
                except Exception:
                    pass  # Stop may have already triggered
                del self._stop_orders[symbol]

            # Place sell order
            quote = self.ds.alpaca.get_latest_quote(symbol)
            sell_price = round(quote["bid"], 2)

            order_id = self.ds.alpaca.submit_limit_order(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                limit_price=sell_price,
            )

            # Clean up tracking if full sell
            positions = self.ds.alpaca.get_positions()
            remaining = 0
            for p in positions:
                if p["symbol"] == symbol:
                    remaining = p["qty"]
                    break

            if remaining <= qty:
                self._cleanup_symbol(symbol)

            log.info(f"EXIT {symbol}: {reason} — {detail}")

            return {
                "success": True,
                "symbol": symbol,
                "action": "SELL",
                "shares": qty,
                "price": sell_price,
                "reason": reason,
                "detail": detail,
                "order_id": order_id,
            }

        except Exception as e:
            log.error(f"SELL failed for {symbol}: {e}")
            return {
                "success": False,
                "symbol": symbol,
                "action": "SELL",
                "reason": reason,
                "error": str(e),
            }

    def _cleanup_symbol(self, symbol: str):
        """Remove all tracking state for a closed position."""
        self._high_water.pop(symbol, None)
        self._entry_times.pop(symbol, None)
        self._entry_prices.pop(symbol, None)
        self._half_sold.pop(symbol, None)
        self._stop_orders.pop(symbol, None)

    # ── Stop Management ────────────────────────────────────────

    def update_trailing_stops(self):
        """Update stop orders to trail the high water mark."""
        positions = self.ds.alpaca.get_positions()

        for pos in positions:
            symbol = pos["symbol"]
            current_price = pos["current_price"]

            # Update high water
            if symbol not in self._high_water or current_price > self._high_water[symbol]:
                self._high_water[symbol] = current_price

            high = self._high_water[symbol]
            new_stop = round(high * (1 - self.exit_cfg["trailing_stop_pct"]), 2)
            entry = self._entry_prices.get(symbol, pos["avg_entry"])

            # Only raise stops, never lower
            old_stop = entry * (1 - self.exit_cfg["stop_loss_pct"])
            if new_stop <= old_stop:
                continue

            # Cancel old stop, place new one
            if symbol in self._stop_orders:
                try:
                    self.ds.alpaca.cancel_order(self._stop_orders[symbol])
                except Exception:
                    pass

                stop_limit = round(new_stop * 0.995, 2)
                try:
                    new_id = self.ds.alpaca.submit_stop_order(
                        symbol=symbol,
                        qty=pos["qty"],
                        stop_price=new_stop,
                        limit_price=stop_limit,
                    )
                    self._stop_orders[symbol] = new_id
                    log.info(f"Trailing stop updated: {symbol} → {new_stop:.2f}")
                except Exception as e:
                    log.error(f"Failed to update trailing stop for {symbol}: {e}")
