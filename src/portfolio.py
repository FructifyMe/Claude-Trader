"""Portfolio tracking — positions, P&L, performance metrics."""

import logging
from datetime import datetime

from src.data_client import DataService

log = logging.getLogger(__name__)


class Portfolio:
    def __init__(self, data_service: DataService):
        self.ds = data_service
        # Closed trades for metrics calculation
        self._closed_trades: list[dict] = []

    def get_snapshot(self) -> dict:
        """Current portfolio state."""
        account = self.ds.alpaca.get_account()
        positions = self.ds.alpaca.get_positions()

        total_unrealized = sum(p["unrealized_pl"] for p in positions)

        return {
            "timestamp": datetime.now().isoformat(),
            "equity": account["equity"],
            "cash": account["cash"],
            "portfolio_value": account["portfolio_value"],
            "buying_power": account["buying_power"],
            "open_positions": len(positions),
            "positions": positions,
            "total_unrealized_pl": round(total_unrealized, 2),
        }

    def record_closed_trade(self, trade: dict):
        """Record a completed trade for metrics."""
        self._closed_trades.append({
            **trade,
            "closed_at": datetime.now().isoformat(),
        })

    def get_performance_metrics(self) -> dict:
        """Calculate performance from closed trades."""
        if not self._closed_trades:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "avg_gain": 0,
                "avg_loss": 0,
                "reward_risk_ratio": 0,
                "total_pnl": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
            }

        wins = [t for t in self._closed_trades if t.get("pnl", 0) > 0]
        losses = [t for t in self._closed_trades if t.get("pnl", 0) <= 0]
        total = len(self._closed_trades)

        win_rate = len(wins) / total if total > 0 else 0
        avg_gain = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        rr_ratio = abs(avg_gain / avg_loss) if avg_loss != 0 else 0

        pnls = [t.get("pnl", 0) for t in self._closed_trades]
        total_pnl = sum(pnls)

        # Sharpe ratio (simplified — daily returns assumed)
        if len(pnls) > 1:
            import numpy as np
            returns = np.array(pnls)
            sharpe = (returns.mean() / returns.std()) * (252 ** 0.5) if returns.std() > 0 else 0
        else:
            sharpe = 0

        # Max drawdown from cumulative P&L
        max_dd = self._calc_max_drawdown(pnls)

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate * 100, 1),
            "avg_gain": round(avg_gain, 2),
            "avg_loss": round(avg_loss, 2),
            "reward_risk_ratio": round(rr_ratio, 2),
            "total_pnl": round(total_pnl, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd * 100, 2),
        }

    def _calc_max_drawdown(self, pnls: list[float]) -> float:
        """Max drawdown as fraction from peak equity."""
        if not pnls:
            return 0

        cumulative = 0
        peak = 0
        max_dd = 0

        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def get_daily_summary(self) -> dict:
        """Summary for today's trading activity."""
        snapshot = self.get_snapshot()
        metrics = self.get_performance_metrics()

        today = datetime.now().date().isoformat()
        todays_trades = [
            t for t in self._closed_trades
            if t.get("closed_at", "").startswith(today)
        ]

        return {
            "date": today,
            "portfolio": snapshot,
            "metrics": metrics,
            "trades_today": len(todays_trades),
            "pnl_today": round(sum(t.get("pnl", 0) for t in todays_trades), 2),
        }
