"""Risk management — position sizing, exposure limits, circuit breakers."""

import logging
from datetime import datetime, timedelta

from src.data_client import DataService

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, data_service: DataService):
        self.ds = data_service
        self.cfg = data_service.settings["risk"]
        self.schedule_cfg = data_service.settings["schedule"]

        # Circuit breaker state (reset daily/weekly)
        self._daily_pnl = 0.0
        self._weekly_pnl = 0.0
        self._daily_halt = False
        self._weekly_halt = False
        self._last_daily_reset: datetime | None = None
        self._last_weekly_reset: datetime | None = None

    # ── Position Sizing ────────────────────────────────────────

    def calculate_position_size(self, symbol: str, entry_price: float,
                                stop_price: float) -> dict:
        """Size position based on per-trade risk and max position limits."""
        account = self.ds.alpaca.get_account()
        portfolio_value = account["portfolio_value"]
        cash = account["cash"]

        # Max position by portfolio %
        max_position_value = portfolio_value * self.cfg["max_position_pct"]

        # Max position by per-trade risk
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share <= 0:
            return {"approved": False, "reason": "Invalid stop price — no risk per share"}

        max_risk_dollars = portfolio_value * self.cfg["max_per_trade_risk_pct"]
        risk_based_shares = int(max_risk_dollars / risk_per_share)

        # Max shares by position size limit
        position_based_shares = int(max_position_value / entry_price)

        # Take the smaller of the two
        shares = min(risk_based_shares, position_based_shares)

        # Check cash reserve
        min_cash = portfolio_value * self.cfg["min_cash_reserve_pct"]
        available_cash = cash - min_cash
        if available_cash <= 0:
            return {"approved": False, "reason": "Insufficient cash after reserve"}

        cash_based_shares = int(available_cash / entry_price)
        shares = min(shares, cash_based_shares)

        if shares <= 0:
            return {"approved": False, "reason": "Position size too small"}

        return {
            "approved": True,
            "shares": shares,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "position_value": round(shares * entry_price, 2),
            "risk_dollars": round(shares * risk_per_share, 2),
            "risk_pct": round((shares * risk_per_share) / portfolio_value * 100, 2),
        }

    # ── Exposure Checks ────────────────────────────────────────

    def check_exposure(self) -> dict:
        """Check current portfolio exposure against limits."""
        positions = self.ds.alpaca.get_positions()
        account = self.ds.alpaca.get_account()
        portfolio_value = account["portfolio_value"]

        open_count = len(positions)
        can_open_new = open_count < self.cfg["max_open_positions"]

        # Check if any single position exceeds max
        oversized = []
        for p in positions:
            position_value = p["qty"] * p["current_price"]
            pct = position_value / portfolio_value if portfolio_value > 0 else 0
            if pct > self.cfg["max_position_pct"]:
                oversized.append({"symbol": p["symbol"], "pct": round(pct * 100, 1)})

        return {
            "open_positions": open_count,
            "max_positions": self.cfg["max_open_positions"],
            "can_open_new": can_open_new,
            "oversized_positions": oversized,
            "portfolio_value": portfolio_value,
            "cash": account["cash"],
        }

    # ── Circuit Breakers ───────────────────────────────────────

    def update_pnl(self, realized_pnl: float):
        """Track realized P&L for daily/weekly circuit breakers."""
        self._daily_pnl += realized_pnl
        self._weekly_pnl += realized_pnl

    def reset_daily(self):
        self._daily_pnl = 0.0
        self._daily_halt = False
        self._last_daily_reset = datetime.now()

    def reset_weekly(self):
        self._weekly_pnl = 0.0
        self._weekly_halt = False
        self._last_weekly_reset = datetime.now()

    def check_circuit_breakers(self) -> dict:
        """Check all circuit breakers. Returns halt status and reasons."""
        account = self.ds.alpaca.get_account()
        portfolio_value = account["portfolio_value"]

        reasons = []

        # Daily loss check
        if portfolio_value > 0:
            daily_loss_pct = abs(self._daily_pnl) / portfolio_value if self._daily_pnl < 0 else 0
            if daily_loss_pct >= self.cfg["max_daily_loss_pct"]:
                self._daily_halt = True
                reasons.append(f"Daily loss {daily_loss_pct:.1%} >= {self.cfg['max_daily_loss_pct']:.0%} limit")

            weekly_loss_pct = abs(self._weekly_pnl) / portfolio_value if self._weekly_pnl < 0 else 0
            if weekly_loss_pct >= self.cfg["max_weekly_loss_pct"]:
                self._weekly_halt = True
                reasons.append(f"Weekly loss {weekly_loss_pct:.1%} >= {self.cfg['max_weekly_loss_pct']:.0%} limit")
        else:
            daily_loss_pct = 0
            weekly_loss_pct = 0

        # LunarCrush API check
        lc_available = self.ds.lunarcrush.is_available()
        if not lc_available:
            reasons.append("LunarCrush API unavailable")

        # Trading hours check
        in_trading_window = self._in_trading_window()
        if not in_trading_window:
            reasons.append("Outside trading window")

        halted = self._daily_halt or self._weekly_halt or not lc_available or not in_trading_window

        return {
            "halted": halted,
            "daily_halt": self._daily_halt,
            "weekly_halt": self._weekly_halt,
            "lc_available": lc_available,
            "in_trading_window": in_trading_window,
            "daily_pnl": round(self._daily_pnl, 2),
            "weekly_pnl": round(self._weekly_pnl, 2),
            "daily_loss_pct": round(daily_loss_pct * 100, 2),
            "weekly_loss_pct": round(weekly_loss_pct * 100, 2),
            "reasons": reasons,
        }

    def _in_trading_window(self) -> bool:
        """Check if current time is within allowed trading hours."""
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(self.schedule_cfg["timezone"])
        now = datetime.now(tz)

        # Weekends
        if now.weekday() >= 5:
            return False

        open_time = datetime.strptime(self.schedule_cfg["market_open"], "%H:%M").time()
        close_time = datetime.strptime(self.schedule_cfg["market_close"], "%H:%M").time()
        current_time = now.time()

        return open_time <= current_time <= close_time

    # ── Pre-Trade Approval ─────────────────────────────────────

    def approve_trade(self, symbol: str, entry_price: float,
                      stop_price: float) -> dict:
        """Full pre-trade risk check. Returns approval or rejection with reasons."""
        # Check circuit breakers first
        cb = self.check_circuit_breakers()
        if cb["halted"]:
            return {
                "approved": False,
                "symbol": symbol,
                "reason": f"Trading halted: {'; '.join(cb['reasons'])}",
                "circuit_breakers": cb,
            }

        # Check exposure
        exposure = self.check_exposure()
        if not exposure["can_open_new"]:
            return {
                "approved": False,
                "symbol": symbol,
                "reason": f"Max positions reached ({exposure['open_positions']}/{exposure['max_positions']})",
                "exposure": exposure,
            }

        # Check if already holding this symbol
        positions = self.ds.alpaca.get_positions()
        for p in positions:
            if p["symbol"] == symbol:
                return {
                    "approved": False,
                    "symbol": symbol,
                    "reason": f"Already holding {symbol} ({p['qty']} shares)",
                }

        # Calculate position size
        sizing = self.calculate_position_size(symbol, entry_price, stop_price)
        if not sizing["approved"]:
            return {
                "approved": False,
                "symbol": symbol,
                "reason": sizing["reason"],
            }

        return {
            "approved": True,
            "symbol": symbol,
            "sizing": sizing,
            "exposure": exposure,
            "circuit_breakers": cb,
        }
